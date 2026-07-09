import json
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path

from ntrp.logging import get_logger
from ntrp.usage import Pricing

_logger = get_logger(__name__)

_models_dir: Path | None = None


def _models_path() -> Path:
    if _models_dir is None:
        raise RuntimeError("load_custom_models() must be called before accessing models path")
    return _models_dir / "models.json"


def _read_models_json() -> dict | None:
    path = _models_path()
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text())
        return raw if isinstance(raw, dict) else None
    except (json.JSONDecodeError, OSError):
        _logger.warning("Failed to read %s", path, exc_info=True)
        return None


class Provider(Enum):
    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    OPENAI_CODEX = "openai-codex"
    GOOGLE = "google"
    OPENROUTER = "openrouter"
    CUSTOM = "custom"


@dataclass(frozen=True)
class Model:
    id: str
    provider: Provider
    max_context_tokens: int
    max_output_tokens: int = 8192
    pricing: Pricing = field(default_factory=lambda: Pricing(0, 0))
    base_url: str | None = None
    api_key_env: str | None = None
    reasoning_efforts: tuple[str, ...] = ()


def _generated_models_path() -> Path:
    return Path(__file__).with_name("generated_models.json")


def _model_from_generated_entry(entry: dict) -> Model:
    return Model(
        id=entry["id"],
        provider=Provider(entry["provider"]),
        max_context_tokens=int(entry["context_window"]),
        max_output_tokens=int(entry.get("max_output_tokens", 8192)),
        pricing=Pricing(
            price_in=float(entry.get("price_in", 0)),
            price_out=float(entry.get("price_out", 0)),
            price_cache_read=float(entry.get("price_cache_read", 0)),
            price_cache_write=float(entry.get("price_cache_write", 0)),
        ),
        reasoning_efforts=tuple(entry.get("reasoning_efforts", ())),
    )


def _load_generated_models() -> list[Model]:
    path = _generated_models_path()
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        _logger.warning("Failed to read generated models from %s", path, exc_info=True)
        return []
    if not isinstance(raw, list):
        _logger.warning("Skipping generated models from %s: expected list", path)
        return []
    return [_model_from_generated_entry(entry) for entry in raw if isinstance(entry, dict)]


