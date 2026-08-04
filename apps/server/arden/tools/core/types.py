from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator


class ToolAction(StrEnum):
    READ = "read"
    DRAFT = "draft"
    WRITE = "write"
    EXECUTE = "execute"


class ToolScope(StrEnum):
    INTERNAL = "internal"
    EXTERNAL = "external"


class ToolPlacement(StrEnum):
    """Where a tool's handler runs: in the server process, or on a connected
    client executor (the user's device). Client-placed tools never silently
    fall back to server execution."""

    SERVER = "server"
    CLIENT = "client"


class ApprovalMode(StrEnum):
    NEVER = "never"
    ALWAYS = "always"


class ToolPolicy(BaseModel):
    model_config = ConfigDict(frozen=True)

    action: ToolAction
    scope: ToolScope
    placement: ToolPlacement = ToolPlacement.SERVER
    requires_approval: StrictBool = False
    # Deferred tools are hidden from the model's schema list until loaded via
    # load_tools / tool_search. Declared here, at the tool definition, so
    # deferral is a property of the tool — not a hand-maintained registry map.
    deferred: StrictBool = False
    approval_mode: ApprovalMode | None = None
    permissions: frozenset[str] = Field(default_factory=frozenset)
    timeout_seconds: int | None = None
    audit: bool = True
    max_result_chars: int | None = None
    offload: bool = True
    allow_approval_bypass: bool = True
    destructive: bool | None = None
    open_world: bool | None = None
    idempotent: bool | None = None
    # Explicit conflict domain for tool-runner scheduling. Tools that share a
    # mutable resource declare the same key; provider tools may omit it and use
    # their registered integration source as the default domain.
    concurrency_group: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _normalize_approval_mode(cls, value: Any) -> Any:
        if not isinstance(value, Mapping):
            return value
        normalized = dict(value)
        requires_approval = normalized.get("requires_approval", False)
        raw_mode = normalized.get("approval_mode")
        if raw_mode is None:
            if isinstance(requires_approval, bool):
                normalized["approval_mode"] = ApprovalMode.ALWAYS if requires_approval else ApprovalMode.NEVER
            return normalized
        try:
            mode = ApprovalMode(raw_mode)
        except (TypeError, ValueError):
            return normalized
        if requires_approval is True and mode is ApprovalMode.NEVER:
            raise ValueError("approval_mode='never' cannot downgrade requires_approval=True")
        normalized["approval_mode"] = mode
        normalized["requires_approval"] = mode is not ApprovalMode.NEVER
        return normalized


class PermissionDecision(StrEnum):
    EXECUTE = "execute"
    REQUEST_APPROVAL = "request_approval"
    DENY = "deny"


class ToolOverrideDecision(StrEnum):
    APPROVE = "approve"
    ASK = "ask"
    DENY = "deny"


@dataclass(frozen=True)
class ApprovalInfo:
    description: str
    preview: str | None
    diff: str | None


@dataclass(frozen=True, slots=True)
class ApprovalWaived:
    """An approval callback's verdict that THIS call needs no approval — e.g.
    steering an agent the caller itself spawned. Distinct from returning None,
    which means no safe preview could be built and blocks the call."""


APPROVAL_WAIVED = ApprovalWaived()
