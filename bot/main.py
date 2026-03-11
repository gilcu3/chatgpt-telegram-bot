import logging
import os

from dotenv import load_dotenv

from plugin_manager import PluginManager
from claude_helper import ClaudeHelper, default_max_tokens, are_functions_available
from telegram_bot import ChatGPTTelegramBot
from user_memory import UserMemory
from group_memory import GroupMemory
from model_router import ModelRouter
from scheduler import BotScheduler


def main():
    # Read .env file
    load_dotenv()

    # Setup logging
    logging.basicConfig(
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        level=logging.INFO
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)

    # Check if the required environment variables are set
    required_values = ['TELEGRAM_BOT_TOKEN', 'ANTHROPIC_API_KEY']
    missing_values = [value for value in required_values if os.environ.get(value) is None]
    if len(missing_values) > 0:
        logging.error(f'The following environment values are missing in your .env: {", ".join(missing_values)}')
        exit(1)

    # Validate API key format
    api_key = os.environ['ANTHROPIC_API_KEY']
    if not api_key.startswith('sk-'):
        logging.warning('ANTHROPIC_API_KEY does not start with "sk-" - verify it is correct')

    # Validate numeric configuration values
    try:
        max_history_size = int(os.environ.get('MAX_HISTORY_SIZE', 15))
        max_conversation_age_minutes = int(os.environ.get('MAX_CONVERSATION_AGE_MINUTES', 180))
        max_tokens_raw = os.environ.get('MAX_TOKENS')
        temperature = float(os.environ.get('TEMPERATURE', 1.0))
        functions_max_consecutive_calls = int(os.environ.get('FUNCTIONS_MAX_CONSECUTIVE_CALLS', 10))
        vision_max_tokens = int(os.environ.get('VISION_MAX_TOKENS', '300'))
        guest_budget = float(os.environ.get('GUEST_BUDGET', os.environ.get('MONTHLY_GUEST_BUDGET', '100.0')))
        group_context_messages = int(os.environ.get('GROUP_CONTEXT_MESSAGES', 5))
        token_price = float(os.environ.get('TOKEN_PRICE', 0.003))
        vision_token_price = float(os.environ.get('VISION_TOKEN_PRICE', '0.003'))
    except ValueError as e:
        logging.error(f'Invalid configuration value: {e}')
        exit(1)

    # Setup configurations
    model = os.environ.get('CLAUDE_MODEL', 'claude-sonnet-4-5-20250929')
    max_tokens_default = default_max_tokens(model=model)
    max_tokens = int(max_tokens_raw) if max_tokens_raw else max_tokens_default
    # Smart routing config
    enable_smart_routing = os.environ.get('ENABLE_SMART_ROUTING', 'false').lower() == 'true'

    claude_config = {
        'api_key': api_key,
        'show_usage': os.environ.get('SHOW_USAGE', 'false').lower() == 'true',
        'stream': os.environ.get('STREAM', 'true').lower() == 'true',
        'proxy': os.environ.get('PROXY', None),
        'max_history_size': max_history_size,
        'max_conversation_age_minutes': max_conversation_age_minutes,
        'assistant_prompt': os.environ.get('ASSISTANT_PROMPT', 'You are a helpful assistant.'),
        'max_tokens': max_tokens,
        'temperature': temperature,
        'model': model,
        'enable_functions': os.environ.get('ENABLE_FUNCTIONS', 'true').lower() == 'true',
        'functions_max_consecutive_calls': functions_max_consecutive_calls,
        'bot_language': os.environ.get('BOT_LANGUAGE', 'en'),
        'telegram_native_stream': os.environ.get('TELEGRAM_NATIVE_STREAM', 'false').lower() == 'true',
        'show_plugins_used': os.environ.get('SHOW_PLUGINS_USED', 'false').lower() == 'true',
        'enable_vision_follow_up_questions': os.environ.get('ENABLE_VISION_FOLLOW_UP_QUESTIONS', 'true').lower() == 'true',
        'vision_prompt': os.environ.get('VISION_PROMPT', 'What is in this image'),
        'vision_max_tokens': vision_max_tokens,
        'enable_smart_routing': enable_smart_routing,
        'show_routing_info': os.environ.get('SHOW_ROUTING_INFO', 'true').lower() == 'true',
    }

    # Scheduler config
    enable_scheduler = os.environ.get('ENABLE_SCHEDULER', 'true').lower() == 'true'
    default_timezone = os.environ.get('DEFAULT_TIMEZONE', 'UTC')

    telegram_config = {
        'token': os.environ['TELEGRAM_BOT_TOKEN'],
        'admin_user_ids': os.environ.get('ADMIN_USER_IDS', '-'),
        'allowed_user_ids': os.environ.get('ALLOWED_TELEGRAM_USER_IDS', '*'),
        'enable_quoting': os.environ.get('ENABLE_QUOTING', 'true').lower() == 'true',
        'enable_vision': os.environ.get('ENABLE_VISION', 'true').lower() == 'true',
        'budget_period': os.environ.get('BUDGET_PERIOD', 'monthly').lower(),
        'user_budgets': os.environ.get('USER_BUDGETS', os.environ.get('MONTHLY_USER_BUDGETS', '*')),
        'guest_budget': guest_budget,
        'stream': os.environ.get('STREAM', 'true').lower() == 'true',
        'proxy': os.environ.get('PROXY', None) or os.environ.get('TELEGRAM_PROXY', None),
        'ignore_group_vision': os.environ.get('IGNORE_GROUP_VISION', 'true').lower() == 'true',
        'group_trigger_keyword': os.environ.get('GROUP_TRIGGER_KEYWORD', ''),
        'group_context_messages': group_context_messages,
        'token_price': token_price,
        'vision_token_price': vision_token_price,
        'bot_language': os.environ.get('BOT_LANGUAGE', 'en'),
        'telegram_native_stream': os.environ.get('TELEGRAM_NATIVE_STREAM', 'false').lower() == 'true',
        'enable_scheduler': enable_scheduler,
    }

    plugin_config = {
        'plugins': os.environ.get('PLUGINS', '').split(',')
    }

    # Setup components
    user_memory = UserMemory()
    group_memory = GroupMemory()

    # Smart model routing (optional)
    model_router = None
    if enable_smart_routing:
        router_config = {
            'routing_haiku_model': os.environ.get('ROUTING_HAIKU_MODEL', 'claude-haiku-4-5-20251001'),
            'routing_sonnet_model': os.environ.get('ROUTING_SONNET_MODEL', 'claude-sonnet-4-5-20250929'),
            'routing_opus_model': os.environ.get('ROUTING_OPUS_MODEL', 'claude-opus-4-6'),
        }
        model_router = ModelRouter(config=router_config)
        logging.info('Smart model routing enabled')

    # Scheduler (optional)
    scheduler = None
    if enable_scheduler:
        scheduler = BotScheduler(default_timezone=default_timezone)
        logging.info('Scheduler enabled')

    # Wire everything together
    plugin_manager = PluginManager(
        config=plugin_config,
        user_memory=user_memory,
        group_memory=group_memory,
        scheduler=scheduler,
    )
    claude_helper = ClaudeHelper(
        config=claude_config,
        plugin_manager=plugin_manager,
        model_router=model_router,
        group_memory=group_memory,
    )
    telegram_bot = ChatGPTTelegramBot(
        config=telegram_config,
        claude=claude_helper,
        user_memory=user_memory,
        group_memory=group_memory,
        scheduler=scheduler,
    )
    telegram_bot.run()


if __name__ == '__main__':
    main()