# Prices are per million tokens.
FALLBACK_DEFAULTS = [
    # --- Anthropic ---
    Model(
        "claude-opus-4-7",
        provider=Provider.ANTHROPIC,
        max_context_tokens=1_000_000,
        max_output_tokens=128_000,
        pricing=Pricing(price_in=5, price_out=25, price_cache_read=0.50, price_cache_write=6.25),
        reasoning_efforts=("low", "medium", "high", "xhigh", "max"),
    ),
    Model(
        "claude-opus-4-6",
        provider=Provider.ANTHROPIC,
        max_context_tokens=200_000,
        max_output_tokens=128_000,
        pricing=Pricing(price_in=5, price_out=25, price_cache_read=0.50, price_cache_write=6.25),
        reasoning_efforts=("low", "medium", "high", "max"),
    ),
    Model(
        "claude-sonnet-4-6",
        provider=Provider.ANTHROPIC,
        max_context_tokens=200_000,
        max_output_tokens=64_000,
        pricing=Pricing(price_in=3, price_out=15, price_cache_read=0.30, price_cache_write=3.75),
        reasoning_efforts=("low", "medium", "high", "max"),
    ),
    Model(
        "claude-haiku-4-5-20251001",
        provider=Provider.ANTHROPIC,
        max_context_tokens=200_000,
        max_output_tokens=64_000,
        pricing=Pricing(price_in=1, price_out=5, price_cache_read=0.10, price_cache_write=1.25),
        reasoning_efforts=("high", "max"),
    ),
    # --- OpenAI ---
    Model(
        "gpt-5.5",
        provider=Provider.OPENAI,
        max_context_tokens=1_050_000,
        max_output_tokens=128_000,
        pricing=Pricing(price_in=5, price_out=30),
        reasoning_efforts=("none", "low", "medium", "high", "xhigh"),
    ),
    Model(
        "gpt-5.4",
        provider=Provider.OPENAI,
        max_context_tokens=1_050_000,
        max_output_tokens=128_000,
        pricing=Pricing(price_in=2.50, price_out=15),
        reasoning_efforts=("none", "low", "medium", "high", "xhigh"),
    ),
    Model(
        "gpt-5.4-mini",
        provider=Provider.OPENAI,
        max_context_tokens=400_000,
        max_output_tokens=128_000,
        pricing=Pricing(price_in=0.75, price_out=4.50),
        reasoning_efforts=("none", "low", "medium", "high", "xhigh"),
    ),
    Model(
        "gpt-5.4-nano",
        provider=Provider.OPENAI,
        max_context_tokens=400_000,
        max_output_tokens=128_000,
        pricing=Pricing(price_in=0.20, price_out=1.25),
        reasoning_efforts=("none", "low", "medium", "high"),
    ),
    Model(
        "gpt-5.2",
        provider=Provider.OPENAI,
        max_context_tokens=128_000,
        max_output_tokens=16384,
        pricing=Pricing(price_in=1.75, price_out=14),
        reasoning_efforts=("minimal", "low", "medium", "high", "xhigh"),
    ),
    # --- OpenAI account auth via Codex endpoint ---
    Model(
        "openai-codex/gpt-5.6-sol",
        provider=Provider.OPENAI_CODEX,
        max_context_tokens=272_000,
        max_output_tokens=128_000,
        reasoning_efforts=("low", "medium", "high"),
    ),
    Model(
        "openai-codex/gpt-5.5",
        provider=Provider.OPENAI_CODEX,
        max_context_tokens=272_000,
        max_output_tokens=128_000,
        reasoning_efforts=("low", "medium", "high", "xhigh"),
    ),
    Model(
        "openai-codex/gpt-5.4",
        provider=Provider.OPENAI_CODEX,
        max_context_tokens=272_000,
        max_output_tokens=128_000,
        reasoning_efforts=("low", "medium", "high", "xhigh"),
    ),
    Model(
        "openai-codex/gpt-5.4-mini",
        provider=Provider.OPENAI_CODEX,
        max_context_tokens=272_000,
        max_output_tokens=128_000,
        reasoning_efforts=("low", "medium", "high", "xhigh"),
    ),
    Model(
        "openai-codex/gpt-5.3-codex",
        provider=Provider.OPENAI_CODEX,
        max_context_tokens=272_000,
        max_output_tokens=128_000,
        reasoning_efforts=("low", "medium", "high", "xhigh"),
    ),
    Model(
        "openai-codex/gpt-5.2",
        provider=Provider.OPENAI_CODEX,
        max_context_tokens=272_000,
        max_output_tokens=128_000,
        reasoning_efforts=("low", "medium", "high", "xhigh"),
    ),
    # --- Google ---
    Model(
        "gemini-3.1-pro-preview",
        provider=Provider.GOOGLE,
        max_context_tokens=1_000_000,
        max_output_tokens=65_536,
        pricing=Pricing(price_in=2, price_out=12),
        reasoning_efforts=("low", "medium", "high"),
    ),
    Model(
        "gemini-3.1-flash-lite-preview",
        provider=Provider.GOOGLE,
        max_context_tokens=1_000_000,
        max_output_tokens=65_536,
        pricing=Pricing(price_in=0.25, price_out=1.50),
        reasoning_efforts=("low", "medium", "high"),
    ),
    Model(
        "gemini-3-flash-preview",
        provider=Provider.GOOGLE,
        max_context_tokens=1_000_000,
        max_output_tokens=65_536,
        pricing=Pricing(price_in=0.50, price_out=3),
        reasoning_efforts=("low", "high"),
    ),
    # --- OpenRouter ---
    Model(
        "qwen/qwen3.5-35b-a3b",
        provider=Provider.OPENROUTER,
        max_context_tokens=262_144,
        max_output_tokens=65_536,
        pricing=Pricing(price_in=0.1625, price_out=1.30),
    ),
    Model(
        "qwen/qwen3.5-27b",
        provider=Provider.OPENROUTER,
        max_context_tokens=262_144,
        max_output_tokens=65_536,
        pricing=Pricing(price_in=0.195, price_out=1.56),
    ),
    Model(
        "qwen/qwen3.5-122b-a10b",
        provider=Provider.OPENROUTER,
        max_context_tokens=262_144,
        max_output_tokens=65_536,
        pricing=Pricing(price_in=0.26, price_out=2.08),
    ),
    Model(
        "minimax/minimax-m2.5",
        provider=Provider.OPENROUTER,
        max_context_tokens=196_608,
        max_output_tokens=196_608,
        pricing=Pricing(price_in=0.295, price_out=1.20),
    ),
    Model(
        "arcee-ai/trinity-large-preview:free",
        provider=Provider.OPENROUTER,
        max_context_tokens=131_000,
        max_output_tokens=16_384,
    ),
    Model(
        "x-ai/grok-4.1-fast",
        provider=Provider.OPENROUTER,
        max_context_tokens=2_000_000,
        max_output_tokens=30_000,
        pricing=Pricing(price_in=0.20, price_out=0.50),
    ),
]

