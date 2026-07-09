from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal


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
    project_id: str | None = None
    chat_model: str | None = None


@dataclass(frozen=True)
class ProjectContext:
    project_id: str
    name: str
    default_cwd: str | None = None
    instructions: str | None = None
    knowledge_scope: str | None = None


@dataclass
class SessionData:
    state: SessionState
    messages: list[dict]
    last_input_tokens: int | None = None
    # Size of the durable transcript after the most recent run. The desktop's
    # budget dial uses this for message-pressure because compaction uses the
    # saved transcript, even when a loop trims its model working set.
    last_message_count: int | None = None
