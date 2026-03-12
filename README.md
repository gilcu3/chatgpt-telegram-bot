# Claude Telegram Bot
![python-version](https://img.shields.io/badge/python-3.9-blue.svg)
[![anthropic-version](https://img.shields.io/badge/anthropic-0.40.0+-blueviolet.svg)](https://www.anthropic.com/)
[![license](https://img.shields.io/badge/License-GPL%202.0-brightgreen.svg)](LICENSE)
[![Publish Docker image](https://github.com/n3d1117/chatgpt-telegram-bot/actions/workflows/publish.yaml/badge.svg)](https://github.com/n3d1117/chatgpt-telegram-bot/actions/workflows/publish.yaml)

A [Telegram bot](https://core.telegram.org/bots/api) powered by Anthropic's [Claude](https://www.anthropic.com/claude) AI models, featuring tool use (plugins), vision, streaming responses, persistent user memory, and more. Ready to use with minimal configuration required.

## Features
- [x] Support markdown in answers
- [x] Reset conversation with the `/reset` command
- [x] Typing indicator while generating a response
- [x] Access can be restricted by specifying a list of allowed users
- [x] Docker and proxy support
- [x] Automatic conversation summary to avoid excessive token usage
- [x] Track token usage per user - by [@AlexHTW](https://github.com/AlexHTW)
- [x] Get personal token usage statistics via the `/stats` command - by [@AlexHTW](https://github.com/AlexHTW)
- [x] User budgets and guest budgets - by [@AlexHTW](https://github.com/AlexHTW)
- [x] Stream support with real-time message editing
- [x] Vision support — send images and have Claude interpret them
- [x] Persistent user memory across conversations (`/mymemory`, `/forgetme`)
- [x] Async memory pipeline backed by Ollama for local fact extraction and embedding-based retrieval
- [x] Group chat support with rolling context buffer, group personas, and group memory
- [x] Claude model selection (Opus 4.6, Opus 4.5, Sonnet 4.5, Haiku 4.5) with `/model` command
- [x] Smart model routing — auto-select Haiku, Sonnet, or Opus based on query complexity
- [x] Scheduler for reminders and recurring messages (`/remind`, `/schedule`)
- [x] Image generation via Gemini or OpenAI DALL-E (`/image`)
- [x] Localized bot language
  - Available languages :brazil: :cn: :finland: :de: :indonesia: :iran: :it: :malaysia: :netherlands: :poland: :ru: :saudi_arabia: :es: :taiwan: :tr: :ukraine: :gb: :uzbekistan: :vietnam: :israel:
- [x] Support *tool use* (plugins) to extend the bot's functionality with 3rd party services
  - Weather, Spotify, web search, text-to-speech and more. See [here](#available-plugins) for a list of available plugins

## Bot Commands

#### General
| Command           | Description                                      |
|-------------------|--------------------------------------------------|
| `/help`           | Show available commands                          |
| `/reset`          | Clear conversation history                       |
| `/stats`          | View token usage and budget                      |
| `/resend`         | Resend last response                             |
| `/model`          | Switch Claude model on-the-fly                   |
| `/image`          | Generate an image from a text prompt             |

#### Memory
| Command           | Description                                      |
|-------------------|--------------------------------------------------|
| `/mymemory`       | View stored memories about you                   |
| `/forgetme`       | Clear all stored memories                        |

#### Scheduling
| Command           | Description                                      |
|-------------------|--------------------------------------------------|
| `/remind`         | Set a one-time reminder                          |
| `/schedule`       | Create a recurring scheduled message             |
| `/myschedules`    | View your active schedules                       |
| `/cancelschedule` | Cancel a scheduled message                       |

#### Group chats
| Command           | Description                                      |
|-------------------|--------------------------------------------------|
| `/chat`           | Explicitly trigger the bot in a group            |
| `/persona`        | Set a group-specific bot personality             |
| `/groupmemory`    | View group-specific stored facts                 |
| `/forgetgroup`    | Clear all group memories                         |

## Additional features - help needed!
If you'd like to help, check out the [issues](https://github.com/n3d1117/chatgpt-telegram-bot/issues) section and contribute!
If you want to help with translations, check out the [Translations Manual](https://github.com/n3d1117/chatgpt-telegram-bot/discussions/219)

PRs are always welcome!

## Prerequisites
- Python 3.9+
- A [Telegram bot](https://core.telegram.org/bots#6-botfather) and its token (see [tutorial](https://core.telegram.org/bots/tutorial#obtain-your-bot-token))
- An [Anthropic](https://www.anthropic.com) API key (see [configuration](#configuration) section)

## Getting started

### Configuration
Customize the configuration by copying `.env.example` and renaming it to `.env`, then editing the required parameters as desired:

| Parameter                   | Description                                                                                                                                                                                                                   |
|-----------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `ANTHROPIC_API_KEY`         | Your Anthropic API key, you can get it from the [Anthropic Console](https://console.anthropic.com/)                                                                                                                           |
| `TELEGRAM_BOT_TOKEN`        | Your Telegram bot's token, obtained using [BotFather](http://t.me/botfather) (see [tutorial](https://core.telegram.org/bots/tutorial#obtain-your-bot-token))                                                                  |
| `ADMIN_USER_IDS`            | Telegram user IDs of admins. These users have access to special admin commands, information and no budget restrictions. Admin IDs don't have to be added to `ALLOWED_TELEGRAM_USER_IDS`. **Note**: by default, no admin (`-`) |
| `ALLOWED_TELEGRAM_USER_IDS` | A comma-separated list of Telegram user IDs that are allowed to interact with the bot (use [getidsbot](https://t.me/getidsbot) to find your user ID). **Note**: by default, *everyone* is allowed (`*`)                       |

### Optional configuration
The following parameters are optional and can be set in the `.env` file:

#### Model and response
| Parameter                      | Description                                                                                                                       | Default value                  |
|--------------------------------|-----------------------------------------------------------------------------------------------------------------------------------|--------------------------------|
| `CLAUDE_MODEL`                 | The Claude model to use. Available: `claude-opus-4-6`, `claude-opus-4-5-20251124`, `claude-sonnet-4-5-20250929`, `claude-haiku-4-5-20251001` | `claude-sonnet-4-5-20250929`   |
| `MAX_TOKENS`                   | Upper bound on how many tokens Claude will return per response                                                                    | `4096`                         |
| `TEMPERATURE`                  | Number between 0 and 2. Higher values make the output more random                                                                 | `1.0`                          |
| `ASSISTANT_PROMPT`             | A system message that sets the tone and controls the behavior of the assistant                                                    | `You are a helpful assistant.` |
| `STREAM`                       | Whether to stream responses with real-time message editing in Telegram                                                            | `true`                         |
| `MAX_HISTORY_SIZE`             | Max number of messages to keep in memory, after which the conversation will be summarised to avoid excessive token usage           | `15`                           |
| `MAX_CONVERSATION_AGE_MINUTES` | Maximum number of minutes a conversation should live since the last message, after which it will be reset                          | `180`                          |
| `SHOW_USAGE`                   | Whether to show token usage information after each response                                                                       | `false`                        |
| `ENABLE_QUOTING`               | Whether to enable message quoting in private chats                                                                                | `true`                         |
| `PROXY`                        | Proxy to be used for Anthropic and Telegram bot (e.g. `http://localhost:8080`)                                                    | -                              |
| `BOT_LANGUAGE`                 | Language of general bot messages. Currently available: `en`, `de`, `ru`, `tr`, `it`, `fi`, `es`, `id`, `nl`, `zh-cn`, `zh-tw`, `vi`, `fa`, `pt-br`, `uk`, `ms`, `uz`, `ar`. [Contribute with additional translations](https://github.com/n3d1117/chatgpt-telegram-bot/discussions/219) | `en` |

#### Vision
| Parameter                           | Description                                                                                                                                           | Default value          |
|-------------------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------|------------------------|
| `ENABLE_VISION`                     | Whether to enable vision capabilities (image interpretation)                                                                                          | `true`                 |
| `VISION_MAX_TOKENS`                 | Upper bound on how many tokens vision responses will use                                                                                              | `300`                  |
| `VISION_PROMPT`                     | Default prompt used to interpret images when no caption is provided                                                                                   | `What is in this image`|
| `ENABLE_VISION_FOLLOW_UP_QUESTIONS` | If true, once you send an image, vision mode stays active until the conversation ends. Otherwise, regular mode resumes for follow-up messages          | `true`                 |
| `IGNORE_GROUP_VISION`               | If set to true, the bot will not process vision queries in group chats                                                                                | `true`                 |

#### Group chats
| Parameter                | Description                                                                           | Default value |
|--------------------------|---------------------------------------------------------------------------------------|---------------|
| `GROUP_TRIGGER_KEYWORD`  | If set, the bot in group chats will only respond to messages that contain this keyword     | -           |
| `GROUP_CONTEXT_MESSAGES` | Number of recent group chat messages to include as context for the bot                | `5`           |

#### Smart model routing
| Parameter              | Description                                                                                      | Default value                  |
|------------------------|--------------------------------------------------------------------------------------------------|--------------------------------|
| `ENABLE_SMART_ROUTING` | Enable automatic model selection based on query complexity (routes to Haiku, Sonnet, or Opus)    | `false`                        |
| `SHOW_ROUTING_INFO`    | Whether to display which model was selected and cost savings in each response                    | `true`                         |
| `ROUTING_HAIKU_MODEL`  | The Haiku model to use for simple queries                                                        | `claude-haiku-4-5-20251001`    |
| `ROUTING_SONNET_MODEL` | The Sonnet model to use for moderate queries                                                     | `claude-sonnet-4-5-20250929`   |
| `ROUTING_OPUS_MODEL`   | The Opus model to use for complex queries                                                        | `claude-opus-4-6`              |

#### Scheduler
| Parameter          | Description                                                                    | Default value |
|--------------------|--------------------------------------------------------------------------------|---------------|
| `ENABLE_SCHEDULER` | Enable the reminder and scheduling system (`/remind`, `/schedule` commands)    | `true`        |
| `DEFAULT_TIMEZONE` | Default timezone for scheduling (IANA timezone, e.g. `Europe/Rome`, `US/Eastern`) | `UTC`      |

#### Memory pipeline (Ollama)
The memory pipeline uses a local [Ollama](https://ollama.com) instance to extract facts from conversations and store them with embeddings for retrieval. This keeps memory processing off the Claude API (no extra cost) and runs asynchronously in the background.

| Parameter                    | Description                                                                           | Default value     |
|------------------------------|---------------------------------------------------------------------------------------|-------------------|
| `OLLAMA_BASE_URL`            | URL of your Ollama server (e.g. `http://localhost:11434`)                             | -                 |
| `OLLAMA_CHAT_MODEL`          | Ollama chat model used for fact extraction (e.g. `qwen3.5:9b`)                       | -                 |
| `OLLAMA_EMBED_MODEL`         | Ollama embedding model used for similarity search (e.g. `nomic-embed-text`)           | -                 |
| `MEMORY_TOP_N`               | Number of most similar facts to inject into the prompt                                | `10`              |
| `MEMORY_RELEVANCE_THRESHOLD` | Minimum cosine similarity score for a fact to be considered relevant (0.0–1.0)        | `0.3`             |
| `MEMORY_MAX_FACTS_PER_USER`  | Maximum number of stored facts per user                                               | `200`             |
| `MEMORY_MAX_FACTS_PER_GROUP` | Maximum number of stored facts per group                                              | `100`             |
| `MEMORY_DB_PATH`             | Path to the SQLite database for memory storage                                        | `memory/memory.db`|

When Ollama is not configured or unreachable, the bot continues to work normally without memory features.

#### Budgets
| Parameter            | Description                                                                                                                                                                                                                                                                                                                                                                               | Default value |
|----------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|---------------|
| `BUDGET_PERIOD`      | Determines the time frame all budgets are applied to. Available periods: `daily` *(resets budget every day)*, `monthly` *(resets budgets on the first of each month)*, `all-time` *(never resets budget)*. See the [Budget Manual](https://github.com/n3d1117/chatgpt-telegram-bot/discussions/184) for more information                                                                  | `monthly`     |
| `USER_BUDGETS`       | A comma-separated list of $-amounts per user from list `ALLOWED_TELEGRAM_USER_IDS` to set custom usage limit of API costs for each. For `*`-user lists the first `USER_BUDGETS` value is given to every user. **Note**: by default, *no limits* for any user (`*`). See the [Budget Manual](https://github.com/n3d1117/chatgpt-telegram-bot/discussions/184) for more information | `*`           |
| `GUEST_BUDGET`       | $-amount as usage limit for all guest users. Guest users are users in group chats that are not in the `ALLOWED_TELEGRAM_USER_IDS` list. Value is ignored if no usage limits are set in user budgets (`USER_BUDGETS`=`*`). See the [Budget Manual](https://github.com/n3d1117/chatgpt-telegram-bot/discussions/184) for more information                                                   | `100.0`       |
| `TOKEN_PRICE`        | $-price per 1000 tokens used to compute cost information in usage statistics                                                                                                                                                                                                                                                                                                              | `0.003`       |
| `VISION_TOKEN_PRICE` | $-price per 1000 vision tokens used to compute cost information                                                                                                                                                                                                                                                                                                                           | `0.003`       |

Check out the [Budget Manual](https://github.com/n3d1117/chatgpt-telegram-bot/discussions/184) for possible budget configurations.

#### Tool use (plugins)
| Parameter                         | Description                                                                                      | Default value                       |
|-----------------------------------|--------------------------------------------------------------------------------------------------|-------------------------------------|
| `ENABLE_FUNCTIONS`                | Whether to enable tool use (plugins). All Claude models support tool use                         | `true`                              |
| `FUNCTIONS_MAX_CONSECUTIVE_CALLS` | Maximum number of back-to-back tool calls to be made by the model in a single response           | `10`                                |
| `PLUGINS`                         | List of plugins to enable (see below for a full list), e.g: `PLUGINS=wolfram,weather`            | -                                   |
| `SHOW_PLUGINS_USED`               | Whether to show which plugins were used for a response                                           | `false`                             |

#### Available plugins
| Name                      | Description                                                                                                                                         | Required environment variable(s)                                     | Dependency          |
|---------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------|----------------------------------------------------------------------|---------------------|
| `weather`                 | Daily weather and 7-day forecast for any location (powered by [Open-Meteo](https://open-meteo.com))                                                 | -                                                                    |                     |
| `wolfram`                 | WolframAlpha queries (powered by [WolframAlpha](https://www.wolframalpha.com))                                                                      | `WOLFRAM_APP_ID`                                                     | `wolframalpha`      |
| `ddg_web_search`          | Web search (powered by [DuckDuckGo](https://duckduckgo.com))                                                                                        | -                                                                    | `duckduckgo_search` |
| `ddg_image_search`        | Search image or GIF (powered by [DuckDuckGo](https://duckduckgo.com))                                                                               | -                                                                    | `duckduckgo_search` |
| `crypto`                  | Live cryptocurrencies rate (powered by [CoinCap](https://coincap.io)) - by [@stumpyfr](https://github.com/stumpyfr)                                 | -                                                                    |                     |
| `spotify`                 | Spotify top tracks/artists, currently playing song and content search (powered by [Spotify](https://spotify.com)). Requires one-time authorization. | `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET`, `SPOTIFY_REDIRECT_URI` | `spotipy`           |
| `worldtimeapi`            | Get latest world time (powered by [WorldTimeAPI](https://worldtimeapi.org/)) - by [@noriellecruz](https://github.com/noriellecruz)                  | `WORLDTIME_DEFAULT_TIMEZONE`                                         |                     |
| `dice`                    | Send a dice in the chat!                                                                                                                            | -                                                                    |                     |
| `youtube_audio_extractor` | Extract audio from YouTube videos                                                                                                                   | -                                                                    | `pytube`            |
| `deepl_translate`         | Translate text to any language (powered by [DeepL](https://deepl.com)) - by [@LedyBacer](https://github.com/LedyBacer)                              | `DEEPL_API_KEY`                                                      |                     |
| `gtts_text_to_speech`     | Text to speech (powered by Google Translate APIs)                                                                                                   | -                                                                    | `gtts`              |
| `whois`                   | Query the whois domain database - by [@jnaskali](https://github.com/jnaskali)                                                                       | -                                                                    | `whois`             |
| `webshot`                 | Screenshot a website from a given url or domain name - by [@noriellecruz](https://github.com/noriellecruz)                                          | -                                                                    |                     |
| `iplocation`              | Look up the geographic location of an IP address                                                                                                    | -                                                                    |                     |
| `url_content`             | Fetch and extract text content from a URL for reading, summarizing, or answering questions about webpages                                           | -                                                                    | `beautifulsoup4`    |
| `auto_tts`                | Text to speech using OpenAI APIs - by [@Jipok](https://github.com/Jipok)                                                                            | -                                                                    |                     |
| `image_generation`        | Generate images from text prompts using Gemini or OpenAI DALL-E                                                                                      | `GEMINI_API_KEY` or `OPENAI_IMAGE_API_KEY`                           | `google-genai`      |
| `explicit_memory`         | Explicit memory management — lets users ask the bot to remember or forget specific facts (requires Ollama memory pipeline)                            | `OLLAMA_BASE_URL`, `OLLAMA_EMBED_MODEL`                              |                     |
| `scheduler`               | Conversational reminders and recurring schedules — lets Claude set reminders during chat (requires `ENABLE_SCHEDULER=true`)                          | -                                                                    |                     |

#### Plugin environment variables
| Variable                          | Description                                                                                                                                                                                     | Default value |
|-----------------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|---------------|
| `WOLFRAM_APP_ID`                  | Wolfram Alpha APP ID (required only for the `wolfram` plugin, you can get one [here](https://products.wolframalpha.com/simple-api/documentation))                                               | -             |
| `SPOTIFY_CLIENT_ID`               | Spotify app Client ID (required only for the `spotify` plugin, you can find it on the [dashboard](https://developer.spotify.com/dashboard/))                                                    | -             |
| `SPOTIFY_CLIENT_SECRET`           | Spotify app Client Secret (required only for the `spotify` plugin, you can find it on the [dashboard](https://developer.spotify.com/dashboard/))                                                | -             |
| `SPOTIFY_REDIRECT_URI`            | Spotify app Redirect URI (required only for the `spotify` plugin, you can find it on the [dashboard](https://developer.spotify.com/dashboard/))                                                 | -             |
| `WORLDTIME_DEFAULT_TIMEZONE`      | Default timezone to use, i.e. `Europe/Rome` (required only for the `worldtimeapi` plugin, you can get TZ Identifiers from [here](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones)) | -             |
| `DUCKDUCKGO_SAFESEARCH`           | DuckDuckGo safe search (`on`, `off` or `moderate`) (optional, applies to `ddg_web_search` and `ddg_image_search`)                                                                               | `moderate`    |
| `DEEPL_API_KEY`                   | DeepL API key (required for the `deepl` plugin, you can get one [here](https://www.deepl.com/pro-api?cta=header-pro-api))                                                                       | -             |
| `IMAGE_GENERATION_PROVIDER`       | Image generation provider: `gemini` or `openai` (required only for the `image_generation` plugin)                                                                                                | `gemini`      |
| `GEMINI_API_KEY`                  | Google Gemini API key (required for Gemini image generation, get one from [Google AI Studio](https://aistudio.google.com/apikey))                                                                 | -             |
| `OPENAI_IMAGE_API_KEY`            | OpenAI API key for DALL-E image generation (required when using OpenAI provider)                                                                                                                 | -             |

### Installing
Clone the repository and navigate to the project directory:

```shell
git clone https://github.com/n3d1117/chatgpt-telegram-bot.git
cd chatgpt-telegram-bot
```

#### From Source
1. Create a virtual environment:
```shell
python -m venv venv
```

2. Activate the virtual environment:
```shell
# For Linux or macOS:
source venv/bin/activate

# For Windows:
venv\Scripts\activate
```

3. Install the dependencies using `requirements.txt` file:
```shell
pip install -r requirements.txt
```

4. Use the following command to start the bot:
```
python bot/main.py
```

#### Using Docker Compose

Run the following command to build and run the Docker image:
```shell
docker compose up
```

#### Ready-to-use Docker images
You can also use the Docker image from [Docker Hub](https://hub.docker.com/r/n3d1117/chatgpt-telegram-bot):
```shell
docker pull n3d1117/chatgpt-telegram-bot:latest
docker run -it --env-file .env n3d1117/chatgpt-telegram-bot
```

or using the [GitHub Container Registry](https://github.com/n3d1117/chatgpt-telegram-bot/pkgs/container/chatgpt-telegram-bot/):

```shell
docker pull ghcr.io/n3d1117/chatgpt-telegram-bot:latest
docker run -it --env-file .env ghcr.io/n3d1117/chatgpt-telegram-bot
```

#### Docker manual build
```shell
docker build -t chatgpt-telegram-bot .
docker run -it --env-file .env chatgpt-telegram-bot
```

#### Heroku
Here is an example of `Procfile` for deploying using Heroku (thanks [err09r](https://github.com/err09r)!):
```
worker: python -m venv venv && source venv/bin/activate && pip install -r requirements.txt && python bot/main.py
```

## Credits
- [Claude](https://www.anthropic.com/claude) from [Anthropic](https://www.anthropic.com)
- [python-telegram-bot](https://python-telegram-bot.org)

## Disclaimer
This is a personal project and is not affiliated with Anthropic in any way.

## License
This project is released under the terms of the GPL 2.0 license. For more information, see the [LICENSE](LICENSE) file included in the repository.
