import os
from pathlib import Path
from typing import Literal, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from arden.constants import (
    AGENT_MAX_COST,
    AGENT_MAX_DEPTH,
    AGENT_MAX_ITERATIONS,
    AGENT_MAX_OUTPUT_TOKENS,
    AGENT_MAX_TOOL_CALLS,
    AGENT_MAX_WALL_TIME_SECONDS,
)
from arden.embedder import EmbeddingConfig
from arden.llm.models import (
    Provider,
    get_embedding_model,
    get_embedding_models,
    get_models,
    get_models_by_provider,
    load_custom_models,
)
from arden.logging import get_logger
from arden.settings import ARDEN_DIR, load_user_settings
from arden.tools.core.types import ToolOverrideDecision

_logger = get_logger(__name__)


def _local_timezone_name() -> str:
    configured = os.environ.get("TZ")
    if configured:
        try:
            ZoneInfo(configured)
        except ZoneInfoNotFoundError:
            pass
        else:
            return configured
    try:
        target = Path("/etc/localtime").resolve()
    except OSError:
        target = Path()
    parts = target.parts
    if "zoneinfo" in parts:
        candidate = "/".join(parts[parts.index("zoneinfo") + 1 :])
        try:
            ZoneInfo(candidate)
        except ZoneInfoNotFoundError:
            pass
        else:
            return candidate
    _logger.warning("local timezone could not be discovered; memory daily projection uses UTC")
    return "UTC"


# --- Provider / service mappings ---

PROVIDER_KEY_FIELDS = {
    "anthropic": "anthropic_api_key",
    "openai": "openai_api_key",
    "google": "gemini_api_key",
    "openrouter": "openrouter_api_key",
}

# provider_field → (default_chat, default_memory, default_embedding)
MODEL_DEFAULTS = {
    "anthropic_api_key": ("claude-sonnet-4-6", "claude-sonnet-4-6", None),
    "openai_api_key": ("gpt-5.2", "gpt-5.2", "text-embedding-3-small"),
    "gemini_api_key": ("gemini-3.1-pro-preview", "gemini-3-flash-preview", "gemini-embedding-001"),
}

OPENAI_CODEX_DEFAULT_CHAT = "openai-codex/gpt-5.5"
OPENAI_CODEX_DEFAULT_MEMORY = "openai-codex/gpt-5.4-mini"

PERSIST_KEYS = frozenset(
    {
        "chat_model",
        "research_model",
        "workflow_model",
        "memory_model",
        "embedding_model",
        "memory",
        "memory_timezone",
        "consolidation_interval",
        "integration_states",
        "gmail_days",
        "max_depth",
        "model_reasoning_efforts",
        "compression_threshold",
        "max_messages",
        "compression_keep_ratio",
        "summary_max_tokens",
        "mcp_servers",
        "tool_overrides",
        "agent_max_iterations",
        "agent_max_tool_calls",
        "agent_max_wall_time_seconds",
        "agent_max_cost",
        "agent_max_output_tokens",
        "web_search",
        "deferred_tools",
    }
)


def _has_openai_codex_auth() -> bool:
    from arden.llm.openai_codex_auth import is_authenticated

    return is_authenticated()


# --- Config ---


