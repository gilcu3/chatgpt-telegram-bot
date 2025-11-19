# CLAUDE.md - ChatGPT Telegram Bot

## Project Overview

This is a Python-based Telegram bot that integrates with OpenAI's official APIs (ChatGPT, DALL-E, and Whisper) to provide a conversational AI assistant, image generation, and audio transcription capabilities through Telegram.

**Key Technologies:**
- Python 3.9+
- OpenAI API (ChatGPT, DALL-E, Whisper)
- python-telegram-bot library
- Plugin system for extensibility
- Docker support

**Repository:** https://github.com/n3d1117/chatgpt-telegram-bot

## Architecture

### Core Components

1. **bot/main.py** - Entry point
   - Loads environment variables from `.env`
   - Initializes OpenAI helper and Telegram bot
   - Sets up logging and validates configuration

2. **bot/telegram_bot.py** - `ChatGPTTelegramBot` class
   - Handles all Telegram interactions
   - Command handlers (`/help`, `/reset`, `/stats`, `/image`, `/tts`, etc.)
   - Message processing and response streaming
   - User authentication and budget enforcement

3. **bot/openai_helper.py** - `OpenAIHelper` class
   - Manages OpenAI API interactions
   - Conversation history management
   - Image generation (DALL-E)
   - Audio transcription (Whisper)
   - Text-to-speech (TTS)
   - Vision capabilities (GPT-4o with images)

4. **bot/plugin_manager.py** - `PluginManager` class
   - Loads and manages plugins based on configuration
   - Provides function specs to OpenAI for function calling
   - Routes function calls to appropriate plugins

5. **bot/usage_tracker.py** - `UsageTracker` class
   - Tracks token and API usage per user
   - Enforces budget limits
   - Provides usage statistics

6. **bot/utils.py** - Utility functions
   - Permission checking
   - Budget validation
   - Message formatting
   - Error handling

### Plugin System

Plugins extend the bot's functionality using OpenAI's function calling feature. Available plugins:

- **weather** - Daily weather and forecasts (Open-Meteo)
- **wolfram** - WolframAlpha queries
- **ddg_web_search** - Web search (DuckDuckGo)
- **ddg_image_search** - Image/GIF search (DuckDuckGo)
- **crypto** - Cryptocurrency rates (CoinCap)
- **spotify** - Spotify integration
- **worldtimeapi** - World time queries
- **dice** - Send dice in chat
- **youtube_audio_extractor** - Extract audio from YouTube
- **deepl_translate** - Text translation (DeepL)
- **gtts_text_to_speech** - Text-to-speech (Google TTS)
- **whois** - WHOIS domain queries
- **webshot** - Website screenshots
- **auto_tts** - OpenAI TTS integration
- **iplocation** - IP geolocation

Each plugin is in `bot/plugins/` and inherits from `bot/plugins/plugin.py`.

## File Structure

```
chatgpt-telegram-bot/
├── bot/
│   ├── main.py                 # Entry point
│   ├── telegram_bot.py         # Telegram bot logic
│   ├── openai_helper.py        # OpenAI API wrapper
│   ├── plugin_manager.py       # Plugin management
│   ├── usage_tracker.py        # Usage tracking and budgets
│   ├── utils.py                # Utility functions
│   └── plugins/                # Plugin implementations
│       ├── plugin.py           # Base plugin class
│       ├── weather.py
│       ├── wolfram_alpha.py
│       ├── ddg_web_search.py
│       └── ...
├── .env.example                # Environment variables template
├── requirements.txt            # Python dependencies
├── Dockerfile                  # Docker image definition
├── docker-compose.yml          # Docker Compose configuration
├── translations.json           # Bot message translations
├── README.md                   # User documentation
└── LICENSE                     # GPL 2.0 license

```

## Configuration System

### Environment Variables

Configuration is managed through a `.env` file (copy from `.env.example`):

**Required:**
- `OPENAI_API_KEY` - OpenAI API key
- `TELEGRAM_BOT_TOKEN` - Telegram bot token from @BotFather

**User Management:**
- `ADMIN_USER_IDS` - Comma-separated admin user IDs (or `-` for none)
- `ALLOWED_TELEGRAM_USER_IDS` - Allowed users (or `*` for all)

