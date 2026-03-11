from __future__ import annotations

import asyncio
import logging
import os
import io
from collections import deque

from uuid import uuid4
from telegram import BotCommandScopeAllGroupChats, Update, constants
from telegram import InlineKeyboardMarkup, InlineKeyboardButton, InlineQueryResultArticle
from telegram import InputTextMessageContent, BotCommand
from telegram.error import RetryAfter, TimedOut, BadRequest
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, \
    filters, InlineQueryHandler, CallbackQueryHandler, Application, ContextTypes, CallbackContext

from PIL import Image

from utils import is_group_chat, get_thread_id, message_text, wrap_with_indicator, split_into_chunks, \
    edit_message_with_retry, get_stream_cutoff_values, is_allowed, get_remaining_budget, is_admin, is_within_budget, \
    get_reply_to_message_id, add_chat_request_to_usage_tracker, error_handler, is_direct_result, handle_direct_result, \
    cleanup_intermediate_files
from claude_helper import ClaudeHelper, localized_text, CLAUDE_MODELS
from usage_tracker import UsageTracker
from user_memory import UserMemory
from group_memory import GroupMemory
from scheduler import BotScheduler


class ChatGPTTelegramBot:
    """
    Class representing a Claude Telegram Bot.
    """

    def __init__(self, config: dict, claude: ClaudeHelper, user_memory: UserMemory,
                 group_memory: GroupMemory = None, scheduler: BotScheduler = None):
        """
        Initializes the bot with the given configuration and Claude helper object.
        :param config: A dictionary containing the bot configuration
        :param claude: ClaudeHelper object
        :param user_memory: UserMemory object for persistent per-user memory
        :param group_memory: GroupMemory object for persistent per-group memory
        :param scheduler: BotScheduler object for reminders and recurring messages
        """
        self.config = config
        self.claude = claude
        self.user_memory = user_memory
        self.group_memory = group_memory
        self.scheduler = scheduler
        bot_language = self.config['bot_language']
        self.commands = [
            BotCommand(command='help', description=localized_text('help_description', bot_language)),
            BotCommand(command='reset', description=localized_text('reset_description', bot_language)),
            BotCommand(command='stats', description=localized_text('stats_description', bot_language)),
            BotCommand(command='resend', description=localized_text('resend_description', bot_language)),
            BotCommand(command='mymemory', description=localized_text('mymemory_description', bot_language)),
            BotCommand(command='forgetme', description=localized_text('forgetme_description', bot_language)),
            BotCommand(command='model', description=localized_text('model_description', bot_language)),
        ]

        # Add scheduler commands if enabled
        if self.scheduler:
            self.commands.extend([
                BotCommand(command='remind', description=localized_text('remind_description', bot_language)),
                BotCommand(command='schedule', description=localized_text('schedule_description', bot_language)),
                BotCommand(command='myschedules', description=localized_text('myschedules_description', bot_language)),
                BotCommand(command='cancelschedule', description=localized_text('cancelschedule_description', bot_language)),
            ])

        self.group_commands = [BotCommand(
            command='chat', description=localized_text('chat_description', bot_language)
        )] + self.commands

        # Add group-only commands
        if self.group_memory:
            self.group_commands.extend([
                BotCommand(command='persona', description=localized_text('persona_description', bot_language)),
                BotCommand(command='groupmemory', description=localized_text('groupmemory_description', bot_language)),
                BotCommand(command='forgetgroup', description=localized_text('forgetgroup_description', bot_language)),
            ])
        self.disallowed_message = localized_text('disallowed', bot_language)
        self.budget_limit_message = localized_text('budget_limit', bot_language)
        self.usage = {}
        self.last_message = {}
        self.inline_queries_cache = {}
        self.group_chat_messages: dict[int, deque] = {}  # {chat_id: rolling buffer of recent messages}
        self.telegram_native_stream = self.config.get('telegram_native_stream', False)
        if self.telegram_native_stream:
            logging.info('TELEGRAM_NATIVE_STREAM enabled, using legacy edit-based transport until Bot API adapter is added.')

    async def _stream_reply_chunks(self, update: Update, context: ContextTypes.DEFAULT_TYPE,
                                   stream_response, chat_id: int, *, initial_text: str | None = None,
                                   initial_markdown: bool = False) -> int | None:
        """
        Stream model output to a regular chat by repeatedly editing Telegram messages.
        Returns total tokens reported by the model stream.
        Returns None when a direct plugin result was handled and caller should return early.
        """
        i = 0
        prev = ''
        sent_message = None
        backoff = 0
        stream_chunk = 0
        total_tokens = 0

        async for content, tokens in stream_response:
            if is_direct_result(content):
                await handle_direct_result(self.config, update, content)
                return None

            if len(content.strip()) == 0:
                continue

            stream_chunks = split_into_chunks(content)
            if len(stream_chunks) > 1:
                content = stream_chunks[-1]
                if stream_chunk != len(stream_chunks) - 1:
                    stream_chunk += 1
                    try:
                        await edit_message_with_retry(context, chat_id, str(sent_message.message_id),
                                                      stream_chunks[-2])
                    except Exception as e:
                        logging.warning(f'Stream chunk edit failed: {e}')
                    try:
                        sent_message = await update.effective_message.reply_text(
                            message_thread_id=get_thread_id(update),
                            text=content if len(content) > 0 else "..."
                        )
                    except Exception as e:
                        logging.warning(f'Stream chunk send failed: {e}')
                    continue

            cutoff = get_stream_cutoff_values(update, content)
            cutoff += backoff

            if i == 0:
                try:
                    if sent_message is not None:
                        await context.bot.delete_message(chat_id=sent_message.chat_id,
                                                         message_id=sent_message.message_id)
                    text_to_send = initial_text if initial_text is not None else content
                    sent_message = await update.effective_message.reply_text(
                        message_thread_id=get_thread_id(update),
                        reply_to_message_id=get_reply_to_message_id(self.config, update),
                        text=text_to_send,
                        parse_mode=constants.ParseMode.MARKDOWN if initial_markdown else None
                    )
                except Exception as e:
                    logging.warning(f'Initial stream message failed: {e}')
                    continue

            elif abs(len(content) - len(prev)) > cutoff or tokens != 'not_finished':
                prev = content

                try:
                    use_markdown = tokens != 'not_finished'
                    await edit_message_with_retry(context, chat_id, str(sent_message.message_id),
                                                  text=content, markdown=use_markdown)

                except RetryAfter as e:
                    backoff += 5
                    await asyncio.sleep(e.retry_after)
                    continue

                except TimedOut:
                    backoff += 5
                    await asyncio.sleep(0.5)
                    continue

                except Exception as e:
                    logging.warning(f'Stream edit failed: {e}')
                    backoff += 5
                    continue

                await asyncio.sleep(0.01)

            i += 1
            if tokens != 'not_finished':
                total_tokens = int(tokens)

        return total_tokens

    async def _stream_inline_chunks(self, update: Update, context: ContextTypes.DEFAULT_TYPE,
                                    stream_response, inline_message_id: str, query: str,
                                    answer_label: str) -> int:
        """
        Stream model output to an inline response by editing a single inline message.
        Returns total tokens reported by the model stream.
        """
        i = 0
        prev = ''
        backoff = 0
        total_tokens = 0
        unavailable_message = localized_text("function_unavailable_in_inline_mode", self.config['bot_language'])

        async for content, tokens in stream_response:
            if is_direct_result(content):
                cleanup_intermediate_files(content)
                await edit_message_with_retry(context, chat_id=None,
                                              message_id=inline_message_id,
                                              text=f'{query}\n\n_{answer_label}:_\n{unavailable_message}',
                                              is_inline=True)
                return 0

            if len(content.strip()) == 0:
                continue

            cutoff = get_stream_cutoff_values(update, content)
            cutoff += backoff

            if i == 0:
                try:
                    await edit_message_with_retry(context, chat_id=None,
                                                  message_id=inline_message_id,
                                                  text=f'{query}\n\n{answer_label}:\n{content}',
                                                  is_inline=True)
                except Exception as e:
                    logging.warning(f'Inline stream initial edit failed: {e}')
                    continue

            elif abs(len(content) - len(prev)) > cutoff or tokens != 'not_finished':
                prev = content
                try:
                    use_markdown = tokens != 'not_finished'
                    divider = '_' if use_markdown else ''
                    text = f'{query}\n\n{divider}{answer_label}:{divider}\n{content}'

                    # We only want to send the first 4096 characters. No chunking allowed in inline mode.
                    text = text[:4096]

                    await edit_message_with_retry(context, chat_id=None, message_id=inline_message_id,
                                                  text=text, markdown=use_markdown, is_inline=True)

                except RetryAfter as e:
                    backoff += 5
                    await asyncio.sleep(e.retry_after)
                    continue
                except TimedOut:
                    backoff += 5
                    await asyncio.sleep(0.5)
                    continue
                except Exception as e:
                    logging.warning(f'Inline stream edit failed: {e}')
                    backoff += 5
                    continue

                await asyncio.sleep(0.01)

            i += 1
            if tokens != 'not_finished':
                total_tokens = int(tokens)

        return total_tokens

    async def help(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        """
        Shows the help menu.
        """
        commands = self.group_commands if is_group_chat(update) else self.commands
        commands_description = [f'/{command.command} - {command.description}' for command in commands]
        bot_language = self.config['bot_language']
        help_text = (
                localized_text('help_text', bot_language)[0] +
                '\n\n' +
                '\n'.join(commands_description) +
                '\n\n' +
                localized_text('help_text', bot_language)[1] +
                '\n\n' +
                localized_text('help_text', bot_language)[2]
        )
        await update.message.reply_text(help_text, disable_web_page_preview=True)

    async def stats(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        Returns token usage statistics for current day and month.
        """
        if not await is_allowed(self.config, update, context):
            logging.warning(f'User {update.message.from_user.name} (id: {update.message.from_user.id}) '
                            'is not allowed to request their usage statistics')
            await self.send_disallowed_message(update, context)
            return

        logging.info(f'User {update.message.from_user.name} (id: {update.message.from_user.id}) '
                     'requested their usage statistics')

        user_id = update.message.from_user.id
        if user_id not in self.usage:
            self.usage[user_id] = UsageTracker(user_id, update.message.from_user.name)

        tokens_today, tokens_month = self.usage[user_id].get_current_token_usage()
        vision_today, vision_month = self.usage[user_id].get_current_vision_tokens()
        current_cost = self.usage[user_id].get_current_cost()

        chat_id = update.effective_chat.id
        chat_messages, chat_token_length = self.claude.get_conversation_stats(chat_id)
        remaining_budget = get_remaining_budget(self.config, self.usage, update)
        bot_language = self.config['bot_language']

        text_current_conversation = (
            f"*{localized_text('stats_conversation', bot_language)[0]}*:\n"
            f"{chat_messages} {localized_text('stats_conversation', bot_language)[1]}\n"
            f"{chat_token_length} {localized_text('stats_conversation', bot_language)[2]}\n"
            "----------------------------\n"
        )

        text_today_vision = ""
        if self.config.get('enable_vision', False):
            text_today_vision = f"{vision_today} {localized_text('stats_vision', bot_language)}\n"

        text_today = (
            f"*{localized_text('usage_today', bot_language)}:*\n"
            f"{tokens_today} {localized_text('stats_tokens', bot_language)}\n"
            f"{text_today_vision}"
            f"{localized_text('stats_total', bot_language)}{current_cost['cost_today']:.2f}\n"
            "----------------------------\n"
        )

        text_month_vision = ""
        if self.config.get('enable_vision', False):
            text_month_vision = f"{vision_month} {localized_text('stats_vision', bot_language)}\n"

        text_month = (
            f"*{localized_text('usage_month', bot_language)}:*\n"
            f"{tokens_month} {localized_text('stats_tokens', bot_language)}\n"
            f"{text_month_vision}"
            f"{localized_text('stats_total', bot_language)}{current_cost['cost_month']:.2f}"
        )

        # text_budget filled with conditional content
        text_budget = "\n\n"
        budget_period = self.config['budget_period']
        if remaining_budget < float('inf'):
            text_budget += (
                f"{localized_text('stats_budget', bot_language)}"
                f"{localized_text(budget_period, bot_language)}: "
                f"${remaining_budget:.2f}.\n"
            )

        usage_text = text_current_conversation + text_today + text_month + text_budget
        await update.message.reply_text(usage_text, parse_mode=constants.ParseMode.MARKDOWN)

    async def resend(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        Resend the last request
        """
        if not await is_allowed(self.config, update, context):
            logging.warning(f'User {update.message.from_user.name}  (id: {update.message.from_user.id})'
                            ' is not allowed to resend the message')
            await self.send_disallowed_message(update, context)
            return

        chat_id = update.effective_chat.id
        if chat_id not in self.last_message:
            logging.warning(f'User {update.message.from_user.name} (id: {update.message.from_user.id})'
                            ' does not have anything to resend')
            await update.effective_message.reply_text(
                message_thread_id=get_thread_id(update),
                text=localized_text('resend_failed', self.config['bot_language'])
            )
            return

        # Update message text, clear self.last_message and send the request to prompt
        logging.info(f'Resending the last prompt from user: {update.message.from_user.name} '
                     f'(id: {update.message.from_user.id})')
        with update.message._unfrozen() as message:
            message.text = self.last_message.pop(chat_id)

        await self.prompt(update=update, context=context)

    async def reset(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        Resets the conversation.
        """
        if not await is_allowed(self.config, update, context):
            logging.warning(f'User {update.message.from_user.name} (id: {update.message.from_user.id}) '
                            'is not allowed to reset the conversation')
            await self.send_disallowed_message(update, context)
            return

        logging.info(f'Resetting the conversation for user {update.message.from_user.name} '
                     f'(id: {update.message.from_user.id})...')

        chat_id = update.effective_chat.id
        self.claude.reset_chat_history(chat_id=chat_id)
        await update.effective_message.reply_text(
            message_thread_id=get_thread_id(update),
            text=localized_text('reset_done', self.config['bot_language'])
        )

    async def mymemory(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        Shows the user what the bot remembers about them.
        """
        if not await is_allowed(self.config, update, context):
            await self.send_disallowed_message(update, context)
            return

        user_id = update.message.from_user.id
        memory_text = self.user_memory.get_all_formatted(user_id)
        bot_language = self.config['bot_language']
        header = localized_text('mymemory_header', bot_language)
        await update.effective_message.reply_text(
            message_thread_id=get_thread_id(update),
            text=f"{header}\n\n{memory_text}"
        )

    async def forgetme(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        Clears all stored memory for the user.
        """
        if not await is_allowed(self.config, update, context):
            await self.send_disallowed_message(update, context)
            return

        user_id = update.message.from_user.id
        self.user_memory.clear_user(user_id)
        bot_language = self.config['bot_language']
        await update.effective_message.reply_text(
            message_thread_id=get_thread_id(update),
            text=localized_text('forgetme_done', bot_language)
        )

    async def model(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        View or set the model for this chat. /model shows current, /model <name> sets override.
        """
        if not await is_allowed(self.config, update, context):
            await self.send_disallowed_message(update, context)
            return

        chat_id = update.effective_chat.id
        bot_language = self.config['bot_language']
        args = context.args

        if not args:
            # Show current model
            override = self.claude.get_model_override(chat_id)
            if override:
                text = f"{localized_text('model_current', bot_language)}: {override} (override)"
            elif self.config.get('enable_smart_routing', False):
                text = f"{localized_text('model_current', bot_language)}: auto (smart routing)"
            else:
                text = f"{localized_text('model_current', bot_language)}: {self.claude.config['model']}"
            await update.effective_message.reply_text(
                message_thread_id=get_thread_id(update), text=text
            )
            return

        model_arg = args[0].lower()
        model_shortcuts = {
            'haiku': 'claude-haiku-4-5-20251001',
            'sonnet': 'claude-sonnet-4-5-20250929',
            'opus': 'claude-opus-4-6',
            'auto': None,
        }

        if model_arg == 'auto':
            self.claude.clear_model_override(chat_id)
            await update.effective_message.reply_text(
                message_thread_id=get_thread_id(update),
                text=localized_text('model_auto', bot_language)
            )
        elif model_arg in model_shortcuts:
            model_id = model_shortcuts[model_arg]
            self.claude.set_model_override(chat_id, model_id)
            await update.effective_message.reply_text(
                message_thread_id=get_thread_id(update),
                text=f"{localized_text('model_set', bot_language)}: {model_arg.capitalize()}"
            )
        elif model_arg in CLAUDE_MODELS:
            self.claude.set_model_override(chat_id, model_arg)
            await update.effective_message.reply_text(
                message_thread_id=get_thread_id(update),
                text=f"{localized_text('model_set', bot_language)}: {model_arg}"
            )
        else:
            available = ", ".join(list(model_shortcuts.keys()))
            await update.effective_message.reply_text(
                message_thread_id=get_thread_id(update),
                text=f"Unknown model. Available: {available}"
            )

    async def persona(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        Set, view, or clear the bot persona for this group. Admin-only.
        """
        if not is_group_chat(update):
            await update.effective_message.reply_text(
                message_thread_id=get_thread_id(update),
                text="This command is only available in group chats."
            )
            return

        chat_id = update.effective_chat.id
        bot_language = self.config['bot_language']
        args_text = " ".join(context.args) if context.args else ""

        if not args_text:
            # Show current persona
            persona = self.group_memory.get_persona(chat_id) if self.group_memory else None
            if persona:
                await update.effective_message.reply_text(
                    message_thread_id=get_thread_id(update),
                    text=f"{localized_text('persona_current', bot_language)}:\n\n{persona}"
                )
            else:
                await update.effective_message.reply_text(
                    message_thread_id=get_thread_id(update),
                    text="No persona set. Default assistant personality is active."
                )
            return

        # Check if user is a group admin or bot admin
        if not await self._is_group_admin(update, context):
            await update.effective_message.reply_text(
                message_thread_id=get_thread_id(update),
                text=localized_text('persona_admin_only', bot_language)
            )
            return

        if args_text.lower() == 'reset':
            self.group_memory.clear_persona(chat_id)
            await update.effective_message.reply_text(
                message_thread_id=get_thread_id(update),
                text=localized_text('persona_cleared', bot_language)
            )
        else:
            self.group_memory.set_persona(chat_id, args_text)
            # Reset conversation so persona takes effect immediately
            self.claude.reset_chat_history(chat_id=chat_id)
            await update.effective_message.reply_text(
                message_thread_id=get_thread_id(update),
                text=localized_text('persona_set', bot_language)
            )

    async def groupmemory(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        Shows what the bot remembers about this group.
        """
        if not is_group_chat(update):
            await update.effective_message.reply_text(
                message_thread_id=get_thread_id(update),
                text="This command is only available in group chats."
            )
            return

        if not await is_allowed(self.config, update, context):
            await self.send_disallowed_message(update, context)
            return

        chat_id = update.effective_chat.id
        bot_language = self.config['bot_language']
        memory_text = self.group_memory.get_all_formatted(chat_id) if self.group_memory else "No group memories stored."
        header = localized_text('groupmemory_description', bot_language)
        await update.effective_message.reply_text(
            message_thread_id=get_thread_id(update),
            text=f"{header}\n\n{memory_text}"
        )

    async def forgetgroup(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        Clears all stored group memory. Admin-only.
        """
        if not is_group_chat(update):
            await update.effective_message.reply_text(
                message_thread_id=get_thread_id(update),
                text="This command is only available in group chats."
            )
            return

        if not await self._is_group_admin(update, context):
            bot_language = self.config['bot_language']
            await update.effective_message.reply_text(
                message_thread_id=get_thread_id(update),
                text=localized_text('forgetgroup_admin_only', bot_language)
            )
            return

        chat_id = update.effective_chat.id
        if self.group_memory:
            self.group_memory.clear_group(chat_id)
        bot_language = self.config['bot_language']
        await update.effective_message.reply_text(
            message_thread_id=get_thread_id(update),
            text=localized_text('groupmemory_cleared', bot_language)
        )

    async def remind(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        Set a one-time reminder. Usage: /remind <time> <message>
        Example: /remind in 2 hours Call mom
        """
        if not await is_allowed(self.config, update, context):
            await self.send_disallowed_message(update, context)
            return

        if not self.scheduler:
            await update.effective_message.reply_text(
                message_thread_id=get_thread_id(update),
                text="Scheduler is not enabled."
            )
            return

        args_text = " ".join(context.args) if context.args else ""
        if not args_text:
            await update.effective_message.reply_text(
                message_thread_id=get_thread_id(update),
                text="Usage: /remind <time> <message>\nExample: /remind in 2 hours Call mom"
            )
            return

        user_id = update.message.from_user.id
        chat_id = update.effective_chat.id

        # Try to split time from message — use dateparser to find the time part
        # Strategy: try progressively shorter prefixes as the time expression
        import dateparser
        best_time = None
        best_message = args_text
        words = args_text.split()
        for i in range(min(len(words), 8), 0, -1):
            time_part = " ".join(words[:i])
            parsed = dateparser.parse(time_part, settings={'PREFER_DATES_FROM': 'future'})
            if parsed:
                best_time = time_part
                best_message = " ".join(words[i:]) or "Reminder!"
                break

        if not best_time:
            # Fallback: treat first half as time, second as message
            best_time = args_text
            best_message = "Reminder!"

        job_id, result = self.scheduler.add_reminder(user_id, chat_id, best_message, best_time)
        bot_language = self.config['bot_language']
        if job_id:
            await update.effective_message.reply_text(
                message_thread_id=get_thread_id(update),
                text=f"{localized_text('reminder_set', bot_language)} {result}\n\"{best_message}\" (ID: {job_id})"
            )
        else:
            await update.effective_message.reply_text(
                message_thread_id=get_thread_id(update),
                text=result
            )

    async def schedule(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        Set a recurring schedule. Usage: /schedule <schedule> <message>
        Example: /schedule every day at 8:00 Good morning briefing
        """
        if not await is_allowed(self.config, update, context):
            await self.send_disallowed_message(update, context)
            return

        if not self.scheduler:
            await update.effective_message.reply_text(
                message_thread_id=get_thread_id(update),
                text="Scheduler is not enabled."
            )
            return

        args_text = " ".join(context.args) if context.args else ""
        if not args_text:
            await update.effective_message.reply_text(
                message_thread_id=get_thread_id(update),
                text="Usage: /schedule <schedule> <message>\nExample: /schedule every day at 8:00 Good morning!"
            )
            return

        user_id = update.message.from_user.id
        chat_id = update.effective_chat.id

        # Split on the time component — everything after "at HH:MM" pattern is the message
        import re
        time_match = re.search(r'at\s+\d{1,2}:?\d{0,2}\s*(am|pm)?\s*', args_text, re.IGNORECASE)
        if time_match:
            schedule_part = args_text[:time_match.end()].strip()
            message_part = args_text[time_match.end():].strip() or "Scheduled reminder!"
        else:
            schedule_part = args_text
            message_part = "Scheduled reminder!"

        job_id, result = self.scheduler.add_recurring(user_id, chat_id, message_part, schedule_part)
        bot_language = self.config['bot_language']
        if job_id:
            await update.effective_message.reply_text(
                message_thread_id=get_thread_id(update),
                text=f"{localized_text('schedule_set', bot_language)} {result}\n\"{message_part}\" (ID: {job_id})"
            )
        else:
            await update.effective_message.reply_text(
                message_thread_id=get_thread_id(update),
                text=result
            )

    async def myschedules(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        List all active reminders and schedules for the user.
        """
        if not await is_allowed(self.config, update, context):
            await self.send_disallowed_message(update, context)
            return

        if not self.scheduler:
            await update.effective_message.reply_text(
                message_thread_id=get_thread_id(update),
                text="Scheduler is not enabled."
            )
            return

        user_id = update.message.from_user.id
        jobs = self.scheduler.get_user_jobs(user_id)
        bot_language = self.config['bot_language']

        if not jobs:
            await update.effective_message.reply_text(
                message_thread_id=get_thread_id(update),
                text=localized_text('no_active_schedules', bot_language)
            )
            return

        lines = []
        for job in jobs:
            job_type = "recurring" if job.get("recurring") else "one-time"
            if job.get("recurring"):
                schedule_info = f"at {job.get('time', '?')}"
                if job.get('days'):
                    day_labels = {0: 'Mon', 1: 'Tue', 2: 'Wed', 3: 'Thu', 4: 'Fri', 5: 'Sat', 6: 'Sun'}
                    days_str = ", ".join(day_labels.get(d, '?') for d in job['days'])
                    schedule_info = f"{days_str} {schedule_info}"
                else:
                    schedule_info = f"daily {schedule_info}"
            else:
                schedule_info = job.get("run_at", "unknown time")

            lines.append(f"[{job['id']}] ({job_type}) {schedule_info}: \"{job['message']}\"")

        text = "\n".join(lines)
        await update.effective_message.reply_text(
            message_thread_id=get_thread_id(update),
            text=text
        )

    async def cancelschedule(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        Cancel a scheduled job by ID. Usage: /cancelschedule <id>
        """
        if not await is_allowed(self.config, update, context):
            await self.send_disallowed_message(update, context)
            return

        if not self.scheduler:
            await update.effective_message.reply_text(
                message_thread_id=get_thread_id(update),
                text="Scheduler is not enabled."
            )
            return

        bot_language = self.config['bot_language']
        if not context.args:
            await update.effective_message.reply_text(
                message_thread_id=get_thread_id(update),
                text="Usage: /cancelschedule <job_id>"
            )
            return

        job_id = context.args[0]
        user_id = update.message.from_user.id
        if self.scheduler.cancel_job(job_id, user_id):
            await update.effective_message.reply_text(
                message_thread_id=get_thread_id(update),
                text=localized_text('schedule_cancelled', bot_language)
            )
        else:
            await update.effective_message.reply_text(
                message_thread_id=get_thread_id(update),
                text=localized_text('schedule_not_found', bot_language)
            )

    async def _is_group_admin(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
        """Check if the user is a group admin, group creator, or bot admin."""
        user_id = update.message.from_user.id
        # Bot admin check
        if is_admin(self.config, user_id):
            return True
        # Telegram group admin check
        try:
            member = await context.bot.get_chat_member(update.effective_chat.id, user_id)
            return member.status in ('administrator', 'creator')
        except Exception:
            return False

    async def vision(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        Interpret image using Claude vision.
        """
        if not self.config['enable_vision'] or not await self.check_allowed_and_within_budget(update, context):
            return

        chat_id = update.effective_chat.id
        prompt = update.message.caption

        if is_group_chat(update):
            if self.config['ignore_group_vision']:
                logging.info('Vision coming from group chat, ignoring...')
                return
            else:
                trigger_keyword = self.config['group_trigger_keyword']
                if (prompt is None and trigger_keyword != '') or \
                   (prompt is not None and not prompt.lower().startswith(trigger_keyword.lower())):
                    logging.info('Vision coming from group chat with wrong keyword, ignoring...')
                    return

        image = update.message.effective_attachment[-1]

        async def _execute():
            bot_language = self.config['bot_language']
            try:
                media_file = await context.bot.get_file(image.file_id)
                temp_file = io.BytesIO(await media_file.download_as_bytearray())
            except Exception as e:
                logging.exception(e)
                await update.effective_message.reply_text(
                    message_thread_id=get_thread_id(update),
                    reply_to_message_id=get_reply_to_message_id(self.config, update),
                    text=(
                        f"{localized_text('media_download_fail', bot_language)[0]}: "
                        f"{str(e)}. {localized_text('media_download_fail', bot_language)[1]}"
                    ),
                    parse_mode=constants.ParseMode.MARKDOWN
                )
                return

            # Convert to PNG for Claude
            temp_file_png = io.BytesIO()
            try:
                original_image = Image.open(temp_file)
                original_image.save(temp_file_png, format='PNG')
                logging.info(f'New vision request received from user {update.message.from_user.name} '
                             f'(id: {update.message.from_user.id})')
            except Exception as e:
                logging.exception(e)
                await update.effective_message.reply_text(
                    message_thread_id=get_thread_id(update),
                    reply_to_message_id=get_reply_to_message_id(self.config, update),
                    text=localized_text('media_type_fail', bot_language)
                )
                return

            user_id = update.message.from_user.id
            if user_id not in self.usage:
                self.usage[user_id] = UsageTracker(user_id, update.message.from_user.name)

            # Prefix with sender identity for user distinction and memory
            vision_prompt = self._prefix_with_user_context(update, prompt) if prompt else prompt

            if self.config['stream']:
                stream_response = self.claude.interpret_image_stream(
                    chat_id=chat_id,
                    fileobj=temp_file_png,
                    prompt=vision_prompt
                )
                total_tokens = await self._stream_reply_chunks(update, context, stream_response, chat_id)


            else:
                try:
                    interpretation, total_tokens = await self.claude.interpret_image(chat_id, temp_file_png, prompt=vision_prompt)

                    try:
                        await update.effective_message.reply_text(
                            message_thread_id=get_thread_id(update),
                            reply_to_message_id=get_reply_to_message_id(self.config, update),
                            text=interpretation,
                            parse_mode=constants.ParseMode.MARKDOWN
                        )
                    except BadRequest:
                        try:
                            await update.effective_message.reply_text(
                                message_thread_id=get_thread_id(update),
                                reply_to_message_id=get_reply_to_message_id(self.config, update),
                                text=interpretation
                            )
                        except Exception as e:
                            logging.exception(e)
                            await update.effective_message.reply_text(
                                message_thread_id=get_thread_id(update),
                                reply_to_message_id=get_reply_to_message_id(self.config, update),
                                text=f"{localized_text('vision_fail', bot_language)}: {str(e)}",
                                parse_mode=constants.ParseMode.MARKDOWN
                            )
                except Exception as e:
                    logging.exception(e)
                    await update.effective_message.reply_text(
                        message_thread_id=get_thread_id(update),
                        reply_to_message_id=get_reply_to_message_id(self.config, update),
                        text=f"{localized_text('vision_fail', bot_language)}: {str(e)}",
                        parse_mode=constants.ParseMode.MARKDOWN
                    )
            vision_token_price = self.config['vision_token_price']
            self.usage[user_id].add_vision_tokens(total_tokens, vision_token_price)

            allowed_user_ids = self.config['allowed_user_ids'].split(',')
            if str(user_id) not in allowed_user_ids and 'guests' in self.usage:
                self.usage["guests"].add_vision_tokens(total_tokens, vision_token_price)

        await wrap_with_indicator(update, context, _execute, constants.ChatAction.TYPING)

    async def prompt(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        React to incoming messages and respond accordingly.
        """
        if update.edited_message or not update.message or update.message.via_bot:
            return

        if not await self.check_allowed_and_within_budget(update, context):
            return

        logging.info(
            f'New message received from user {update.message.from_user.name} (id: {update.message.from_user.id})')
        chat_id = update.effective_chat.id
        user_id = update.message.from_user.id
        prompt = message_text(update.message)
        self.last_message[chat_id] = prompt

        if is_group_chat(update):
            trigger_keyword = self.config['group_trigger_keyword']

            if prompt.lower().startswith(trigger_keyword.lower()) or update.message.text.lower().startswith('/chat'):
                if prompt.lower().startswith(trigger_keyword.lower()):
                    prompt = prompt[len(trigger_keyword):].strip()

                if update.message.reply_to_message and \
                        update.message.reply_to_message.text and \
                        update.message.reply_to_message.from_user.id != context.bot.id:
                    prompt = f'"{update.message.reply_to_message.text}" {prompt}'
            else:
                if update.message.reply_to_message and update.message.reply_to_message.from_user.id == context.bot.id:
                    logging.info('Message is a reply to the bot, allowing...')
                else:
                    logging.warning('Message does not start with trigger keyword, ignoring...')
                    return

        # Prefix the prompt with sender identity so Claude can distinguish
        # users and use the memory tools with the correct user_id.
        prompt = self._prefix_with_user_context(update, prompt)

        # Build group context from the rolling buffer (only used on fresh conversations)
        group_context = None
        if is_group_chat(update):
            group_context = self._get_group_context(chat_id)

        try:
            total_tokens = 0

            if self.config['stream']:
                await update.effective_message.reply_chat_action(
                    action=constants.ChatAction.TYPING,
                    message_thread_id=get_thread_id(update)
                )

                stream_response = self.claude.get_chat_response_stream(
                    chat_id=chat_id,
                    query=prompt,
                    user_id=user_id,
                    group_context=group_context
                )
                total_tokens = await self._stream_reply_chunks(update, context, stream_response, chat_id)

            else:
                async def _reply():
                    nonlocal total_tokens
                    response, total_tokens = await self.claude.get_chat_response(chat_id=chat_id, query=prompt, user_id=user_id, group_context=group_context)

                    if is_direct_result(response):
                        return await handle_direct_result(self.config, update, response)

                    # Split into chunks of 4096 characters (Telegram's message limit)
                    chunks = split_into_chunks(response)

                    for index, chunk in enumerate(chunks):
                        try:
                            await update.effective_message.reply_text(
                                message_thread_id=get_thread_id(update),
                                reply_to_message_id=get_reply_to_message_id(self.config,
                                                                            update) if index == 0 else None,
                                text=chunk,
                                parse_mode=constants.ParseMode.MARKDOWN
                            )
                        except Exception:
                            try:
                                await update.effective_message.reply_text(
                                    message_thread_id=get_thread_id(update),
                                    reply_to_message_id=get_reply_to_message_id(self.config,
                                                                                update) if index == 0 else None,
                                    text=chunk
                                )
                            except Exception as exception:
                                raise exception

                await wrap_with_indicator(update, context, _reply, constants.ChatAction.TYPING)

            add_chat_request_to_usage_tracker(self.usage, self.config, user_id, total_tokens)

        except Exception as e:
            logging.exception(e)
            await update.effective_message.reply_text(
                message_thread_id=get_thread_id(update),
                reply_to_message_id=get_reply_to_message_id(self.config, update),
                text=f"{localized_text('chat_fail', self.config['bot_language'])} {str(e)}",
                parse_mode=constants.ParseMode.MARKDOWN
            )

    async def inline_query(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """
        Handle the inline query. This is run when you type: @botusername <query>
        """
        query = update.inline_query.query
        if len(query) < 3:
            return
        if not await self.check_allowed_and_within_budget(update, context, is_inline=True):
            return

        callback_data_suffix = "claude:"
        result_id = str(uuid4())
        self.inline_queries_cache[result_id] = query
        callback_data = f'{callback_data_suffix}{result_id}'

        await self.send_inline_query_result(update, result_id, message_content=query, callback_data=callback_data)

    async def send_inline_query_result(self, update: Update, result_id, message_content, callback_data=""):
        """
        Send inline query result
        """
        try:
            reply_markup = None
            bot_language = self.config['bot_language']
            if callback_data:
                reply_markup = InlineKeyboardMarkup([[
                    InlineKeyboardButton(text=f'🤖 {localized_text("answer_with_claude", bot_language)}',
                                         callback_data=callback_data)
                ]])

            inline_query_result = InlineQueryResultArticle(
                id=result_id,
                title=localized_text("ask_claude", bot_language),
                input_message_content=InputTextMessageContent(message_content),
                description=message_content,
                thumbnail_url='https://user-images.githubusercontent.com/11541888/223106202-7576ff11-2c8e-408d-94ea-b02a7a32149a.png',
                reply_markup=reply_markup
            )

            await update.inline_query.answer([inline_query_result], cache_time=0)
        except Exception as e:
            logging.error(f'An error occurred while generating the result card for inline query {e}')

    async def handle_callback_inline_query(self, update: Update, context: CallbackContext):
        """
        Handle the callback query from the inline query result
        """
        callback_data = update.callback_query.data
        user_id = update.callback_query.from_user.id
        inline_message_id = update.callback_query.inline_message_id
        name = update.callback_query.from_user.name
        callback_data_suffix = "claude:"
        query = ""
        bot_language = self.config['bot_language']
        answer_tr = localized_text("answer", bot_language)
        loading_tr = localized_text("loading", bot_language)

        try:
            if callback_data.startswith(callback_data_suffix):
                unique_id = callback_data.split(':')[1]
                total_tokens = 0

                # Retrieve the prompt from the cache
                query = self.inline_queries_cache.get(unique_id)
                if query:
                    self.inline_queries_cache.pop(unique_id)
                else:
                    error_message = (
                        f'{localized_text("error", bot_language)}. '
                        f'{localized_text("try_again", bot_language)}'
                    )
                    await edit_message_with_retry(context, chat_id=None, message_id=inline_message_id,
                                                  text=f'{query}\n\n_{answer_tr}:_\n{error_message}',
                                                  is_inline=True)
                    return

                unavailable_message = localized_text("function_unavailable_in_inline_mode", bot_language)
                if self.config['stream']:
                    stream_response = self.claude.get_chat_response_stream(chat_id=user_id, query=query, user_id=user_id)
                    total_tokens = await self._stream_inline_chunks(
                        update,
                        context,
                        stream_response,
                        inline_message_id,
                        query,
                        answer_tr
                    )

                else:
                    async def _send_inline_query_response():
                        nonlocal total_tokens
                        # Edit the current message to indicate that the answer is being processed
                        await context.bot.edit_message_text(inline_message_id=inline_message_id,
                                                            text=f'{query}\n\n_{answer_tr}:_\n{loading_tr}',
                                                            parse_mode=constants.ParseMode.MARKDOWN)

                        logging.info(f'Generating response for inline query by {name}')
                        response, total_tokens = await self.claude.get_chat_response(chat_id=user_id, query=query, user_id=user_id)

                        if is_direct_result(response):
                            cleanup_intermediate_files(response)
                            await edit_message_with_retry(context, chat_id=None,
                                                          message_id=inline_message_id,
                                                          text=f'{query}\n\n_{answer_tr}:_\n{unavailable_message}',
                                                          is_inline=True)
                            return

                        text_content = f'{query}\n\n_{answer_tr}:_\n{response}'

                        # We only want to send the first 4096 characters. No chunking allowed in inline mode.
                        text_content = text_content[:4096]

                        # Edit the original message with the generated content
                        await edit_message_with_retry(context, chat_id=None, message_id=inline_message_id,
                                                      text=text_content, is_inline=True)

                    await wrap_with_indicator(update, context, _send_inline_query_response,
                                              constants.ChatAction.TYPING, is_inline=True)

                add_chat_request_to_usage_tracker(self.usage, self.config, user_id, total_tokens)

        except Exception as e:
            logging.error(f'Failed to respond to an inline query via button callback: {e}')
            logging.exception(e)
            localized_answer = localized_text('chat_fail', self.config['bot_language'])
            await edit_message_with_retry(context, chat_id=None, message_id=inline_message_id,
                                          text=f"{query}\n\n_{answer_tr}:_\n{localized_answer} {str(e)}",
                                          is_inline=True)

    async def check_allowed_and_within_budget(self, update: Update, context: ContextTypes.DEFAULT_TYPE,
                                              is_inline=False) -> bool:
        """
        Checks if the user is allowed to use the bot and if they are within their budget
        """
        name = update.inline_query.from_user.name if is_inline else update.message.from_user.name
        user_id = update.inline_query.from_user.id if is_inline else update.message.from_user.id

        if not await is_allowed(self.config, update, context, is_inline=is_inline):
            logging.warning(f'User {name} (id: {user_id}) is not allowed to use the bot')
            await self.send_disallowed_message(update, context, is_inline)
            return False
        if not is_within_budget(self.config, self.usage, update, is_inline=is_inline):
            logging.warning(f'User {name} (id: {user_id}) reached their usage limit')
            await self.send_budget_reached_message(update, context, is_inline)
            return False

        return True

    async def send_disallowed_message(self, update: Update, _: ContextTypes.DEFAULT_TYPE, is_inline=False):
        """
        Sends the disallowed message to the user.
        """
        if not is_inline:
            await update.effective_message.reply_text(
                message_thread_id=get_thread_id(update),
                text=self.disallowed_message,
                disable_web_page_preview=True
            )
        else:
            result_id = str(uuid4())
            await self.send_inline_query_result(update, result_id, message_content=self.disallowed_message)

    async def send_budget_reached_message(self, update: Update, _: ContextTypes.DEFAULT_TYPE, is_inline=False):
        """
        Sends the budget reached message to the user.
        """
        if not is_inline:
            await update.effective_message.reply_text(
                message_thread_id=get_thread_id(update),
                text=self.budget_limit_message
            )
        else:
            result_id = str(uuid4())
            await self.send_inline_query_result(update, result_id, message_content=self.budget_limit_message)

    async def _capture_group_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        Silently capture group chat messages into a rolling context buffer.
        Runs in a separate handler group so it processes every group text message
        independently of the main prompt handler.
        """
        if update.edited_message or not update.message or update.message.via_bot:
            return
        if not is_group_chat(update):
            return

        max_messages = self.config.get('group_context_messages', 5)
        if max_messages <= 0:
            return

        text = message_text(update.message)
        if not text:
            return

        chat_id = update.effective_chat.id
        sender = update.message.from_user.first_name or "Unknown"

        if chat_id not in self.group_chat_messages:
            self.group_chat_messages[chat_id] = deque(maxlen=max_messages)

        self.group_chat_messages[chat_id].append(f"{sender}: {text}")

    def _get_group_context(self, chat_id: int) -> str | None:
        """
        Build a formatted context string from the rolling group chat buffer.
        Returns None if no buffered messages are available.
        """
        if chat_id not in self.group_chat_messages:
            return None

        messages = list(self.group_chat_messages[chat_id])
        if not messages:
            return None

        context_lines = "\n".join(messages)
        return (
            f"[Recent group chat messages for context:\n"
            f"{context_lines}]"
        )

    def _prefix_with_user_context(self, update: Update, prompt: str) -> str:
        """
        Prefixes a prompt with sender identity and memory context.
        In group chats: includes user_id, display name, and any stored memory.
        In DMs: includes memory context only (no name prefix needed).
        """
        user_id = update.message.from_user.id
        first_name = update.message.from_user.first_name
        display_name = self.user_memory.get_display_name(user_id) or first_name
        memory_context = self.user_memory.get_context_string(user_id)

        if is_group_chat(update):
            prefix = f"{display_name}: "
            if memory_context:
                prefix = f"{memory_context} {prefix}"
            return f"{prefix}{prompt}"
        else:
            # DM — no name prefix, but include memory if available
            if memory_context:
                return f"{memory_context}\n{prompt}"
            return prompt

    async def post_init(self, application: Application) -> None:
        """
        Post initialization hook for the bot.
        """
        await application.bot.set_my_commands(self.group_commands, scope=BotCommandScopeAllGroupChats())
        await application.bot.set_my_commands(self.commands)

        # Initialize scheduler with the application's job queue
        if self.scheduler:
            async def send_scheduled_message(chat_id: int, message: str):
                await application.bot.send_message(chat_id=chat_id, text=f"🔔 {message}")

            self.scheduler.set_application(application, send_scheduled_message)
            self.scheduler.restore_jobs()

    def run(self):
        """
        Runs the bot indefinitely until the user presses Ctrl+C
        """
        application = ApplicationBuilder() \
            .token(self.config['token']) \
            .proxy_url(self.config['proxy']) \
            .get_updates_proxy_url(self.config['proxy']) \
            .post_init(self.post_init) \
            .concurrent_updates(True) \
            .build()

        application.add_handler(CommandHandler('reset', self.reset))
        application.add_handler(CommandHandler('help', self.help))
        application.add_handler(CommandHandler('start', self.help))
        application.add_handler(CommandHandler('stats', self.stats))
        application.add_handler(CommandHandler('resend', self.resend))
        application.add_handler(CommandHandler('mymemory', self.mymemory))
        application.add_handler(CommandHandler('forgetme', self.forgetme))
        application.add_handler(CommandHandler('model', self.model))

        # Group memory & persona commands
        if self.group_memory:
            application.add_handler(CommandHandler(
                'persona', self.persona,
                filters=filters.ChatType.GROUP | filters.ChatType.SUPERGROUP
            ))
            application.add_handler(CommandHandler(
                'groupmemory', self.groupmemory,
                filters=filters.ChatType.GROUP | filters.ChatType.SUPERGROUP
            ))
            application.add_handler(CommandHandler(
                'forgetgroup', self.forgetgroup,
                filters=filters.ChatType.GROUP | filters.ChatType.SUPERGROUP
            ))

        # Scheduler commands
        if self.scheduler:
            application.add_handler(CommandHandler('remind', self.remind))
            application.add_handler(CommandHandler('schedule', self.schedule))
            application.add_handler(CommandHandler('myschedules', self.myschedules))
            application.add_handler(CommandHandler('cancelschedule', self.cancelschedule))

        application.add_handler(CommandHandler(
            'chat', self.prompt, filters=filters.ChatType.GROUP | filters.ChatType.SUPERGROUP)
        )
        application.add_handler(MessageHandler(
            filters.PHOTO | filters.Document.IMAGE,
            self.vision))
        application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), self.prompt))
        application.add_handler(
            MessageHandler(
                filters.TEXT & (~filters.COMMAND) & (filters.ChatType.GROUP | filters.ChatType.SUPERGROUP),
                self._capture_group_message
            ),
            group=-1  # Runs before main handlers to capture every group message
        )
        application.add_handler(InlineQueryHandler(self.inline_query, chat_types=[
            constants.ChatType.GROUP, constants.ChatType.SUPERGROUP, constants.ChatType.PRIVATE
        ]))
        application.add_handler(CallbackQueryHandler(self.handle_callback_inline_query))

        application.add_error_handler(error_handler)

        application.run_polling()