class Config(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ARDEN_",
        env_file_encoding="utf-8",
        extra="allow",
        validate_assignment=True,
        populate_by_name=True,
    )

    arden_dir: Path = Field(default=ARDEN_DIR, alias="ARDEN_DIR")

    # API keys — read from standard env vars via aliases
    anthropic_api_key: str | None = Field(default=None, alias="ANTHROPIC_API_KEY")
    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")
    gemini_api_key: str | None = Field(default=None, alias="GEMINI_API_KEY")
    openrouter_api_key: str | None = Field(default=None, alias="OPENROUTER_API_KEY")

    # Model IDs
    chat_model: str | None = None
    research_model: str | None = None
    workflow_model: str | None = None
    memory_model: str | None = None
    embedding_model: str | None = None

    # Memory
    memory: bool = True
    memory_timezone: str = Field(default_factory=lambda: _local_timezone_name())
    consolidation_interval: int = 30

    # Integrations
    integration_states: dict[str, bool] = Field(default_factory=dict)
    gmail_days: int = 30

    # Exa web search
    exa_api_key: str | None = Field(default=None, alias="EXA_API_KEY")
    web_search: Literal["auto", "exa", "ddgs", "none"] = Field(default="auto", alias="WEB_SEARCH")

    # Telegram
    telegram_bot_token: str | None = Field(default=None, alias="TELEGRAM_BOT_TOKEN")

    # Slack
    slack_bot_token: str | None = Field(default=None, alias="SLACK_BOT_TOKEN")
    slack_user_token: str | None = Field(default=None, alias="SLACK_USER_TOKEN")

    # MCP servers
    mcp_servers: dict[str, dict] | None = None
    tool_overrides: dict[str, ToolOverrideDecision] = Field(default_factory=dict)

    # Agent
    max_depth: int = Field(default=AGENT_MAX_DEPTH, gt=0)
    agent_max_iterations: int | None = AGENT_MAX_ITERATIONS
    agent_max_tool_calls: int | None = AGENT_MAX_TOOL_CALLS
    agent_max_wall_time_seconds: float | None = AGENT_MAX_WALL_TIME_SECONDS
    agent_max_cost: float | None = AGENT_MAX_COST
    agent_max_output_tokens: int | None = AGENT_MAX_OUTPUT_TOKENS
    model_reasoning_efforts: dict[str, str] = Field(default_factory=dict)
    deferred_tools: bool = True
    approval_timeout_seconds: int = 300

    # Context compaction
    compression_threshold: float = 0.8
    max_messages: int = 120
    compression_keep_ratio: float = 0.2
    summary_max_tokens: int = 1500

    # Server
    host: str = "127.0.0.1"
    port: int = 6877

    # API authentication
    api_key_hash: str | None = None

    # --- Validators ---

    @model_validator(mode="after")
    def _resolve_model_defaults(self) -> Self:
        self._resolve_chat_model()
        self._fill_model_fallbacks()
        self._resolve_embedding_model()
        return self

    def _resolve_chat_model(self) -> None:
        if self.chat_model:
            return
        for field, (chat, memory, _) in MODEL_DEFAULTS.items():
            if getattr(self, field, None):
                object.__setattr__(self, "chat_model", chat)
                if not self.memory_model:
                    object.__setattr__(self, "memory_model", memory)
                return
        if _has_openai_codex_auth():
            if not self.memory_model:
                object.__setattr__(self, "memory_model", OPENAI_CODEX_DEFAULT_MEMORY)
            object.__setattr__(self, "chat_model", OPENAI_CODEX_DEFAULT_CHAT)

    def _fill_model_fallbacks(self) -> None:
        if not self.memory_model and self.chat_model:
            self.memory_model = self.chat_model
        if not self.research_model and self.chat_model:
            self.research_model = self.chat_model
        if not self.workflow_model and self.chat_model:
            self.workflow_model = self.chat_model

    def _resolve_embedding_model(self) -> None:
        if self.embedding_model or "embedding_model" in self.model_fields_set:
            return
        for field, (_, _, embedding) in MODEL_DEFAULTS.items():
            if embedding and getattr(self, field, None):
                self.embedding_model = embedding
                return

    @field_validator("chat_model", "research_model", "workflow_model", "memory_model")
    @classmethod
    def _validate_model(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if v not in get_models():
            _logger.warning("Unknown model '%s' in settings, falling back to default", v)
            return None
        return v

    @field_validator("memory_timezone")
    @classmethod
    def _validate_memory_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"unknown memory timezone: {value}") from exc
        return value

    @field_validator("embedding_model")
    @classmethod
    def _validate_embedding_model(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if v not in get_embedding_models():
            _logger.warning("Unknown embedding model '%s' in settings, falling back to default", v)
            return None
        return v

    @field_validator("web_search", mode="before")
    @classmethod
    def _normalize_web_search(cls, v: str | None) -> str:
        if v is None:
            return "auto"
        normalized = str(v).strip().lower()
        if normalized in ("", "auto"):
            return "auto"
        if normalized in ("exa", "ddgs", "none"):
            return normalized
        raise ValueError("web_search must be one of: auto, exa, ddgs, none")

    # --- Derived properties ---

    @property
    def has_providers(self) -> bool:
        return bool(
            self.anthropic_api_key
            or self.openai_api_key
            or self.gemini_api_key
            or self.openrouter_api_key
            or _has_openai_codex_auth()
        )

    @property
    def has_any_model(self) -> bool:
        return self.has_providers or bool(get_models_by_provider(Provider.CUSTOM))

    @property
    def embedding(self) -> EmbeddingConfig | None:
        if not self.embedding_model:
            return None
        model = get_embedding_model(self.embedding_model)
        return EmbeddingConfig(model=model.id, dim=model.dim)

    def integration_enabled(self, integration_id: str) -> bool:
        return self.integration_states.get(integration_id, False)

    def reasoning_effort_for(self, model_id: str | None) -> str | None:
        if not model_id:
            return None
        effort = self.model_reasoning_efforts.get(model_id)
        if not effort:
            return None
        return effort if effort in get_models()[model_id].reasoning_efforts else None

    @property
    def db_dir(self) -> Path:
        return self.arden_dir

    @property
    def sessions_db_path(self) -> Path:
        return self.db_dir / "sessions.db"

    @property
    def search_db_path(self) -> Path:
        return self.db_dir / "search.db"

    @property
    def memory_db_path(self) -> Path:
        return self.db_dir / "memory.db"

    @property
    def memory_artifacts_dir(self) -> Path:
        return self.arden_dir / "memory"


# --- Config loading ---


def get_config() -> Config:
    load_custom_models(ARDEN_DIR)
    settings = load_user_settings()

    overrides = {k: settings[k] for k in PERSIST_KEYS if k in settings}
    if "api_key_hash" in settings:
        overrides["api_key_hash"] = settings["api_key_hash"]

    config = Config(
        _env_file=(ARDEN_DIR / ".env", ".env"),
        **overrides,
    )  # type: ignore

    # Fill stored API keys where env / .env didn't provide one
    for provider_id, field in PROVIDER_KEY_FIELDS.items():
        if getattr(config, field) is None and provider_id in settings.get("provider_keys", {}):
            setattr(config, field, settings["provider_keys"][provider_id])

    # service_keys are keyed by Config attribute name.
    stored_keys = settings.get("service_keys", {})
    for attr, api_key in stored_keys.items():
        if hasattr(config, attr) and getattr(config, attr) is None:
            setattr(config, attr, api_key)

    return config
