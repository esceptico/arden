"""Tests for the read-only sessions tools (`list_recent_sessions`,
`read_session`) used by cross-session audit automations."""

from datetime import UTC, datetime, timedelta

import pytest

from arden.context.models import SessionState
from arden.tools.core.context import (
    BackgroundTaskRegistry,
    IOBridge,
    RunContext,
    ToolContext,
    ToolExecution,
)
from arden.tools.core.registry import ToolRegistry
from arden.tools.sessions import (
    CreateSessionInput,
    ListRecentSessionsInput,
    ReadSessionInput,
    create_session,
    list_recent_sessions,
    read_session,
)


class _StubSessionService:
    """Just enough of the SessionService surface to drive the tools.
    Lets us assert what the tool actually returned without spinning up a
    real session store."""

    def __init__(self, sessions: list[dict], messages: dict[str, list[dict]] | None = None):
        self._sessions = sessions
        self._messages = messages or {}
        self.calls: list[tuple[str, dict]] = []

    async def list_sessions(self, limit: int = 20, **kwargs) -> list[dict]:
        self.calls.append(("list_sessions", kwargs))
        return list(self._sessions[:limit])

    async def list_messages(self, session_id: str, limit: int = 100, **kwargs) -> dict:
        self.calls.append(("list_messages", kwargs))
        msgs = self._messages.get(session_id, [])
        return {"messages": list(msgs[:limit])}

    async def search_messages(self, query: str, **kwargs) -> dict:
        self.calls.append(("search_messages", kwargs))
        return {"hits": [], "has_more": False}


def _make_execution(services: dict | None = None, *, area_id: str | None = None) -> ToolExecution:
    ctx = ToolContext(
        session_state=SessionState(session_id="cur", started_at=datetime.now(UTC), area_id=area_id),
        registry=ToolRegistry(),
        run=RunContext(run_id="run-1"),
        io=IOBridge(),
        background_tasks=BackgroundTaskRegistry(session_id="cur"),
        services=services or {},
    )
    return ToolExecution(tool_id="t1", tool_name="test", ctx=ctx)


@pytest.mark.asyncio
async def test_list_recent_sessions_returns_formatted_list():
    now = datetime.now(UTC)
    service = _StubSessionService(
        sessions=[
            {
                "session_id": "20260510_120000_000",
                "name": "Daily standup",
                "started_at": (now - timedelta(hours=2)).isoformat(),
                "last_activity": (now - timedelta(hours=1)).isoformat(),
                "message_count": 12,
            },
            {
                "session_id": "20260509_090000_000",
                "name": None,
                "started_at": (now - timedelta(days=1)).isoformat(),
                "last_activity": (now - timedelta(days=1)).isoformat(),
                "message_count": 4,
            },
        ]
    )
    service._sessions.reverse()
    execution = _make_execution(services={"session": service})

    result = await list_recent_sessions(execution, ListRecentSessionsInput(limit=20))

    assert not result.is_error
    assert "20260510_120000_000" in result.content
    assert "Daily standup" in result.content
    assert "(untitled)" in result.content  # falls back when name is None
    assert "12 msgs" in result.content
    assert result.content.index("20260510_120000_000") < result.content.index("20260509_090000_000")


@pytest.mark.asyncio
async def test_session_tools_pass_active_project_scope():
    service = _StubSessionService(
        sessions=[
            {"session_id": "s1", "name": "Scoped", "last_activity": datetime.now(UTC).isoformat(), "message_count": 1}
        ],
        messages={"s1": [{"seq": 0, "role": "user", "message": {"role": "user", "content": "hi"}}]},
    )
    execution = _make_execution(services={"session": service}, area_id="proj-1")

    await list_recent_sessions(execution, ListRecentSessionsInput())
    await read_session(execution, ReadSessionInput(session_id="s1"))

    assert ("list_sessions", {"area_id": "proj-1"}) in service.calls
    assert any(name == "list_messages" and kwargs.get("area_id") == "proj-1" for name, kwargs in service.calls)


@pytest.mark.asyncio
async def test_list_recent_sessions_filters_by_within_days():
    now = datetime.now(UTC)
    service = _StubSessionService(
        sessions=[
            {
                "session_id": "recent",
                "name": "Today",
                "last_activity": (now - timedelta(hours=2)).isoformat(),
                "message_count": 5,
            },
            {
                "session_id": "old",
                "name": "Last month",
                "last_activity": (now - timedelta(days=20)).isoformat(),
                "message_count": 3,
            },
        ]
    )
    execution = _make_execution(services={"session": service})

    result = await list_recent_sessions(execution, ListRecentSessionsInput(limit=20, within_days=7))

    assert "recent" in result.content
    assert "old" not in result.content


@pytest.mark.asyncio
async def test_list_recent_sessions_missing_service_is_error():
    execution = _make_execution(services={})  # session service absent

    result = await list_recent_sessions(execution, ListRecentSessionsInput())

    assert result.is_error
    assert "unavailable" in result.content.lower()


