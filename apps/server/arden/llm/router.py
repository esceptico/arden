from itertools import chain

from arden.llm.anthropic import AnthropicClient
from arden.llm.base import CompletionClient, EmbeddingClient
from arden.llm.gemini import GeminiClient
from arden.llm.models import Provider, get_embedding_model, get_embedding_models, get_model, get_models
from arden.llm.openai import OpenAIClient
from arden.llm.openai_codex import OpenAICodexClient
from arden.settings import load_user_settings

_completion_clients: dict[str, CompletionClient] = {}
_embedding_clients: dict[str, EmbeddingClient] = {}
_api_keys: dict[Provider | str, str | None] = {}
_stale_clients: list[CompletionClient] = []


def init(config) -> None:
    _completion_clients.clear()
    _embedding_clients.clear()
    _api_keys.clear()
    _api_keys[Provider.ANTHROPIC] = config.anthropic_api_key
    _api_keys[Provider.OPENAI] = config.openai_api_key
    _api_keys[Provider.GOOGLE] = config.gemini_api_key
    _api_keys[Provider.OPENROUTER] = config.openrouter_api_key

    settings = load_user_settings()
    custom_keys = settings.get("custom_model_keys", {})
    for model_id, key in custom_keys.items():
        _api_keys[model_id] = key

    # Custom model files may name an environment variable instead of storing a key.
    for model in chain(get_models().values(), get_embedding_models().values()):
        if model.provider == Provider.CUSTOM and model.api_key_env and model.id not in _api_keys:
            _api_keys[model.id] = config.model_extra.get(model.api_key_env.lower())


def get_completion_client(model_id: str) -> CompletionClient:
    model = get_model(model_id)
    cache_key = _completion_client_cache_key(model)
    if cache_key not in _completion_clients:
        _completion_clients[cache_key] = create_completion_client(model_id)
    return _completion_clients[cache_key]


def _completion_client_cache_key(model) -> str:
    if model.provider == Provider.ANTHROPIC:
        return "anthropic"
    return model.id if model.provider == Provider.CUSTOM else (model.base_url or model.provider.value)


def create_completion_client(model_id: str) -> CompletionClient:
    model = get_model(model_id)
    if model.provider == Provider.ANTHROPIC:
        return AnthropicClient(api_key=_api_keys.get(Provider.ANTHROPIC))
    key = _api_keys.get(model.provider)
    match model.provider:
        case Provider.OPENAI:
            return OpenAIClient(api_key=key)
        case Provider.OPENAI_CODEX:
            return OpenAICodexClient()
        case Provider.GOOGLE:
            return GeminiClient(api_key=key)
        case Provider.OPENROUTER:
            return OpenAIClient(base_url="https://openrouter.ai/api/v1", api_key=key, native_openai=False)
        case Provider.CUSTOM:
            return OpenAIClient(
                base_url=model.base_url, api_key=_api_keys.get(model.id) or "unused", native_openai=False
            )
        case _:
            raise ValueError(f"Unknown provider: {model.provider}")


def get_embedding_client(model_id: str) -> EmbeddingClient:
    model = get_embedding_model(model_id)
    cache_key = model.id if model.provider == Provider.CUSTOM else model.provider.value
    if cache_key not in _embedding_clients:
        key = _api_keys.get(model.provider)
        match model.provider:
            case Provider.OPENAI:
                _embedding_clients[cache_key] = OpenAIClient(api_key=key)
            case Provider.GOOGLE:
                _embedding_clients[cache_key] = GeminiClient(api_key=key)
            case Provider.CUSTOM:
                _embedding_clients[cache_key] = OpenAIClient(
                    base_url=model.base_url, api_key=_api_keys.get(model.id) or "unused"
                )
            case _:
                raise ValueError(f"Provider {model.provider} does not support embeddings")
    return _embedding_clients[cache_key]


async def reset() -> None:
    """Drain previous stale clients, then move active clients to stale list."""
    for client in _stale_clients:
        await client.close()
    _stale_clients.clear()
    _stale_clients.extend(_completion_clients.values())
    _stale_clients.extend(_embedding_clients.values())
    _completion_clients.clear()
    _embedding_clients.clear()


async def close() -> None:
    for client in _stale_clients:
        await client.close()
    _stale_clients.clear()
    for client in _completion_clients.values():
        await client.close()
    for client in _embedding_clients.values():
        await client.close()
    _completion_clients.clear()
    _embedding_clients.clear()
