import hashlib
import os
from pathlib import Path
from typing import Literal, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, field_validator, model_validator
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

ROLE_NAMES = ("research", "workflow", "memory", "auxiliary")


class RoleModelSetup(BaseModel):
    """Everything a background role needs to make a call, kept together.

    Roles used to be a row of parallel scalars — research_model,
    research_reasoning_effort, and one more top-level field per knob after
    that. One object per role means the next knob (output ceiling, provider
    routing, a thinking budget) lands in one place for all of them.

    Effort here overrides the per-model map: research, workflows, auxiliary
    tasks, and memory routinely share a model, and sharing its single effort
    entry made the role rows move together.
    """

    model: str | None = None
    reasoning_effort: str | None = None

    model_config = {"extra": "forbid"}


PERSIST_KEYS = frozenset(
    {
        "chat_model",
        "model_roles",
        "embedding_model",
        "memory",
        "memory_timezone",
        "timezone",
        "integration_states",
        "gmail_days",
        "max_depth",
        "model_reasoning_efforts",
        "compression_threshold",
        "max_messages",
        "compression_keep_ratio",
        "summary_max_tokens",
        "max_space_gb",
        "storage_backup_retention_days",
        "storage_allow_archived_cleanup",
        "storage_allow_current_cleanup",
        "storage_current_inactive_days",
        "storage_current_minimum",
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
    # Per-role setup (research | workflow | memory | auxiliary). The
    # `<role>_model` and
    # `<role>_reasoning_effort` properties below read out of here, so the rest
    # of the codebase never learned about the move.
    model_roles: dict[str, RoleModelSetup] = Field(default_factory=dict)
    embedding_model: str | None = None

    # The user's timezone (IANA name). Defaults to the machine's local zone,
    # which is right for a local server; set explicitly when the server runs
    # remotely so time-facing surfaces report the user's clock, not the host's.
    timezone: str = Field(default_factory=lambda: _local_timezone_name())

    # Memory
    memory: bool = True
    memory_timezone: str = Field(default_factory=lambda: _local_timezone_name())

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
    max_depth: int = Field(default=AGENT_MAX_DEPTH, ge=1, le=16)
    agent_max_iterations: int | None = AGENT_MAX_ITERATIONS
    agent_max_tool_calls: int | None = AGENT_MAX_TOOL_CALLS
    agent_max_wall_time_seconds: float | None = AGENT_MAX_WALL_TIME_SECONDS
    agent_max_cost: float | None = AGENT_MAX_COST
    agent_max_output_tokens: int | None = AGENT_MAX_OUTPUT_TOKENS
    # Effort keyed by MODEL. Right for chat — a session pins its model and
    # carries the effort with it — and it stays the default a role falls back
    # to when its own setup names none.
    model_reasoning_efforts: dict[str, str] = Field(default_factory=dict)
    deferred_tools: bool = True
    approval_timeout_seconds: int = 300

    # Context compaction
    compression_threshold: float = Field(default=0.8, ge=0.1, le=1)
    max_messages: int = Field(default=120, ge=10, le=1000)
    compression_keep_ratio: float = Field(default=0.2, ge=0, le=1)
    summary_max_tokens: int = Field(default=1500, ge=256, le=8000)

    # Server
    host: str = "127.0.0.1"
    port: int = 6877

    # Managed storage. None reports usage without enforcing a quota.
    max_space_gb: float | None = Field(default=None, ge=0.1)
    storage_backup_retention_days: int = Field(default=14, ge=1, le=3650)
    storage_allow_archived_cleanup: bool = False
    storage_allow_current_cleanup: bool = False
    storage_current_inactive_days: int = Field(default=90, ge=1, le=3650)
    storage_current_minimum: int = Field(default=100, ge=1, le=100_000)

    # API authentication
    api_key_hash: str | None = None

    # --- Validators ---

    @model_validator(mode="before")
    @classmethod
    def _fold_legacy_role_keys(cls, data):
        """Settings written before roles were objects carry `research_model` and
        friends at the top level. Read them into `model_roles` so an existing
        install keeps its models; the next save writes only the new shape."""
        if not isinstance(data, dict):
            return data
        roles = dict(data.get("model_roles") or {})
        for role in ROLE_NAMES:
            legacy = {
                "model": data.pop(f"{role}_model", None),
                "reasoning_effort": data.pop(f"{role}_reasoning_effort", None),
            }
            carried = {k: v for k, v in legacy.items() if v is not None}
            if not carried:
                continue
            existing = roles.get(role) or {}
            if not isinstance(existing, dict):
                existing = existing.model_dump()
            roles[role] = {**carried, **{k: v for k, v in existing.items() if v is not None}}
        if "auxiliary" not in roles and (memory := roles.get("memory")):
            memory_model = RoleModelSetup.model_validate(memory).model
            if memory_model:
                roles["auxiliary"] = {"model": memory_model}
        if roles:
            data["model_roles"] = roles
        return data

    def role_setup(self, role: str) -> RoleModelSetup:
        return self.model_roles.get(role) or RoleModelSetup()

    def _set_role_model(self, role: str, model_id: str | None) -> None:
        roles = dict(self.model_roles)
        roles[role] = self.role_setup(role).model_copy(update={"model": model_id})
        object.__setattr__(self, "model_roles", roles)

    @property
    def research_model(self) -> str | None:
        return self.role_setup("research").model

    @property
    def workflow_model(self) -> str | None:
        return self.role_setup("workflow").model

    @property
    def memory_model(self) -> str | None:
        return self.role_setup("memory").model

    @property
    def auxiliary_model(self) -> str | None:
        return self.role_setup("auxiliary").model

    @model_validator(mode="after")
    def _resolve_model_defaults(self) -> Self:
        self._resolve_chat_model()
        self._resolve_role_defaults()
        self._resolve_embedding_model()
        return self

    def _resolve_chat_model(self) -> None:
        if self.chat_model:
            return
        for field, (chat, memory, _) in MODEL_DEFAULTS.items():
            if getattr(self, field, None):
                object.__setattr__(self, "chat_model", chat)
                if not self.memory_model:
                    self._set_role_model("memory", memory)
                return
        if _has_openai_codex_auth():
            if not self.memory_model:
                self._set_role_model("memory", OPENAI_CODEX_DEFAULT_MEMORY)
            object.__setattr__(self, "chat_model", OPENAI_CODEX_DEFAULT_CHAT)

    def _resolve_role_defaults(self) -> None:
        if not self.chat_model:
            return
        for role in ("research", "workflow", "memory"):
            if not self.role_setup(role).model:
                self._set_role_model(role, self.chat_model)
        if not self.auxiliary_model:
            self._set_role_model("auxiliary", self.memory_model)

    def _resolve_embedding_model(self) -> None:
        if self.embedding_model or "embedding_model" in self.model_fields_set:
            return
        for field, (_, _, embedding) in MODEL_DEFAULTS.items():
            if embedding and getattr(self, field, None):
                self.embedding_model = embedding
                return

    @field_validator("model_roles")
    @classmethod
    def _validate_role_models(cls, roles: dict[str, RoleModelSetup]) -> dict[str, RoleModelSetup]:
        known = get_models()
        for role, setup in roles.items():
            if setup.model and setup.model not in known:
                _logger.warning("Unknown model '%s' for role '%s', falling back to default", setup.model, role)
                roles[role] = setup.model_copy(update={"model": None})
        return roles

    @field_validator("chat_model")
    @classmethod
    def _validate_model(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if v not in get_models():
            _logger.warning("Unknown model '%s' in settings, falling back to default", v)
            return None
        return v

    @field_validator("timezone", "memory_timezone")
    @classmethod
    def _validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"unknown timezone: {value}") from exc
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
        identity = model.id
        if model.provider is Provider.CUSTOM:
            endpoint_hash = hashlib.sha256((model.base_url or "").encode()).hexdigest()[:12]
            identity = f"{model.id}@{endpoint_hash}"
        return EmbeddingConfig(model=model.id, dim=model.dim, identity=identity)

    def integration_enabled(self, integration_id: str) -> bool:
        return self.integration_states.get(integration_id, False)

    def reasoning_effort_for(self, model_id: str | None) -> str | None:
        if not model_id:
            return None
        effort = self.model_reasoning_efforts.get(model_id)
        if not effort:
            return None
        return effort if effort in get_models()[model_id].reasoning_efforts else None

    def reasoning_effort_for_role(self, role: str, model_id: str | None) -> str | None:
        """A background role's effort: its own override when set and valid for the
        model, else whatever the per-model map says. `role` is research |
        workflow | memory | auxiliary."""
        if not model_id:
            return None
        override = self.role_setup(role).reasoning_effort
        if override and override in get_models()[model_id].reasoning_efforts:
            return override
        return self.reasoning_effort_for(model_id)

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
