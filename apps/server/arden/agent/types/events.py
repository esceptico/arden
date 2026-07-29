from dataclasses import dataclass

from arden.agent.types.stop import StopReason
from arden.agent.types.tools import ToolOutcome, ToolSourceRef
from arden.agent.types.usage import Usage
from arden.core.content import ContentBlock


@dataclass(frozen=True, kw_only=True)
class AgentEventBase:
    depth: int = 0
    parent_id: str | None = None


@dataclass(frozen=True, kw_only=True)
class TextStarted(AgentEventBase):
    message_id: str


@dataclass(frozen=True, kw_only=True)
class TextEnded(AgentEventBase):
    message_id: str
    content: str = ""


@dataclass(frozen=True, kw_only=True)
class TextDelta(AgentEventBase):
    content: str
    message_id: str | None = None


@dataclass(frozen=True, kw_only=True)
class TextBlock(AgentEventBase):
    content: str
    message_id: str | None = None


@dataclass(frozen=True, kw_only=True)
class ReasoningBlock(AgentEventBase):
    content: str


@dataclass(frozen=True, kw_only=True)
class ReasoningStarted(AgentEventBase):
    message_id: str


@dataclass(frozen=True, kw_only=True)
class ReasoningDelta(AgentEventBase):
    message_id: str
    content: str


@dataclass(frozen=True, kw_only=True)
class ReasoningEnded(AgentEventBase):
    message_id: str


@dataclass(frozen=True, kw_only=True)
class ToolInputStarted(AgentEventBase):
    tool_id: str
    name: str
    display_name: str
    kind: str = "tool"
    icon: str | None = None
    noun: str | None = None
    source: str | None = None


@dataclass(frozen=True, kw_only=True)
class ToolInputDelta(AgentEventBase):
    tool_id: str
    delta: str


@dataclass(frozen=True, kw_only=True)
class ToolInputEnded(AgentEventBase):
    tool_id: str


@dataclass(frozen=True, kw_only=True)
class ToolStarted(AgentEventBase):
    tool_id: str
    name: str
    args: dict
    display_name: str
    kind: str = "tool"
    icon: str | None = None
    noun: str | None = None
    source: str | None = None


@dataclass(frozen=True, kw_only=True)
class ToolCompleted(AgentEventBase):
    tool_id: str
    name: str
    result: str
    preview: str
    duration_ms: int
    is_error: bool
    data: dict | None
    display_name: str
    kind: str = "tool"
    model_content: tuple[ContentBlock, ...] = ()
    source_refs: tuple[ToolSourceRef, ...] = ()
    outcome: ToolOutcome | None = None


@dataclass(frozen=True)
class Result:
    text: str
    stop_reason: StopReason
    steps: int
    usage: Usage
