import json
from collections import Counter
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from arden.atomic_file import write_private_atomic
from arden.logging import get_logger
from arden.usage import Pricing

_logger = get_logger(__name__)

_models_dir: Path | None = None


def _models_path() -> Path:
    if _models_dir is None:
        raise RuntimeError("load_custom_models() must be called before accessing models path")
    return _models_dir / "models.json"


class CustomModelsError(RuntimeError):
    """The custom-model file does not satisfy its current exact contract."""


class CustomModelInputError(ValueError):
    """A requested custom-model value is invalid."""


class _CustomChatModelSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    base_url: str = Field(min_length=1)
    context_window: int = Field(gt=0, le=2_000_000)
    max_output_tokens: int = Field(default=8192, gt=0)
    api_key_env: str | None = None
    price_in: float = Field(default=0, ge=0, allow_inf_nan=False)
    price_out: float = Field(default=0, ge=0, allow_inf_nan=False)
    reasoning_efforts: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_output_limit(self):
        if self.max_output_tokens > self.context_window:
            raise ValueError("max_output_tokens must not exceed context_window")
        return self


class _CustomEmbeddingModelSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    base_url: str = Field(min_length=1)
    dim: int = Field(gt=0, le=65_536)
    api_key_env: str | None = None


@dataclass(frozen=True)
class _CustomModelsFile:
    chat: dict[str, _CustomChatModelSpec]
    embedding: dict[str, _CustomEmbeddingModelSpec]


def _reject_nonfinite_json(value: str) -> None:
    raise ValueError(f"invalid JSON constant {value}")


