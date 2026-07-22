from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from ntrp.config import Config
    from ntrp.notifiers.base import Notifier

from ntrp.tools.core.base import Tool

ConnectionState = Literal[
    "connected",
    "not_configured",
    "disabled",
    "auth_required",
    "scope_required",
    "degraded",
]
ConnectionAction = Literal["oauth", "credentials", "enable", "settings"]


class IntegrationConnectionError(RuntimeError):
    """A provider failure that can be repaired through its connection flow."""

    def __init__(
        self,
        *,
        integration_id: str,
        reason: ConnectionState,
        detail: str,
        required_scopes: tuple[str, ...] = (),
        retry_safe: bool = False,
    ):
        super().__init__(detail)
        self.integration_id = integration_id
        self.reason = reason
        self.detail = detail
        self.required_scopes = required_scopes
        self.retry_safe = retry_safe


class IntegrationOperationError(RuntimeError):
    """A provider operation failure safe to expose at the tool boundary."""

    def __init__(self, *, code: str, safe_message: str, retryable: bool = False):
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message
        self.retryable = retryable


class IntegrationProviderError(IntegrationOperationError):
    def __init__(self, *, integration_label: str, cause: Exception):
        status = getattr(getattr(cause, "resp", None), "status", None)
        if status == 429:
            code = "rate_limited"
            retryable = True
        elif status == 404:
            code = "not_found"
            retryable = False
        else:
            code = "provider_error"
            retryable = status in {408, 500, 502, 503, 504}
        super().__init__(
            code=code,
            safe_message=f"{integration_label} provider request failed.",
            retryable=retryable,
        )


@dataclass(frozen=True)
class IntegrationConnectionSpec:
    connection_id: str
    capability: str
    action: ConnectionAction
    settings_tab: str = "integrations"
    enabled: Callable[["Config"], bool] | None = None
    configured: Callable[["Config"], bool] | None = None
    required_scopes: tuple[str, ...] = ()


@dataclass(frozen=True)
class IntegrationConnectionDescriptor:
    integration_id: str
    connection_id: str
    label: str
    capability: str
    action: ConnectionAction
    settings_tab: str
    state: ConnectionState
    detail: str | None = None
    required_scopes: tuple[str, ...] = ()
    tool_names: tuple[str, ...] = ()


@dataclass(frozen=True)
class IntegrationField:
    key: str
    label: str
    secret: bool = False
    env_var: str | None = None


@dataclass(frozen=True)
class IntegrationHealth:
    status: Literal["connected", "error", "not_configured"]
    detail: str | None = None


@dataclass(frozen=True)
class Integration:
    id: str
    label: str
    service_fields: list[IntegrationField] = field(default_factory=list)
    tools: dict[str, Tool] = field(default_factory=dict)
    command_tool_names: frozenset[str] = frozenset()
    notifier_class: type["Notifier"] | None = None
    build: Callable[["Config"], object | None] | None = None
    connection: IntegrationConnectionSpec | None = None


@dataclass(frozen=True)
class ToolProviderStatus:
    id: str
    label: str
    kind: Literal["native", "mcp"]
    health: IntegrationHealth
    tool_count: int
