from __future__ import annotations
import datetime
import logging
import os
import json
from zoneinfo import ZoneInfo
import httpx

import anthropic

from tenacity import retry, stop_after_attempt, wait_fixed, retry_if_exception_type

from utils import is_direct_result
from plugin_manager import PluginManager

# Claude models and their context windows
CLAUDE_MODELS = {
    "claude-opus-4-6": 200000,
    "claude-opus-4-5-20251124": 200000,
    "claude-sonnet-4-5-20250929": 200000,
    "claude-haiku-4-5-20251001": 200000,
}

DEFAULT_MODEL = "claude-sonnet-4-5-20250929"


def default_max_tokens(model: str) -> int:
    """
    Gets the default number of max tokens for the given model.
    :param model: The model name
    :return: The default number of max tokens
    """
    return 4096


def are_functions_available(model: str) -> bool:
    """
    Whether the given model supports tool use (all Claude models do).
    """
    return True


# Load translations
parent_dir_path = os.path.join(os.path.dirname(__file__), os.pardir)
translations_file_path = os.path.join(parent_dir_path, 'translations.json')
with open(translations_file_path, 'r', encoding='utf-8') as f:
    translations = json.load(f)


def localized_text(key, bot_language):
    """
    Return translated text for a key in specified bot_language.
    Keys and translations can be found in the translations.json.
    """
    try:
        return translations[bot_language][key]
    except KeyError:
        logging.warning(f"No translation available for bot_language code '{bot_language}' and key '{key}'")
        if key in translations['en']:
            return translations['en'][key]
        else:
            logging.warning(f"No english definition found for key '{key}' in translations.json")
            return key