def _reject_duplicate_json_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _custom_model_id(value: object, *, kind: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > 256:
        raise CustomModelInputError(f"Custom {kind} model ID must contain 1-256 trimmed characters")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise CustomModelInputError(f"Custom {kind} model ID contains control characters")
    if kind == "chat" and value == "embedding":
        raise CustomModelInputError("Custom chat model ID 'embedding' is reserved")
    return value


def _decode_custom_models(raw: object) -> _CustomModelsFile:
    if not isinstance(raw, dict):
        raise CustomModelsError("Custom models file must be a JSON object")
    values = dict(raw)
    embedding_raw = values.pop("embedding", {})
    if not isinstance(embedding_raw, dict):
        raise CustomModelsError("Custom embedding models must be a JSON object")

    chat: dict[str, _CustomChatModelSpec] = {}
    embedding: dict[str, _CustomEmbeddingModelSpec] = {}
    try:
        for model_id, entry in values.items():
            model_id = _custom_model_id(model_id, kind="chat")
            chat[model_id] = _CustomChatModelSpec.model_validate(entry)
        for model_id, entry in embedding_raw.items():
            model_id = _custom_model_id(model_id, kind="embedding")
            embedding[model_id] = _CustomEmbeddingModelSpec.model_validate(entry)
    except (CustomModelInputError, ValidationError) as error:
        raise CustomModelsError(f"Invalid custom model entry: {error}") from error
    overlap = chat.keys() & embedding.keys()
    if overlap:
        raise CustomModelsError(f"Custom model IDs cannot be shared across kinds: {', '.join(sorted(overlap))}")
    return _CustomModelsFile(chat=chat, embedding=embedding)


def _read_custom_models() -> _CustomModelsFile:
    path = _models_path()
    if not path.exists():
        return _CustomModelsFile(chat={}, embedding={})
    try:
        raw = json.loads(
            path.read_text(),
            parse_constant=_reject_nonfinite_json,
            object_pairs_hook=_reject_duplicate_json_keys,
        )
    except (json.JSONDecodeError, OSError, ValueError) as error:
        raise CustomModelsError(f"Custom models file at {path} is unreadable or invalid JSON") from error
    return _decode_custom_models(raw)


def _encode_custom_models(models: _CustomModelsFile) -> bytes:
    raw = {
        model_id: spec.model_dump(exclude_defaults=True, exclude_none=True) for model_id, spec in models.chat.items()
    }
    if models.embedding:
        raw["embedding"] = {
            model_id: spec.model_dump(exclude_defaults=True, exclude_none=True)
            for model_id, spec in models.embedding.items()
        }
    return json.dumps(raw, indent=2, allow_nan=False).encode()


def _write_custom_models(models: _CustomModelsFile) -> None:
    write_private_atomic(_models_path(), _encode_custom_models(models))


class Provider(Enum):
    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    OPENAI_CODEX = "openai-codex"
    GOOGLE = "google"
    OPENROUTER = "openrouter"
    CUSTOM = "custom"


PROVIDER_LABELS: dict[Provider, str] = {
    Provider.ANTHROPIC: "Anthropic",
    Provider.OPENAI: "OpenAI",
    Provider.OPENAI_CODEX: "OpenAI Codex",
    Provider.GOOGLE: "Google",
    Provider.OPENROUTER: "OpenRouter",
    Provider.CUSTOM: "Custom",
}


def provider_label(provider: Provider) -> str:
    """Canonical user-facing provider name shared by every model surface."""
    return PROVIDER_LABELS[provider]


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
    native_deferred_tools: bool = False


def _generated_models_path() -> Path:
    return Path(__file__).with_name("generated_models.json")


class ModelCatalogError(RuntimeError):
    """The packaged model catalog does not satisfy its current contract."""


class _GeneratedModelSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    id: str = Field(min_length=1, max_length=256)
    provider: Literal["anthropic", "openai", "openai-codex", "google", "openrouter"]
    context_window: int = Field(gt=0)
    max_output_tokens: int = Field(gt=0)
    price_in: float = Field(ge=0, allow_inf_nan=False)
    price_out: float = Field(ge=0, allow_inf_nan=False)
    price_cache_read: float = Field(ge=0, allow_inf_nan=False)
    price_cache_write: float = Field(ge=0, allow_inf_nan=False)
    reasoning_efforts: list[str]
    native_deferred_tools: bool

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        if value != value.strip() or any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise ValueError("model ID must be trimmed and contain no control characters")
        return value


def _model_from_generated_spec(spec: _GeneratedModelSpec) -> Model:
    provider = Provider(spec.provider)
    return Model(
        id=spec.id,
        provider=provider,
        max_context_tokens=spec.context_window,
        max_output_tokens=spec.max_output_tokens,
        pricing=Pricing(
            price_in=spec.price_in,
            price_out=spec.price_out,
            price_cache_read=spec.price_cache_read,
            price_cache_write=spec.price_cache_write,
        ),
        reasoning_efforts=tuple(spec.reasoning_efforts),
        native_deferred_tools=spec.native_deferred_tools,
    )


def _load_generated_models() -> list[Model]:
    path = _generated_models_path()
    if not path.exists():
        raise ModelCatalogError(f"Packaged model catalog is missing at {path}")
    try:
        raw = json.loads(
            path.read_text(),
            parse_constant=_reject_nonfinite_json,
            object_pairs_hook=_reject_duplicate_json_keys,
        )
    except (json.JSONDecodeError, OSError, ValueError) as error:
        raise ModelCatalogError(f"Packaged model catalog at {path} is unreadable or invalid JSON") from error
    if not isinstance(raw, list) or not raw:
        raise ModelCatalogError(f"Packaged model catalog at {path} must be a non-empty JSON array")
    try:
        specs = [_GeneratedModelSpec.model_validate(entry) for entry in raw]
    except ValidationError as error:
        raise ModelCatalogError(f"Packaged model catalog at {path} has an invalid entry: {error}") from error

    model_ids = [spec.id for spec in specs]
    duplicates = sorted(model_id for model_id, count in Counter(model_ids).items() if count > 1)
    if duplicates:
        raise ModelCatalogError(f"Packaged model catalog contains duplicate IDs: {', '.join(duplicates)}")
    providers = {spec.provider for spec in specs}
    expected_providers = {"anthropic", "openai", "openai-codex", "google", "openrouter"}
    if providers != expected_providers:
        missing = ", ".join(sorted(expected_providers - providers))
        raise ModelCatalogError(f"Packaged model catalog is missing providers: {missing}")
    return [_model_from_generated_spec(spec) for spec in specs]


def _load_default_models() -> list[Model]:
    return _load_generated_models()


DEFAULTS = _load_default_models()


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


class ModelRegistry:
    def __init__(self, models: list[Model], embedding_models: list[EmbeddingModel] | None = None):
        self._models = {model.id: model for model in models}
        self._embedding_models = {model.id: model for model in (embedding_models or [])}

    def get_model(self, model_id: str) -> Model:
        if model_id not in self._models:
            raise ValueError(f"Unknown model: {model_id}. Available: {', '.join(self._models)}")
        return self._models[model_id]

    def get_models(self) -> dict[str, Model]:
        return dict(self._models)

    def get_models_by_provider(self, provider: Provider) -> dict[str, Model]:
        return {mid: model for mid, model in self._models.items() if model.provider == provider}

    def replace_provider_models(self, provider: Provider, models: list[Model]) -> bool:
        replacement = {model.id: model for model in models}
        if any(model.provider != provider for model in replacement.values()):
            raise ValueError(f"Replacement models must use provider {provider.value}")
        if replacement == self.get_models_by_provider(provider):
            return False
        updated: dict[str, Model] = {}
        inserted = False
        for mid, model in self._models.items():
            if model.provider == provider:
                if not inserted:
                    updated.update(replacement)
                    inserted = True
                continue
            updated[mid] = model
        if not inserted:
            updated.update(replacement)
        self._models = updated
        return True

    def add_model(self, model: Model) -> None:
        self._models[model.id] = model

    def remove_model(self, model_id: str) -> None:
        del self._models[model_id]

    def supports_native_deferred_tools(self, model_id: str) -> bool:
        model = self._models.get(model_id)
        return bool(model and model.native_deferred_tools)

    def get_embedding_model(self, model_id: str) -> EmbeddingModel:
        if model_id not in self._embedding_models:
            available = ", ".join(self._embedding_models)
            raise ValueError(f"Unknown embedding model: {model_id}. Available: {available}")
        return self._embedding_models[model_id]

    def get_embedding_models(self) -> dict[str, EmbeddingModel]:
        return dict(self._embedding_models)

    def get_embedding_models_by_provider(self, provider: Provider) -> dict[str, EmbeddingModel]:
        return {mid: model for mid, model in self._embedding_models.items() if model.provider == provider}

    def add_embedding_model(self, model: EmbeddingModel) -> None:
        self._embedding_models[model.id] = model

    def remove_embedding_model(self, model_id: str) -> None:
        del self._embedding_models[model_id]


_registry = ModelRegistry(DEFAULTS, EMBEDDING_DEFAULTS)
_custom_loaded = False


def load_custom_models(base_dir: Path) -> None:
    global _custom_loaded, _models_dir
    _models_dir = base_dir
    if _custom_loaded:
        return

    custom = _read_custom_models()
    chat_models: list[Model] = []
    embedding_models: list[EmbeddingModel] = []
    for model_id, entry in custom.chat.items():
        if model_id in _registry.get_models() or model_id in _registry.get_embedding_models():
            raise CustomModelsError(f"Custom model ID {model_id!r} conflicts with an existing model")
        model = Model(
            id=model_id,
            provider=Provider.CUSTOM,
            max_context_tokens=entry.context_window,
            max_output_tokens=entry.max_output_tokens,
            pricing=Pricing(
                price_in=entry.price_in,
                price_out=entry.price_out,
            ),
            base_url=entry.base_url,
            api_key_env=entry.api_key_env,
            reasoning_efforts=tuple(entry.reasoning_efforts),
        )
        chat_models.append(model)
    for model_id, entry in custom.embedding.items():
        if model_id in _registry.get_models() or model_id in _registry.get_embedding_models():
            raise CustomModelsError(f"Custom model ID {model_id!r} conflicts with an existing model")
        emb = EmbeddingModel(
            id=model_id,
            provider=Provider.CUSTOM,
            dim=entry.dim,
            base_url=entry.base_url,
            api_key_env=entry.api_key_env,
        )
        embedding_models.append(emb)

    for model in chat_models:
        _registry.add_model(model)
        _logger.info("Registered custom model: %s (base_url=%s)", model.id, model.base_url)
    for emb in embedding_models:
        _registry.add_embedding_model(emb)
        _logger.info("Registered custom embedding model: %s (base_url=%s)", emb.id, emb.base_url)
    _custom_loaded = True


def get_model(model_id: str) -> Model:
    return _registry.get_model(model_id)


def supports_native_deferred_tools(model_id: str) -> bool:
    return _registry.supports_native_deferred_tools(model_id)


def get_embedding_model(model_id: str) -> EmbeddingModel:
    return _registry.get_embedding_model(model_id)


def get_models() -> dict[str, Model]:
    return _registry.get_models()


def get_models_by_provider(provider: Provider) -> dict[str, Model]:
    return _registry.get_models_by_provider(provider)


def replace_provider_models(provider: Provider, models: list[Model]) -> bool:
    return _registry.replace_provider_models(provider, models)


def list_embedding_models() -> list[str]:
    return list(_registry.get_embedding_models())


def get_embedding_models() -> dict[str, EmbeddingModel]:
    return _registry.get_embedding_models()


def get_embedding_models_by_provider(provider: Provider) -> dict[str, EmbeddingModel]:
    return _registry.get_embedding_models_by_provider(provider)


def add_custom_model(
    model_id: str,
    base_url: str,
    context_window: int,
    max_output_tokens: int = 8192,
    api_key_env: str | None = None,
) -> Model:
    model_id = _custom_model_id(model_id, kind="chat")
    if model_id in get_models() or model_id in get_embedding_models():
        raise ValueError(f"Model ID {model_id!r} already exists")
    spec = _CustomChatModelSpec(
        base_url=base_url,
        context_window=context_window,
        max_output_tokens=max_output_tokens,
        api_key_env=api_key_env,
    )
    model = Model(
        id=model_id,
        provider=Provider.CUSTOM,
        max_context_tokens=spec.context_window,
        max_output_tokens=spec.max_output_tokens,
        base_url=spec.base_url,
        api_key_env=spec.api_key_env,
    )
    current = _read_custom_models()
    updated = _CustomModelsFile(chat={**current.chat, model_id: spec}, embedding=current.embedding)
    _write_custom_models(updated)
    _registry.add_model(model)
    return model


def add_custom_embedding_model(
    model_id: str,
    base_url: str,
    dim: int,
    api_key_env: str | None = None,
) -> EmbeddingModel:
    model_id = _custom_model_id(model_id, kind="embedding")
    if model_id in get_models() or model_id in get_embedding_models():
        raise ValueError(f"Model ID {model_id!r} already exists")
    spec = _CustomEmbeddingModelSpec(base_url=base_url, dim=dim, api_key_env=api_key_env)
    model = EmbeddingModel(
        id=model_id,
        provider=Provider.CUSTOM,
        dim=spec.dim,
        base_url=spec.base_url,
        api_key_env=spec.api_key_env,
    )
    current = _read_custom_models()
    updated = _CustomModelsFile(chat=current.chat, embedding={**current.embedding, model_id: spec})
    _write_custom_models(updated)
    _registry.add_embedding_model(model)
    return model


def remove_custom_model(model_id: str) -> None:
    try:
        model = get_model(model_id)
    except ValueError:
        raise ValueError(f"Not a custom model: {model_id}") from None
    if model.provider != Provider.CUSTOM:
        raise ValueError(f"Not a custom model: {model_id}")
    current = _read_custom_models()
    if model_id not in current.chat:
        raise CustomModelsError(f"Custom model {model_id!r} is missing from {_models_path()}")
    chat = dict(current.chat)
    del chat[model_id]
    _write_custom_models(_CustomModelsFile(chat=chat, embedding=current.embedding))
    _registry.remove_model(model_id)


def remove_custom_embedding_model(model_id: str) -> None:
    try:
        model = get_embedding_model(model_id)
    except ValueError:
        raise ValueError(f"Not a custom embedding model: {model_id}") from None
    if model.provider != Provider.CUSTOM:
        raise ValueError(f"Not a custom embedding model: {model_id}")
    current = _read_custom_models()
    if model_id not in current.embedding:
        raise CustomModelsError(f"Custom embedding model {model_id!r} is missing from {_models_path()}")
    embedding = dict(current.embedding)
    del embedding[model_id]
    _write_custom_models(_CustomModelsFile(chat=current.chat, embedding=embedding))
    _registry.remove_embedding_model(model_id)
