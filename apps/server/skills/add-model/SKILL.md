---
name: add-model
description: Add a custom OpenAI-compatible model (OpenRouter, Ollama, vLLM, LM Studio, etc.) to ~/.arden/models.json
---

# Add Custom Model

Help the user register a custom model in `~/.arden/models.json`. Supports both **completion** (chat) models and **embedding** models.

## Step 1: Determine model type

Ask the user whether they want to add a **completion model** or an **embedding model**.

## Completion models

### Information to collect

1. **Model ID** — a name they'll use to reference it (e.g. `openrouter/deepseek-r1`, `ollama/llama3`)
2. **Base URL** — the OpenAI-compatible API endpoint (e.g. `https://openrouter.ai/api/v1`, `http://localhost:11434/v1`)
3. **API key env var** (optional) — the environment variable holding the API key (e.g. `OPENROUTER_API_KEY`). Not needed for local models like Ollama.
4. **Context window** — max input tokens (e.g. `128000`). If the user doesn't know, suggest checking the model's docs.
5. **Max output tokens** (optional, default 8192)

### File format

Completion models are top-level keys:

```json
{
  "model-id": {
    "base_url": "https://...",
    "api_key_env": "ENV_VAR_NAME",
    "context_window": 128000,
    "max_output_tokens": 8192
  }
}
```

Only include `api_key_env` if the user provided one. Only include `max_output_tokens` if it differs from the default (8192).

### After adding

- The model is available as `model-id`
- Set it in `.env` as `ARDEN_CHAT_MODEL=model-id`, or select it in Settings
- Configure role-specific model choices in Settings; do not invent role-specific environment variables
- If they specified an `api_key_env`, remind them to set that environment variable

## Embedding models

### Information to collect

1. **Model ID** — a name they'll use to reference it (e.g. `jina-embeddings-v3`, `nomic-embed-text`)
2. **Base URL** — the OpenAI-compatible embeddings endpoint (e.g. `https://api.jina.ai/v1`)
3. **API key env var** (optional) — the environment variable holding the API key (e.g. `JINA_API_KEY`)
4. **Dimensions** — the embedding vector size (e.g. `1024`). Check the model's docs if unsure.

### File format

Embedding models go under the `"embedding"` key:

```json
{
  "embedding": {
    "model-id": {
      "base_url": "https://...",
      "api_key_env": "ENV_VAR_NAME",
      "dim": 1024
    }
  }
}
```

Only include `api_key_env` if the user provided one.

### After adding

- The model is available as `model-id`
- Set it in `.env` as `ARDEN_EMBEDDING_MODEL=model-id`
- If they specified an `api_key_env`, remind them to set that environment variable
- **Changing the embedding model triggers a full re-index** of all stored vectors

## Common presets

If the user mentions a known provider, pre-fill what you can:

- **OpenRouter**: `base_url: "https://openrouter.ai/api/v1"`, `api_key_env: "OPENROUTER_API_KEY"`
- **Ollama**: `base_url: "http://localhost:11434/v1"`, no api_key_env needed
- **vLLM**: `base_url: "http://localhost:8000/v1"`, no api_key_env needed
- **LM Studio**: `base_url: "http://localhost:1234/v1"`, no api_key_env needed
- **Together.ai**: `base_url: "https://api.together.xyz/v1"`, `api_key_env: "TOGETHER_API_KEY"`
- **Jina AI**: `base_url: "https://api.jina.ai/v1"`, `api_key_env: "JINA_API_KEY"`
- **Voyage AI**: `base_url: "https://api.voyageai.com/v1"`, `api_key_env: "VOYAGE_API_KEY"`
- **Cohere**: `base_url: "https://api.cohere.com/v2"`, `api_key_env: "COHERE_API_KEY"`

## How to write the config

1. Read `~/.arden/models.json` if it exists (it may not — create it as `{}` if missing)
2. Add the new model entry (top-level for completion, under `"embedding"` for embedding)
3. Write the file back with proper JSON formatting

## Notes

- The server needs a restart to pick up new models
- Both completion and embedding models must expose an **OpenAI-compatible** API