class ClaudeHelper:
    """
    Claude API helper class.
    """

    def __init__(self, config: dict, plugin_manager: PluginManager, model_router=None, group_memory=None):
        """
        Initializes the Claude helper class with the given configuration.
        :param config: A dictionary containing the Claude configuration
        :param plugin_manager: The plugin manager
        :param model_router: Optional ModelRouter for smart model selection
        :param group_memory: Optional GroupMemory for group-scoped personas and facts
        """
        http_client = httpx.AsyncClient(proxy=config['proxy']) if config.get('proxy') else None
        self.client = anthropic.AsyncAnthropic(api_key=config['api_key'], http_client=http_client)
        self.config = config
        self.plugin_manager = plugin_manager
        self.model_router = model_router
        self.group_memory = group_memory
        self.conversations: dict[int: list] = {}  # {chat_id: history}
        self.last_updated: dict[int: datetime] = {}  # {chat_id: last_update_timestamp}
        self.model_overrides: dict[int: str] = {}  # {chat_id: model_id} per-chat overrides
        self._last_routing_info: dict[int: dict] = {}  # {chat_id: routing metadata}

    def _build_system_prompt(self, chat_id: int = None) -> str:
        """Builds the full system prompt, including memory tool instructions and optional group persona."""
        # Use group persona if set, otherwise use default assistant prompt
        base = self.config['assistant_prompt']
        if chat_id and self.group_memory:
            persona = self.group_memory.get_persona(chat_id)
            if persona:
                base = persona

        memory_supplement = (
            "\n\nYou have persistent memory tools. USE THEM PROACTIVELY — do not wait to be asked."
            "\n- When a user introduces themselves or states their name, call remember_user_name immediately."
            "\n- When a user shares significant personal details (job, hobbies, preferences, skills, "
            "location, life events), call remember_user_fact to store a concise summary."
            "\n- Do NOT store trivial or transient things (greetings, one-off questions, temporary moods)."
            "\n- Memory context about the user may appear in brackets before their message — check it "
            "to avoid storing duplicates. If stored info is outdated or contradicted, call forget_user_fact "
            "to remove the old fact before storing the corrected one."
            "\n- In group chats, messages are prefixed with the sender's name (e.g. 'Dave: message')."
            "\n- The user_id is provided automatically — just supply the name or fact."
            "\n- In group chats, you also have group memory tools (remember_group_fact, forget_group_fact) "
            "for storing shared group decisions, project context, and recurring topics."
            "\n- You also have scheduling tools (set_reminder, set_recurring_schedule, list_reminders, "
            "cancel_reminder) to set, view, and manage reminders and recurring messages for users."
            "\n- Each user message is prefixed with a timestamp in [YYYY-MM-DD HH:MM TZ] format. "
            "Use this to know the current date and time, and to gauge time gaps between messages."
        )
        return base + memory_supplement

    def set_model_override(self, chat_id: int, model: str):
        """Set a per-chat model override."""
        self.model_overrides[chat_id] = model

    def clear_model_override(self, chat_id: int):
        """Clear per-chat model override, returning to auto-routing or default."""
        self.model_overrides.pop(chat_id, None)

    def get_model_override(self, chat_id: int) -> str | None:
        """Get the current model override for a chat, or None."""
        return self.model_overrides.get(chat_id)

    def _format_routing_footer(self, routing_info: dict, input_tokens: int, output_tokens: int) -> str:
        """Format the smart routing footer with model info and savings."""
        emoji = routing_info['emoji']
        label = routing_info['label']
        model = routing_info['model']

        savings_text = ""
        if self.model_router:
            savings = self.model_router.calculate_savings(model, input_tokens, output_tokens)
            if savings is not None and savings > 0:
                savings_text = f" — saved ~${savings:.4f} vs Opus"

        return f"{emoji} {label}{savings_text}"

    def get_conversation_stats(self, chat_id: int) -> tuple[int, int]:
        """
        Gets the number of messages and tokens used in the conversation.
        :param chat_id: The chat ID
        :return: A tuple containing the number of messages and tokens used
        """
        if chat_id not in self.conversations:
            self.reset_chat_history(chat_id)
        return len(self.conversations[chat_id]), self.__count_tokens(self.conversations[chat_id])

    async def get_chat_response(self, chat_id: int, query: str, user_id: int = None, group_context: str = None) -> tuple[str, str]:
        """
        Gets a full response from the Claude model.
        :param chat_id: The chat ID
        :param query: The query to send to the model
        :param user_id: The Telegram user ID of the sender (for memory tools)
        :param group_context: Optional recent group chat messages for context on fresh conversations
        :return: The answer from the model and the number of tokens used
        """
        plugins_used = ()
        response = await self.__common_get_chat_response(chat_id, query, group_context=group_context)
        if self.config['enable_functions']:
            response, plugins_used = await self.__handle_tool_call(chat_id, response, user_id=user_id, chat_id_for_plugins=chat_id)
            if is_direct_result(response):
                return response, '0'

        answer = ''
        for block in response.content:
            if block.type == 'text':
                answer += block.text

        answer = answer.strip()
        self.__add_to_history(chat_id, role="assistant", content=answer)

        bot_language = self.config['bot_language']
        show_plugins_used = len(plugins_used) > 0 and self.config['show_plugins_used']
        plugin_names = tuple(self.plugin_manager.get_plugin_source_name(plugin) for plugin in plugins_used)
        total_tokens = response.usage.input_tokens + response.usage.output_tokens

        footer_parts = []
        if self.config['show_usage']:
            footer_parts.append(
                f"💰 {str(total_tokens)} {localized_text('stats_tokens', bot_language)}"
                f" ({str(response.usage.input_tokens)} {localized_text('prompt', bot_language)},"
                f" {str(response.usage.output_tokens)} {localized_text('completion', bot_language)})"
            )

        # Smart routing footer
        routing_info = self._last_routing_info.get(chat_id)
        if routing_info and self.config.get('show_routing_info', True):
            footer_parts.append(self._format_routing_footer(routing_info, response.usage.input_tokens, response.usage.output_tokens))

        if show_plugins_used:
            footer_parts.append(f"🔌 {', '.join(plugin_names)}")

        if footer_parts:
            answer += "\n\n---\n" + "\n".join(footer_parts)

        return answer, total_tokens

    async def get_chat_response_stream(self, chat_id: int, query: str, user_id: int = None, group_context: str = None):
        """
        Stream response from the Claude model.
        :param chat_id: The chat ID
        :param query: The query to send to the model
        :param user_id: The Telegram user ID of the sender (for memory tools)
        :param group_context: Optional recent group chat messages for context on fresh conversations
        :return: The answer from the model and the number of tokens used, or 'not_finished'
        """
        plugins_used = ()

        has_tools = False
        if self.config['enable_functions']:
            has_tools = len(self.plugin_manager.get_functions_specs()) > 0

        if has_tools:
            # Tool calls require non-streaming: get full response, handle tools, yield result
            logging.info('Streaming fallback: ENABLE_FUNCTIONS is true and tools are configured, using non-streaming response path.')
            response = await self.__common_get_chat_response(chat_id, query, group_context=group_context)
            response, plugins_used = await self.__handle_tool_call(chat_id, response, user_id=user_id, chat_id_for_plugins=chat_id)
            if is_direct_result(response):
                yield response, '0'
                return

            answer = ''
            for block in response.content:
                if block.type == 'text':
                    answer += block.text
            answer = answer.strip()
            self.__add_to_history(chat_id, role="assistant", content=answer)
            tokens_used = str(response.usage.input_tokens + response.usage.output_tokens)

            show_plugins_used = len(plugins_used) > 0 and self.config['show_plugins_used']
            plugin_names = tuple(self.plugin_manager.get_plugin_source_name(plugin) for plugin in plugins_used)

            footer_parts = []
            if self.config['show_usage']:
                footer_parts.append(f"💰 {tokens_used} {localized_text('stats_tokens', self.config['bot_language'])}")

            routing_info = self._last_routing_info.get(chat_id)
            if routing_info and self.config.get('show_routing_info', True):
                input_tokens = response.usage.input_tokens
                output_tokens = response.usage.output_tokens
                footer_parts.append(self._format_routing_footer(routing_info, input_tokens, output_tokens))

            if show_plugins_used:
                footer_parts.append(f"🔌 {', '.join(plugin_names)}")

            if footer_parts:
                answer += "\n\n---\n" + "\n".join(footer_parts)

            yield answer, tokens_used
            return

        # Pure streaming path (no tool calls)
        answer = ''
        async for chunk in self.__common_get_chat_response_stream(chat_id, query, group_context=group_context):
            if hasattr(chunk, 'type') and chunk.type == 'content_block_delta':
                if hasattr(chunk.delta, 'text'):
                    answer += chunk.delta.text
                    yield answer, 'not_finished'

        answer = answer.strip()
        self.__add_to_history(chat_id, role="assistant", content=answer)
        tokens_used = str(self.__count_tokens(self.conversations[chat_id]))

        if self.config['show_usage']:
            answer += f"\n\n---\n💰 {tokens_used} {localized_text('stats_tokens', self.config['bot_language'])}"

        yield answer, tokens_used

    async def __prepare_chat(self, chat_id: int, query: str, group_context: str = None):
        """
        Prepare conversation history for a chat request (shared logic).
        Returns the common_args dict for the API call.
        """
        is_new_conversation = chat_id not in self.conversations or self.__max_age_reached(chat_id)
        if is_new_conversation:
            self.reset_chat_history(chat_id)

        self.last_updated[chat_id] = datetime.datetime.now()

        # On fresh conversations, prepend rolling group chat context so Claude
        # has awareness of what was being discussed before it was called upon.
        if is_new_conversation and group_context:
            query = f"{group_context}\n\n{query}"

        self.__add_to_history(chat_id, role="user", content=query)

        # Summarize the chat history if it's too long to avoid excessive token usage
        token_count = self.__count_tokens(self.conversations[chat_id])
        exceeded_max_tokens = token_count + self.config['max_tokens'] > self.__max_model_tokens()
        exceeded_max_history_size = len(self.conversations[chat_id]) > self.config['max_history_size']

        if exceeded_max_tokens or exceeded_max_history_size:
            logging.info(f'Chat history for chat ID {chat_id} is too long. Summarising...')
            try:
                summary = await self.__summarise(self.conversations[chat_id][:-1])
                logging.debug(f'Summary: {summary}')
                self.reset_chat_history(chat_id)
                self.__add_to_history(chat_id, role="assistant", content=summary)
                self.__add_to_history(chat_id, role="user", content=query)
            except Exception as e:
                logging.warning(f'Error while summarising chat history: {str(e)}. Popping elements instead...')
                self.conversations[chat_id] = self.conversations[chat_id][-self.config['max_history_size']:]

        # Determine model: per-chat override > smart routing > config default
        model = self.config['model']
        self._last_routing_info.pop(chat_id, None)

        if chat_id in self.model_overrides:
            model = self.model_overrides[chat_id]
        elif self.model_router and self.config.get('enable_smart_routing'):
            conversation_length = len(self.conversations[chat_id])
            routed_model, label, emoji = self.model_router.route(query, conversation_length)
            model = routed_model
            self._last_routing_info[chat_id] = {
                'model': routed_model, 'label': label, 'emoji': emoji
            }
            logging.info(f'Smart routing selected {label} ({routed_model}) for chat {chat_id}')

        # Inject group memory context into query if available
        if self.group_memory:
            group_ctx = self.group_memory.get_context_string(chat_id)
            if group_ctx:
                # Prepend group context to the last user message
                last_msg = self.conversations[chat_id][-1]
                if last_msg['role'] == 'user' and isinstance(last_msg['content'], str):
                    self.conversations[chat_id][-1] = {
                        'role': 'user',
                        'content': f"{group_ctx}\n{last_msg['content']}"
                    }

        common_args = {
            'model': model,
            'messages': self.conversations[chat_id],
            'system': self._build_system_prompt(chat_id=chat_id),
            'temperature': self.config['temperature'],
            'max_tokens': self.config['max_tokens'],
        }

        if self.config['enable_functions']:
            tools = self.plugin_manager.get_functions_specs()
            if len(tools) > 0:
                common_args['tools'] = tools

        return common_args

    @retry(
        reraise=True,
        retry=retry_if_exception_type(anthropic.RateLimitError),
        wait=wait_fixed(20),
        stop=stop_after_attempt(3)
    )
    async def __common_get_chat_response(self, chat_id: int, query: str, group_context: str = None):
        """
        Request a non-streaming response from the Claude model.
        """
        bot_language = self.config['bot_language']
        try:
            common_args = await self.__prepare_chat(chat_id, query, group_context=group_context)
            return await self.client.messages.create(**common_args)

        except anthropic.RateLimitError as e:
            raise e

        except anthropic.BadRequestError as e:
            raise Exception(f"⚠️ _{localized_text('claude_invalid', bot_language)}._ ⚠️\n{str(e)}") from e

        except Exception as e:
            raise Exception(f"⚠️ _{localized_text('error', bot_language)}._ ⚠️\n{str(e)}") from e

    async def __common_get_chat_response_stream(self, chat_id: int, query: str, group_context: str = None):
        """
        Request a streaming response from the Claude model (no tool use).
        """
        bot_language = self.config['bot_language']
        try:
            common_args = await self.__prepare_chat(chat_id, query, group_context=group_context)
            # Remove tools for streaming — tool calls require non-streaming
            common_args.pop('tools', None)
            async with self.client.messages.stream(**common_args) as stream_response:
                async for event in stream_response:
                    yield event

        except anthropic.RateLimitError as e:
            raise e

        except anthropic.BadRequestError as e:
            raise Exception(f"⚠️ _{localized_text('claude_invalid', bot_language)}._ ⚠️\n{str(e)}") from e

        except Exception as e:
            raise Exception(f"⚠️ _{localized_text('error', bot_language)}._ ⚠️\n{str(e)}") from e

    async def __handle_tool_call(self, chat_id, response, times=0, plugins_used=(), user_id=None, chat_id_for_plugins=None):
        """
        Handle tool use blocks in the Claude response.
        """
        # Check if the response contains any tool_use blocks
        tool_use_blocks = [block for block in response.content if block.type == 'tool_use']

        if not tool_use_blocks:
            return response, plugins_used

        # Process each tool call
        tool_results = []
        for tool_block in tool_use_blocks:
            function_name = tool_block.name
            arguments = json.dumps(tool_block.input)
            tool_use_id = tool_block.id

            logging.info(f'Calling function {function_name} with arguments {arguments}')
            function_response = await self.plugin_manager.call_function(function_name, self, arguments, user_id=user_id, chat_id=chat_id_for_plugins)

            if function_name not in plugins_used:
                plugins_used += (function_name,)

            if is_direct_result(function_response):
                # Add a summary to history and return direct result
                self.__add_to_history(chat_id, role="assistant", content=[
                    {"type": "tool_use", "id": tool_use_id, "name": function_name, "input": tool_block.input}
                ])
                self.__add_to_history(chat_id, role="user", content=[
                    {"type": "tool_result", "tool_use_id": tool_use_id,
                     "content": json.dumps({'result': 'Done, the content has been sent to the user.'})}
                ])
                return function_response, plugins_used

            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": function_response
            })

        # Add the assistant's response (with tool_use) to history
        self.__add_to_history(chat_id, role="assistant", content=[
            {"type": "tool_use", "id": b.id, "name": b.name, "input": b.input}
            for b in tool_use_blocks
        ])
        # Add tool results as user message
        self.__add_to_history(chat_id, role="user", content=tool_results)

        # Call Claude again with the tool results — use routed model if available
        tools = self.plugin_manager.get_functions_specs()
        routing_info = self._last_routing_info.get(chat_id)
        model = routing_info['model'] if routing_info else self.config['model']
        if chat_id in self.model_overrides:
            model = self.model_overrides[chat_id]

        common_args = {
            'model': model,
            'messages': self.conversations[chat_id],
            'system': self._build_system_prompt(chat_id=chat_id),
            'temperature': self.config['temperature'],
            'max_tokens': self.config['max_tokens'],
        }
        if times < self.config['functions_max_consecutive_calls'] and len(tools) > 0:
            common_args['tools'] = tools

        response = await self.client.messages.create(**common_args)
        return await self.__handle_tool_call(chat_id, response, times + 1, plugins_used, user_id=user_id, chat_id_for_plugins=chat_id_for_plugins)

    async def __prepare_vision_chat(self, chat_id: int, content: list):
        """
        Prepare conversation history for a vision request (shared logic).
        Returns the common_args dict for the API call.
        """
        if chat_id not in self.conversations or self.__max_age_reached(chat_id):
            self.reset_chat_history(chat_id)

        self.last_updated[chat_id] = datetime.datetime.now()

        if self.config['enable_vision_follow_up_questions']:
            self.__add_to_history(chat_id, role="user", content=content)
        else:
            for message in content:
                if message['type'] == 'text':
                    query = message['text']
                    break
            self.__add_to_history(chat_id, role="user", content=query)

        # Summarize the chat history if it's too long
        token_count = self.__count_tokens(self.conversations[chat_id])
        exceeded_max_tokens = token_count + self.config['max_tokens'] > self.__max_model_tokens()
        exceeded_max_history_size = len(self.conversations[chat_id]) > self.config['max_history_size']

        if exceeded_max_tokens or exceeded_max_history_size:
            logging.info(f'Chat history for chat ID {chat_id} is too long. Summarising...')
            try:
                last = self.conversations[chat_id][-1]
                summary = await self.__summarise(self.conversations[chat_id][:-1])
                logging.debug(f'Summary: {summary}')
                self.reset_chat_history(chat_id)
                self.__add_to_history(chat_id, role="assistant", content=summary)
                self.conversations[chat_id] += [last]
            except Exception as e:
                logging.warning(f'Error while summarising chat history: {str(e)}. Popping elements instead...')
                self.conversations[chat_id] = self.conversations[chat_id][-self.config['max_history_size']:]

        # Build the message with vision content
        message = {'role': 'user', 'content': content}

        return {
            'model': self.config['model'],
            'messages': self.conversations[chat_id][:-1] + [message],
            'system': self._build_system_prompt(chat_id=chat_id),
            'temperature': self.config['temperature'],
            'max_tokens': self.config['vision_max_tokens'],
        }

    @retry(
        reraise=True,
        retry=retry_if_exception_type(anthropic.RateLimitError),
        wait=wait_fixed(20),
        stop=stop_after_attempt(3)
    )
    async def __common_get_chat_response_vision(self, chat_id: int, content: list):
        """
        Request a non-streaming vision response from the Claude model.
        """
        bot_language = self.config['bot_language']
        try:
            common_args = await self.__prepare_vision_chat(chat_id, content)
            return await self.client.messages.create(**common_args)

        except anthropic.RateLimitError as e:
            raise e

        except anthropic.BadRequestError as e:
            raise Exception(f"⚠️ _{localized_text('claude_invalid', bot_language)}._ ⚠️\n{str(e)}") from e

        except Exception as e:
            raise Exception(f"⚠️ _{localized_text('error', bot_language)}._ ⚠️\n{str(e)}") from e

    async def __common_get_chat_response_vision_stream(self, chat_id: int, content: list):
        """
        Request a streaming vision response from the Claude model.
        """
        bot_language = self.config['bot_language']
        try:
            common_args = await self.__prepare_vision_chat(chat_id, content)
            async with self.client.messages.stream(**common_args) as stream_response:
                async for event in stream_response:
                    yield event

        except anthropic.RateLimitError as e:
            raise e

        except anthropic.BadRequestError as e:
            raise Exception(f"⚠️ _{localized_text('claude_invalid', bot_language)}._ ⚠️\n{str(e)}") from e

        except Exception as e:
            raise Exception(f"⚠️ _{localized_text('error', bot_language)}._ ⚠️\n{str(e)}") from e

    async def interpret_image(self, chat_id, fileobj, prompt=None):
        """
        Interprets a given image file using the Claude vision model.
        """
        image_data, media_type = self.__encode_image_for_claude(fileobj)
        prompt = self.config['vision_prompt'] if prompt is None else prompt

        content = [
            {'type': 'image', 'source': {'type': 'base64', 'media_type': media_type, 'data': image_data}},
            {'type': 'text', 'text': prompt}
        ]

        response = await self.__common_get_chat_response_vision(chat_id, content)

        answer = ''
        for block in response.content:
            if block.type == 'text':
                answer += block.text

        answer = answer.strip()
        self.__add_to_history(chat_id, role="assistant", content=answer)

        bot_language = self.config['bot_language']
        total_tokens = response.usage.input_tokens + response.usage.output_tokens
        if self.config['show_usage']:
            answer += "\n\n---\n" \
                      f"💰 {str(total_tokens)} {localized_text('stats_tokens', bot_language)}" \
                      f" ({str(response.usage.input_tokens)} {localized_text('prompt', bot_language)}," \
                      f" {str(response.usage.output_tokens)} {localized_text('completion', bot_language)})"

        return answer, total_tokens

    async def interpret_image_stream(self, chat_id, fileobj, prompt=None):
        """
        Interprets a given image file using Claude vision with streaming.
        """
        image_data, media_type = self.__encode_image_for_claude(fileobj)
        prompt = self.config['vision_prompt'] if prompt is None else prompt

        content = [
            {'type': 'image', 'source': {'type': 'base64', 'media_type': media_type, 'data': image_data}},
            {'type': 'text', 'text': prompt}
        ]

        answer = ''
        async for chunk in self.__common_get_chat_response_vision_stream(chat_id, content):
            if hasattr(chunk, 'type') and chunk.type == 'content_block_delta':
                if hasattr(chunk.delta, 'text'):
                    answer += chunk.delta.text
                    yield answer, 'not_finished'

        answer = answer.strip()
        self.__add_to_history(chat_id, role="assistant", content=answer)
        tokens_used = str(self.__count_tokens(self.conversations[chat_id]))

        if self.config['show_usage']:
            answer += f"\n\n---\n💰 {tokens_used} {localized_text('stats_tokens', self.config['bot_language'])}"

        yield answer, tokens_used

    def reset_chat_history(self, chat_id, content=''):
        """
        Resets the conversation history.
        """
        self.conversations[chat_id] = []

    def __max_age_reached(self, chat_id) -> bool:
        """
        Checks if the maximum conversation age has been reached.
        """
        if chat_id not in self.last_updated:
            return False
        last_updated = self.last_updated[chat_id]
        now = datetime.datetime.now()
        max_age_minutes = self.config['max_conversation_age_minutes']
        return last_updated < now - datetime.timedelta(minutes=max_age_minutes)

    def __add_to_history(self, chat_id, role, content):
        """
        Adds a message to the conversation history.
        For user messages, prepends a timestamp so the model knows the current date/time.
        """
        if role == "user" and isinstance(content, str):
            tz = ZoneInfo(self.config.get('default_timezone', 'UTC'))
            now = datetime.datetime.now(tz)
            timestamp = now.strftime("%Y-%m-%d %H:%M %Z")
            content = f"[{timestamp}] {content}"
        self.conversations[chat_id].append({"role": role, "content": content})

    async def __summarise(self, conversation) -> str:
        """
        Summarises the conversation history.
        """
        messages = [
            {"role": "user", "content": f"Summarize this conversation in 700 characters or less:\n{str(conversation)}"}
        ]
        response = await self.client.messages.create(
            model=self.config['model'],
            messages=messages,
            system="You are a helpful assistant that summarizes conversations concisely.",
            max_tokens=1024,
            temperature=0.4
        )
        return response.content[0].text

    def __max_model_tokens(self):
        model = self.config['model']
        if model in CLAUDE_MODELS:
            return CLAUDE_MODELS[model]
        # Default to 200K for unknown Claude models
        return 200000

    def __count_tokens(self, messages) -> int:
        """
        Estimates the number of tokens for the given messages.
        This is a rough estimate since Claude doesn't have a local tokenizer like tiktoken.
        Uses ~4 characters per token as a rough approximation.
        """
        num_tokens = 0
        for message in messages:
            content = message.get('content', '')
            if isinstance(content, str):
                num_tokens += len(content) // 4
            elif isinstance(content, list):
                for item in content:
                    if isinstance(item, dict):
                        if item.get('type') == 'text':
                            num_tokens += len(item.get('text', '')) // 4
                        elif item.get('type') == 'image':
                            # Images cost roughly 1600 tokens for a typical image
                            num_tokens += 1600
            num_tokens += 4  # overhead per message
        return num_tokens

    def __encode_image_for_claude(self, fileobj) -> tuple[str, str]:
        """
        Encodes an image file object for the Claude API.
        Returns (base64_data, media_type)
        """
        import base64
        image_bytes = fileobj.getvalue()
        image_data = base64.standard_b64encode(image_bytes).decode('utf-8')
        # Detect format from the image bytes
        if image_bytes[:8] == b'\x89PNG\r\n\x1a\n':
            media_type = 'image/png'
        elif image_bytes[:2] == b'\xff\xd8':
            media_type = 'image/jpeg'
        elif image_bytes[:4] == b'GIF8':
            media_type = 'image/gif'
        elif image_bytes[:4] == b'RIFF' and image_bytes[8:12] == b'WEBP':
            media_type = 'image/webp'
        else:
            media_type = 'image/png'  # default
        return image_data, media_type
