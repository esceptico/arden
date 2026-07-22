# Setup Guide

## Requirements

- Python 3.13+
- [uv](https://docs.astral.sh/uv/) or pip
- [Bun](https://bun.sh/) (for the desktop app from source)

## Install

```bash
uv tool install arden    # or: pip install arden
```

## Quick Start

Create `~/.arden/.env` or repo-root `.env` with at least one LLM provider key and the model variables, or connect OpenAI Codex with browser sign-in from the desktop app. See [.env.example](../../.env.example) for all options.

```bash
mkdir -p ~/.arden
cp .env.example .env   # if developing from source
# or create ~/.arden/.env manually with your keys
```

```bash
arden-server serve   # starts backend, prints a one-time API key
cd apps/desktop && bun run dev  # desktop client (separate terminal) – paste the key on first launch
```

Config priority: environment variables > CWD `.env` > `~/.arden/.env` > defaults.

## OpenAI Account Sign-In

OpenAI can be used in two ways:

- `OPENAI_API_KEY` for normal platform API billing and embeddings.
- OpenAI Codex browser sign-in for account/subscription-backed models.

Choose **OpenAI Codex** in provider onboarding, `/connect`, or `/settings`. arden starts a local callback server on `localhost:1455`, opens the OpenAI authorization page, and stores refreshable tokens in `~/.arden/openai-codex-auth.json`.

Default Codex models:

```
chat   openai-codex/gpt-5.5
memory openai-codex/gpt-5.4-mini
```

For GPT reasoning models with tools, Arden uses the Responses API request shape internally. This avoids the `reasoning_effort` + function tools limitation in Chat Completions.

## Custom Models

You can use any OpenAI-compatible model (OpenRouter, Ollama, vLLM, LM Studio, etc.) by defining it in `~/.arden/models.json`:

```json
{
  "deepseek/deepseek-r1": {
    "base_url": "https://openrouter.ai/api/v1",
    "api_key_env": "OPENROUTER_API_KEY",
    "context_window": 128000,
    "max_output_tokens": 8192
  },
  "ollama/llama3": {
    "base_url": "http://localhost:11434/v1",
    "context_window": 8192
  }
}
```

Each model needs:
- `base_url` — the OpenAI-compatible API endpoint
- `context_window` — max input tokens (used for context compression thresholds)
- `api_key_env` (optional) — name of the environment variable holding the API key
- `max_output_tokens` (optional, default 8192)
- `price_in`, `price_out` (optional) — cost per million tokens, for usage tracking

Then use the model ID in your `.env`:

```
ARDEN_CHAT_MODEL=deepseek/deepseek-r1
```

Custom models appear in the settings UI alongside built-in models.

## Google Gmail & Calendar

Recommended path: use **Settings → Integrations → Gmail / Google Calendar → Run setup assistant**.

The assistant supports:

- Service choice: email-only, email+calendar, calendar-only, or all current Google scopes.
- Importing Google OAuth Desktop app credentials by file path.
- Pasting the credentials JSON directly.
- Preflight validation for missing/wrong credentials.
- Better OAuth/setup error messages for missing credentials, test-user denial, API disabled/403, redirect/client mismatch, and missing scopes.

Manual path:

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create or select a project.
3. Enable the APIs you need:
   - **Gmail API** for Gmail tools.
   - **Google Calendar API** for Calendar tools.
4. Configure **APIs & Services → OAuth consent screen**.
   - Add yourself as a test user while the app is in Testing.
5. Create **OAuth client ID → Desktop app** credentials.
6. Save/import the JSON as `~/.arden/gmail_credentials.json`.
7. Enable in `.env` as needed:

```
ARDEN_GMAIL=true
ARDEN_CALENDAR=true
```

Account add still uses Google's local browser OAuth flow. Tokens are saved under `~/.arden/` as `gmail_token_<email>.json` or `calendar_token*.json` and refresh automatically.

Google note: arden intentionally uses BYO OAuth credentials for now. Gmail read/send scopes are restricted Google scopes, so a shared public Google OAuth app would require Google's verification/security-assessment process.

## Telegram Notifications

Used for scheduled task notifications.

### 1. Create a bot

- Message [@BotFather](https://t.me/BotFather) on Telegram
- Send `/newbot` and follow the prompts
- Copy the bot token

### 2. Get your user ID

- Message [@userinfobot](https://t.me/userinfobot) on Telegram
- It replies with your user ID

### 3. Configure

In your `.env`:

```
TELEGRAM_BOT_TOKEN=your-bot-token
```

When creating a scheduled task with notifications, select Telegram as the channel and provide your user ID.

## Web Search

Web search is available out of the box via DuckDuckGo (DDGS). For higher-quality results, configure Exa:

- Sign up at [exa.ai](https://exa.ai)
- Get your API key from the dashboard
- Add to `.env`:

```
EXA_API_KEY=your-key
```

Control the provider with `WEB_SEARCH` (`auto` | `exa` | `ddgs` | `none`). Default `auto` prefers Exa when `EXA_API_KEY` is set, otherwise falls back to DDGS.

## Browser History

Reads local browser history for context. macOS only.

```
ARDEN_BROWSER=chrome    # or: safari, arc
ARDEN_BROWSER_DAYS=30   # how far back to look
```

## Context Compaction

Controls when and how conversation context is compressed to stay within model limits. Adjustable in **Settings > Limits** or via `PATCH /config`.

| Setting | Key | Default | Range |
|---|---|---|---|
| Compact trigger | `compression_threshold` | `0.8` | 0.5–1.0 (fraction of model context window) |
| Max messages | `max_messages` | `120` | 20–500 |
| Keep ratio | `compression_keep_ratio` | `0.2` | 0.1–0.8 (fraction of recent messages kept) |
| Summary tokens | `summary_max_tokens` | `1500` | 500–4000 |
| Consolidation interval | `consolidation_interval` | `30` | Minutes between memory consolidation runs |

Compaction triggers when either the message count exceeds `max_messages` or actual input tokens exceed `compression_threshold` × model context limit. The most recent `compression_keep_ratio` fraction of messages is preserved, and older messages are replaced with an LLM-generated summary capped at `summary_max_tokens`.

## Deferred Tools

`ARDEN_DEFERRED_TOOLS=true` by default. Deferred loading keeps infrequent tool schemas out of the prompt until needed. The model always sees `load_tools`; Gmail, calendar, Slack, automation, background task, notification, directive, and MCP tools are loaded by group on demand.

Compaction unloads deferred tools so a long session does not keep carrying stale schemas forever. The sidebar context box shows visible/total tools plus loaded/deferred counts for the active run.

## Docker

```bash
cp .env.example .env   # configure your keys
docker compose up -d
```

The Compose file stays at the repo root because it owns root `.env` interpolation and service orchestration. The backend image definition lives at `apps/server/Dockerfile`.

Data (sessions, memory, search index) is persisted in the `arden-data` volume, mapped to `~/.arden` inside the container. The server runs as a non-root user and is available at `http://localhost:6877` (or `ARDEN_PORT`).

Gmail and Calendar tokens are stored in `~/.arden/` (covered by the data volume). Browser history is not available in Docker.

## API Authentication

The server generates and stores a hashed API key on first run. The plaintext key is printed once — enter it in the desktop client connection screen:

```bash
cd apps/desktop && bun run dev   # paste the key in the connection screen
```

To regenerate the key:

```bash
arden-server serve --reset-key
```

All API requests require `Authorization: Bearer <key>`.
