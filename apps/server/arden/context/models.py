from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal


class BackgroundStartDisposition(StrEnum):
    STARTED = "started"
    CANCELLED = "cancelled"


@dataclass
class SessionState:
    session_id: str
    started_at: datetime
    last_activity: datetime = field(default_factory=lambda: datetime.now(UTC))
    name: str | None = None
    auto_approve: set[str] = field(default_factory=set)
    session_type: Literal["chat", "channel", "agent"] = "chat"
    origin_automation_id: str | None = None
    parent_session_id: str | None = None
    parent_tool_call_id: str | None = None
    agent_type: str | None = None
    agent_status: str | None = None
    area_id: str | None = None
    chat_model: str | None = None
    # Loaded runs carry this optimistic-write fence. Explicit clear/rewind
    # advances it so an older in-memory run cannot restore discarded context.
    context_generation: int = 0
    context_etag: str | None = None


@dataclass(frozen=True)
class AreaContext:
    area_id: str
    name: str
    page_path: str | None = None
    default_cwd: str | None = None
    instructions: str | None = None
    knowledge_scope: str | None = None


@dataclass
class SessionData:
    state: SessionState
    messages: list[dict]
    last_input_tokens: int | None = None
    # Hash of the exact raw sessions.messages projection, including malformed
    # JSON recovered from the immutable transcript.
    context_generation: int = 0
    context_etag: str | None = None

    def __post_init__(self) -> None:
        if self.context_generation == 0 and self.state.context_generation != 0:
            self.context_generation = self.state.context_generation
        if self.context_etag is None:
            self.context_etag = self.state.context_etag

    @property
    def active_message_count(self) -> int:
        return len(self.messages)


@dataclass(frozen=True)
class SessionHistoryHeader:
    """Budget and routing fields needed before fetching a transcript page."""

    state: SessionState
    active_message_count: int
    last_input_tokens: int | None = None