def _derive_codex_models(generated: list[Model]) -> list[Model]:
    # Codex serves OpenAI models under ChatGPT account auth (no per-token pricing);
    # models.dev has no codex provider, so mirror the generated OpenAI gpt-5 entries.
    return [
        replace(m, id=f"openai-codex/{m.id}", provider=Provider.OPENAI_CODEX, pricing=Pricing(0, 0))
        for m in generated
        if m.provider == Provider.OPENAI
        and m.id.startswith("gpt-5")
        and "chat-latest" not in m.id
        and not m.id.endswith("-pro")
    ]


_GENERATED = _load_generated_models()
OAUTH_DEFAULTS = _derive_codex_models(_GENERATED) or [
    m for m in FALLBACK_DEFAULTS if m.provider == Provider.OPENAI_CODEX
]
DEFAULTS = (_GENERATED or [m for m in FALLBACK_DEFAULTS if m.provider != Provider.OPENAI_CODEX]) + OAUTH_DEFAULTS


@dataclass(frozen=True)
class EmbeddingModel:
    id: str
    provider: Provider
    dim: int
    base_url: str | None = None
    api_key_env: str | None = None


EMBEDDING_DEFAULTS = [
    EmbeddingModel("text-embedding-3-small", Provider.OPENAI, 1536),
    EmbeddingModel("text-embedding-3-large", Provider.OPENAI, 3072),
    EmbeddingModel("text-embedding-ada-002", Provider.OPENAI, 1536),
    EmbeddingModel("gemini-embedding-001", Provider.GOOGLE, 3072),
]


_models: dict[str, Model] = {m.id: m for m in DEFAULTS}
_embedding_models: dict[str, EmbeddingModel] = {m.id: m for m in EMBEDDING_DEFAULTS}
_custom_loaded = False