@pytest.mark.asyncio
async def test_read_session_truncates_long_content():
    long_body = "x" * 1000
    service = _StubSessionService(
        sessions=[],
        messages={
            "s1": [
                {"role": "user", "content": "Hello, can you summarize my emails?"},
                {"role": "assistant", "content": long_body},
            ]
        },
    )
    execution = _make_execution(services={"session": service})

    result = await read_session(execution, ReadSessionInput(session_id="s1", content_chars=100))

    assert not result.is_error
    assert "[user]" in result.content
    assert "[assistant]" in result.content
    # Long body should have been trimmed with an ellipsis marker.
    assert "…" in result.content
    # And it shouldn't carry the full original.
    assert long_body not in result.content


@pytest.mark.asyncio
async def test_read_session_role_filter():
    service = _StubSessionService(
        sessions=[],
        messages={
            "s1": [
                {"role": "user", "content": "prompt"},
                {"role": "assistant", "content": "answer"},
                {"role": "tool", "content": "tool output", "name": "bash"},
            ]
        },
    )
    execution = _make_execution(services={"session": service})

    result = await read_session(execution, ReadSessionInput(session_id="s1", role_filter="user"))

    assert "[user]" in result.content
    assert "[assistant]" not in result.content
    assert "[tool" not in result.content


# --- create_session tool ---


class _CapturingSessionService:
    """Captures session.create() + save() calls for assertion."""

    def __init__(self):
        self.created: list[SessionState] = []

    def create(self, name=None, session_type="chat", origin_automation_id=None):
        state = SessionState(
            session_id=f"sess-{len(self.created) + 1:03d}",
            started_at=datetime.now(UTC),
            name=name,
            session_type=session_type,
            origin_automation_id=origin_automation_id,
        )
        self.created.append(state)
        return state

    async def provision(self, name=None, session_type="chat", origin_automation_id=None, **kwargs):
        # Mirrors SessionService.provision: create + persist + announce. The
        # stub just records the create and skips the bus publish.
        return self.create(name=name, session_type=session_type, origin_automation_id=origin_automation_id)

    async def save(self, state, messages, metadata=None):
        return None


def _execution_with_loop(services: dict, loop_task_id: str | None = None) -> ToolExecution:
    ctx = ToolContext(
        session_state=SessionState(session_id="cur", started_at=datetime.now(UTC)),
        registry=ToolRegistry(),
        run=RunContext(run_id="run-1", loop_task_id=loop_task_id),
        io=IOBridge(),
        background_tasks=BackgroundTaskRegistry(session_id="cur"),
        services=services,
    )
    return ToolExecution(tool_id="t1", tool_name="create_session", ctx=ctx)


@pytest.mark.asyncio
async def test_create_session_defaults_to_channel():
    svc = _CapturingSessionService()
    execution = _execution_with_loop({"session": svc})

    result = await create_session(execution, CreateSessionInput(name="ops alerts"))

    assert not result.is_error
    assert len(svc.created) == 1
    state = svc.created[0]
    assert state.session_type == "channel"
    assert state.name == "ops alerts"
    assert state.origin_automation_id is None
    assert state.session_id in result.content


@pytest.mark.asyncio
async def test_create_session_chat_type_when_requested():
    svc = _CapturingSessionService()
    execution = _execution_with_loop({"session": svc})

    result = await create_session(execution, CreateSessionInput(name="adhoc", session_type="chat"))

    assert not result.is_error
    assert svc.created[0].session_type == "chat"


@pytest.mark.asyncio
async def test_create_session_stamps_origin_when_in_loop():
    svc = _CapturingSessionService()
    execution = _execution_with_loop({"session": svc}, loop_task_id="loop-shy-otter")

    result = await create_session(execution, CreateSessionInput(name="from loop"))

    assert not result.is_error
    assert svc.created[0].origin_automation_id == "loop-shy-otter"


@pytest.mark.asyncio
async def test_create_session_no_origin_when_not_in_loop():
    svc = _CapturingSessionService()
    execution = _execution_with_loop({"session": svc}, loop_task_id=None)

    await create_session(execution, CreateSessionInput(name="standalone"))

    assert svc.created[0].origin_automation_id is None


@pytest.mark.asyncio
async def test_create_session_missing_service_is_error():
    execution = _execution_with_loop({})  # no session service

    result = await create_session(execution, CreateSessionInput(name="x"))

    assert result.is_error
    assert "unavailable" in result.content.lower()


@pytest.mark.asyncio
async def test_read_session_handles_structured_content_blocks():
    service = _StubSessionService(
        sessions=[],
        messages={
            "s1": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Look at this image"},
                        {"type": "image", "source": {}},
                    ],
                },
                {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "name": "bash", "input": {}},
                    ],
                },
            ]
        },
    )
    execution = _make_execution(services={"session": service})

    result = await read_session(execution, ReadSessionInput(session_id="s1"))

    assert "Look at this image" in result.content
    assert "[image]" in result.content
    assert "[tool_use: bash]" in result.content