**Budget Control:**
- `BUDGET_PERIOD` - `daily`, `monthly`, or `all-time`
- `USER_BUDGETS` - Per-user budget limits
- `GUEST_BUDGET` - Budget for non-allowed users in groups
- Various pricing configs (`TOKEN_PRICE`, `IMAGE_PRICES`, etc.)

**Model Configuration:**
- `OPENAI_MODEL` - Default: `gpt-4o`
- `OPENAI_BASE_URL` - For unofficial OpenAI-compatible APIs
- `ASSISTANT_PROMPT` - System prompt
- `MAX_TOKENS`, `TEMPERATURE`, `PRESENCE_PENALTY`, `FREQUENCY_PENALTY`

**Feature Flags:**
- `ENABLE_IMAGE_GENERATION` - Enable `/image` command
- `ENABLE_TRANSCRIPTION` - Enable audio/video transcription
- `ENABLE_TTS_GENERATION` - Enable `/tts` command
- `ENABLE_VISION` - Enable vision capabilities
- `ENABLE_FUNCTIONS` - Enable plugin system
- `STREAM` - Stream responses

**Plugin Configuration:**
- `PLUGINS` - Comma-separated list (e.g., `weather,wolfram,ddg_web_search`)
- Plugin-specific env vars (e.g., `WOLFRAM_APP_ID`, `SPOTIFY_CLIENT_ID`)

**Other:**
- `BOT_LANGUAGE` - Available: en, de, ru, tr, it, fi, es, id, nl, zh-cn, zh-tw, vi, fa, pt-br, uk, ms, uz, ar
- `GROUP_TRIGGER_KEYWORD` - Keyword to trigger bot in groups
- `PROXY`, `OPENAI_PROXY`, `TELEGRAM_PROXY` - Proxy settings

See `.env.example` and `README.md` for complete configuration options.

## Common Development Tasks

### Adding a New Plugin

1. Create a new file in `bot/plugins/` (e.g., `my_plugin.py`)
2. Inherit from `Plugin` class in `bot/plugins/plugin.py`
3. Implement `get_spec()` to return OpenAI function spec
4. Implement `execute()` to handle the function call
5. Add the plugin to `PluginManager` in `bot/plugin_manager.py`
6. Add required dependencies to `requirements.txt`
7. Document the plugin in `README.md`

Example structure:
```python
from plugins.plugin import Plugin

class MyPlugin(Plugin):
    def get_spec(self) -> list[dict]:
        return [{
            "type": "function",
            "function": {
                "name": "my_function",
                "description": "Does something useful",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "param": {"type": "string", "description": "A parameter"}
                    },
                    "required": ["param"]
                }
            }
        }]

    async def execute(self, function_name, helper, **kwargs) -> dict:
        # Implementation
        return {"result": "success"}
```

### Adding a New Bot Command

1. Add command to `self.commands` in `ChatGPTTelegramBot.__init__()` in `bot/telegram_bot.py`
2. Create handler method (e.g., `async def my_command(self, update, context)`)
3. Register handler in `run()` method: `application.add_handler(CommandHandler('mycommand', self.my_command))`
4. Add localized descriptions to `translations.json`

### Modifying Conversation Behavior

Edit `bot/openai_helper.py`:
- `get_chat_response()` - Main chat logic
- `get_chat_response_stream()` - Streaming responses
- Conversation history is stored in `self.conversations` dict

### Testing Locally

1. Copy `.env.example` to `.env` and configure
2. Create a test Telegram bot via @BotFather
3. Install dependencies: `pip install -r requirements.txt`
4. Run: `python bot/main.py`

### Docker Development

```bash
# Build and run with Docker Compose
docker compose up

# Or build manually
docker build -t chatgpt-telegram-bot .
docker run -it --env-file .env chatgpt-telegram-bot
```

## Important Implementation Details

### Conversation Management

- Conversations are stored in memory per user/chat
- History is limited by `MAX_HISTORY_SIZE` (default: 15 messages)
- Conversations auto-reset after `MAX_CONVERSATION_AGE_MINUTES` (default: 180)
- When history limit is reached, conversation is summarized to save tokens