def load_custom_models(base_dir: Path) -> None:
    global _custom_loaded, _models_dir
    _models_dir = base_dir
    if _custom_loaded:
        return
    _custom_loaded = True

    if (raw := _read_models_json()) is None:
        return

    embedding_raw = {}
    for model_id, entry in raw.items():
        if model_id == "embedding":
            if isinstance(entry, dict):
                embedding_raw = entry
            continue

        if not isinstance(entry, dict):
            _logger.warning("Skipping custom model %s: expected object", model_id)
            continue
        if "base_url" not in entry:
            _logger.warning("Skipping custom model %s: missing base_url", model_id)
            continue
        if "context_window" not in entry:
            _logger.warning("Skipping custom model %s: missing context_window", model_id)
            continue

        model = Model(
            id=model_id,
            provider=Provider.CUSTOM,
            max_context_tokens=int(entry["context_window"]),
            max_output_tokens=int(entry.get("max_output_tokens", 8192)),
            pricing=Pricing(
                price_in=float(entry.get("price_in", 0)),
                price_out=float(entry.get("price_out", 0)),
            ),
            base_url=entry["base_url"],
            api_key_env=entry.get("api_key_env"),
            reasoning_efforts=tuple(entry.get("reasoning_efforts", ())),
        )
        _models[model_id] = model
        _logger.info("Registered custom model: %s (base_url=%s)", model_id, model.base_url)

    for model_id, entry in embedding_raw.items():
        if not isinstance(entry, dict):
            _logger.warning("Skipping custom embedding model %s: expected object", model_id)
            continue
        if "base_url" not in entry:
            _logger.warning("Skipping custom embedding model %s: missing base_url", model_id)
            continue
        if "dim" not in entry:
            _logger.warning("Skipping custom embedding model %s: missing dim", model_id)
            continue

        emb = EmbeddingModel(
            id=model_id,
            provider=Provider.CUSTOM,
            dim=int(entry["dim"]),
            base_url=entry["base_url"],
            api_key_env=entry.get("api_key_env"),
        )
        _embedding_models[model_id] = emb
        _logger.info("Registered custom embedding model: %s (base_url=%s)", model_id, emb.base_url)


def get_model(model_id: str) -> Model:
    if model_id not in _models:
        raise ValueError(f"Unknown model: {model_id}. Available: {', '.join(_models)}")
    return _models[model_id]


def supports_native_deferred_tools(model_id: str) -> bool:
    try:
        model = get_model(model_id)
    except ValueError:
        return False
    if model.provider == Provider.ANTHROPIC:
        return True
    if model.provider == Provider.OPENAI:
        return model.id.startswith(("gpt-5.4", "gpt-5.5", "gpt-5.6"))
    if model.provider == Provider.OPENAI_CODEX:
        return model.id.removeprefix("openai-codex/").startswith(("gpt-5.4", "gpt-5.5", "gpt-5.6"))
    return False


def get_embedding_model(model_id: str) -> EmbeddingModel:
    if model_id not in _embedding_models:
        raise ValueError(f"Unknown embedding model: {model_id}. Available: {', '.join(_embedding_models)}")
    return _embedding_models[model_id]


def list_models() -> list[str]:
    return list(_models)


def get_models() -> dict[str, Model]:
    return _models


def get_models_by_provider(provider: Provider) -> dict[str, Model]:
    return {mid: m for mid, m in _models.items() if m.provider == provider}


def list_embedding_models() -> list[str]:
    return list(_embedding_models)


def get_embedding_models() -> dict[str, EmbeddingModel]:
    return _embedding_models


def get_embedding_models_by_provider(provider: Provider) -> dict[str, EmbeddingModel]:
    return {mid: m for mid, m in _embedding_models.items() if m.provider == provider}


def add_custom_model(
    model_id: str,
    base_url: str,
    context_window: int,
    max_output_tokens: int = 8192,
    api_key_env: str | None = None,
) -> Model:
    raw = _read_models_json() or {}

    entry: dict = {"base_url": base_url, "context_window": context_window}
    if max_output_tokens != 8192:
        entry["max_output_tokens"] = max_output_tokens
    if api_key_env:
        entry["api_key_env"] = api_key_env

    raw[model_id] = entry
    _models_path().parent.mkdir(exist_ok=True)
    _models_path().write_text(json.dumps(raw, indent=2))

    model = Model(
        id=model_id,
        provider=Provider.CUSTOM,
        max_context_tokens=context_window,
        max_output_tokens=max_output_tokens,
        base_url=base_url,
        api_key_env=api_key_env,
    )
    _models[model_id] = model
    return model


def remove_custom_model(model_id: str) -> None:
    if model_id not in _models or _models[model_id].provider != Provider.CUSTOM:
        raise ValueError(f"Not a custom model: {model_id}")

    del _models[model_id]

    raw = _read_models_json()
    if raw is not None:
        raw.pop(model_id, None)
        _models_path().write_text(json.dumps(raw, indent=2))
