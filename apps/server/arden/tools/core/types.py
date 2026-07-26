from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ToolAction(StrEnum):
    READ = "read"
    DRAFT = "draft"
    WRITE = "write"
    EXECUTE = "execute"


class ToolScope(StrEnum):
    INTERNAL = "internal"
    EXTERNAL = "external"


class ApprovalMode(StrEnum):
    NEVER = "never"
    ALWAYS = "always"


class ToolPolicy(BaseModel):
    model_config = ConfigDict(frozen=True)

    action: ToolAction
    scope: ToolScope
    requires_approval: bool = False
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

    @model_validator(mode="after")
    def _normalize_approval_mode(self):
        if self.approval_mode is None:
            mode = ApprovalMode.ALWAYS if self.requires_approval else ApprovalMode.NEVER
            object.__setattr__(self, "approval_mode", mode)
            return self
        if not isinstance(self.approval_mode, ApprovalMode):
            raise ValueError(f"Unsupported approval_mode: {self.approval_mode!r}")
        if self.requires_approval and self.approval_mode == ApprovalMode.NEVER:
            raise ValueError("approval_mode='never' cannot downgrade requires_approval=True")
        requires = self.approval_mode != ApprovalMode.NEVER
        object.__setattr__(self, "requires_approval", requires)
        return self


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