### Budget System

- Budgets are tracked per user using `UsageTracker`
- Supports daily, monthly, or all-time budget periods
- Admin users (`ADMIN_USER_IDS`) have no budget restrictions
- Guest users (in groups, not in allowed list) share `GUEST_BUDGET`
- Costs calculated based on token usage and API pricing configs

### Streaming Responses

- When `STREAM=true`, responses are streamed token-by-token
- Updates message every 20 tokens or at sentence boundaries
- Incompatible with `N_CHOICES > 1`

### Group Chat Behavior

- Requires `GROUP_TRIGGER_KEYWORD` or explicit `/chat` command
- Can ignore transcriptions/vision in groups via config
- Different command set for groups vs. private chats

### Vision Support

- Enabled with `ENABLE_VISION=true`
- Uses `VISION_MODEL` (default: `gpt-4o`)
- `ENABLE_VISION_FOLLOW_UP_QUESTIONS` determines if vision model continues after image

### Error Handling

- Retry logic for rate limits and timeouts
- Error messages are localized
- Cleanup of intermediate files (audio conversions, etc.)

## Code Style and Conventions

- Python 3.9+ with type hints where appropriate
- Async/await for all I/O operations
- Logging via Python's `logging` module
- Configuration accessed via `self.config` dict
- Localized strings via `localized_text()` function

## Debugging Tips

1. **Enable detailed logging:**
   - Logging is configured in `bot/main.py`
   - Set level to `logging.DEBUG` for verbose output

2. **Test with specific user IDs:**
   - Use `ALLOWED_TELEGRAM_USER_IDS` to restrict access during testing
   - Use @getidsbot on Telegram to find your user ID

3. **Test plugins individually:**
   - Enable only one plugin at a time via `PLUGINS` env var
   - Check plugin-specific env vars are set correctly

4. **Monitor token usage:**
   - Set `SHOW_USAGE=true` to see token counts after responses
   - Use `/stats` command to check cumulative usage

5. **Test with different models:**
   - Change `OPENAI_MODEL` to test GPT-3.5, GPT-4, etc.
   - Some features (functions, vision) require specific models

## Deployment Considerations

- **Environment variables:** Never commit `.env` to version control
- **Docker:** Use official image from Docker Hub or build from source
- **Heroku:** See `Procfile` example in README
- **Proxies:** Configure if running in restricted networks
- **Monitoring:** Set up logging aggregation for production
- **Costs:** Monitor OpenAI API usage and set appropriate budgets
- **Security:** Restrict `ALLOWED_TELEGRAM_USER_IDS` to prevent abuse

## Localization

Bot messages are localized via `translations.json`. To add a new language:

1. Add language code to `translations.json`
2. Translate all message keys
3. Set `BOT_LANGUAGE` in `.env`
4. Update README with new language support

See: https://github.com/n3d1117/chatgpt-telegram-bot/discussions/219

## Dependencies

Key Python packages (see `requirements.txt`):
- `openai==1.58.1` - OpenAI API client
- `python-telegram-bot==21.9` - Telegram bot framework
- `tiktoken==0.7.0` - Token counting for OpenAI models
- `pydub~=0.25.1` - Audio processing
- `Pillow~=11.0.0` - Image processing
- `python-dotenv~=1.0.0` - Environment variable management
- Plugin-specific: `wolframalpha`, `duckduckgo_search`, `spotipy`, `pytube`, `gtts`, `whois`

## Contributing Guidelines

- Follow existing code structure and patterns
- Add tests for new features (if applicable)
- Update README.md with new configuration options
- Update translations.json for new user-facing messages
- Keep dependencies minimal and up-to-date
- Use meaningful commit messages
- Test with multiple models and configurations

## License

GPL 2.0 - See LICENSE file

## Additional Resources

- OpenAI API Documentation: https://platform.openai.com/docs/
- python-telegram-bot Documentation: https://docs.python-telegram-bot.org/
- Telegram Bot API: https://core.telegram.org/bots/api
- Budget Manual: https://github.com/n3d1117/chatgpt-telegram-bot/discussions/184
- Translations Manual: https://github.com/n3d1117/chatgpt-telegram-bot/discussions/219
