import logging
import os

from dotenv import load_dotenv

from plugin_manager import PluginManager
from claude_helper import ClaudeHelper, default_max_tokens, are_functions_available
from telegram_bot import ChatGPTTelegramBot
from user_memory import UserMemory


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

    # Setup configurations
    model = os.environ.get('CLAUDE_MODEL', 'claude-sonnet-4-5-20250929')
    max_tokens_default = default_max_tokens(model=model)
    claude_config = {
        'api_key': os.environ['ANTHROPIC_API_KEY'],
        'show_usage': os.environ.get('SHOW_USAGE', 'false').lower() == 'true',
        'stream': os.environ.get('STREAM', 'true').lower() == 'true',
        'proxy': os.environ.get('PROXY', None),
        'max_history_size': int(os.environ.get('MAX_HISTORY_SIZE', 15)),
        'max_conversation_age_minutes': int(os.environ.get('MAX_CONVERSATION_AGE_MINUTES', 180)),
        'assistant_prompt': os.environ.get('ASSISTANT_PROMPT', 'You are a helpful assistant.'),
        'max_tokens': int(os.environ.get('MAX_TOKENS', max_tokens_default)),
        'temperature': float(os.environ.get('TEMPERATURE', 1.0)),
        'model': model,
        'enable_functions': os.environ.get('ENABLE_FUNCTIONS', 'true').lower() == 'true',
        'functions_max_consecutive_calls': int(os.environ.get('FUNCTIONS_MAX_CONSECUTIVE_CALLS', 10)),
        'bot_language': os.environ.get('BOT_LANGUAGE', 'en'),
        'telegram_native_stream': os.environ.get('TELEGRAM_NATIVE_STREAM', 'false').lower() == 'true',
        'show_plugins_used': os.environ.get('SHOW_PLUGINS_USED', 'false').lower() == 'true',
        'enable_vision_follow_up_questions': os.environ.get('ENABLE_VISION_FOLLOW_UP_QUESTIONS', 'true').lower() == 'true',
        'vision_prompt': os.environ.get('VISION_PROMPT', 'What is in this image'),
        'vision_max_tokens': int(os.environ.get('VISION_MAX_TOKENS', '300')),
    }

    telegram_config = {
        'token': os.environ['TELEGRAM_BOT_TOKEN'],
        'admin_user_ids': os.environ.get('ADMIN_USER_IDS', '-'),
        'allowed_user_ids': os.environ.get('ALLOWED_TELEGRAM_USER_IDS', '*'),
        'enable_quoting': os.environ.get('ENABLE_QUOTING', 'true').lower() == 'true',
        'enable_vision': os.environ.get('ENABLE_VISION', 'true').lower() == 'true',
        'budget_period': os.environ.get('BUDGET_PERIOD', 'monthly').lower(),
        'user_budgets': os.environ.get('USER_BUDGETS', os.environ.get('MONTHLY_USER_BUDGETS', '*')),
        'guest_budget': float(os.environ.get('GUEST_BUDGET', os.environ.get('MONTHLY_GUEST_BUDGET', '100.0'))),
        'stream': os.environ.get('STREAM', 'true').lower() == 'true',
        'proxy': os.environ.get('PROXY', None) or os.environ.get('TELEGRAM_PROXY', None),
        'ignore_group_vision': os.environ.get('IGNORE_GROUP_VISION', 'true').lower() == 'true',
        'group_trigger_keyword': os.environ.get('GROUP_TRIGGER_KEYWORD', ''),
        'group_context_messages': int(os.environ.get('GROUP_CONTEXT_MESSAGES', 5)),
        'token_price': float(os.environ.get('TOKEN_PRICE', 0.003)),
        'vision_token_price': float(os.environ.get('VISION_TOKEN_PRICE', '0.003')),
        'bot_language': os.environ.get('BOT_LANGUAGE', 'en'),
        'telegram_native_stream': os.environ.get('TELEGRAM_NATIVE_STREAM', 'false').lower() == 'true',
    }

    plugin_config = {
        'plugins': os.environ.get('PLUGINS', '').split(',')
    }

    # Setup and run Claude and Telegram bot
    user_memory = UserMemory()
    plugin_manager = PluginManager(config=plugin_config, user_memory=user_memory)
    claude_helper = ClaudeHelper(config=claude_config, plugin_manager=plugin_manager)
    telegram_bot = ChatGPTTelegramBot(config=telegram_config, claude=claude_helper, user_memory=user_memory)
    telegram_bot.run()


if __name__ == '__main__':
    main()
