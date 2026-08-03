"""Tests for the read-only sessions tools (`session_list`,
`session_read`) used by cross-session audit automations."""

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from arden.context.models import SessionState
from arden.context.store import AREA_FILTER_UNSET
from arden.tools.core.context import (
    BackgroundTaskRegistry,
    IOBridge,
    RunContext,
    ToolContext,
    ToolExecution,
)
from arden.tools.core.registry import ToolRegistry
from arden.tools.sessions import (
    _ITEM_FIELDS,
    CreateSessionInput,
    ListRecentSessionsInput,
    ReadSessionInput,
    SearchTranscriptsInput,
    create_session,
    list_recent_sessions,
    read_session,
    search_transcripts,
)


class _StubSessionService:
    """Just enough of the SessionService surface to drive the tools.
    Lets us assert what the tool actually returned without spinning up a
    real session store."""

    def __init__(self, sessions: list[dict], messages: dict[str, list[dict]] | None = None):
        self._sessions = sessions
        self._messages = messages or {}
        self.calls: list[tuple[str, dict]] = []

    async def list_sessions(self, limit: int = 20, offset: int = 0, **kwargs) -> list[dict]:
        self.calls.append(("list_sessions", {"limit": limit, "offset": offset, **kwargs}))
        return list(self._sessions[offset : offset + limit])

    async def list_messages(self, session_id: str, limit: int = 100, **kwargs) -> dict:
        self.calls.append(("list_messages", kwargs))
        msgs = self._messages.get(session_id, [])
        return {"messages": list(msgs[:limit])}

    async def search_messages(self, query: str, **kwargs) -> dict:
        self.calls.append(("search_messages", kwargs))
        return {"hits": [], "has_more": False}


class _StubRun:
    def __init__(self, session_id: str, run_id: str, approvals: int = 0, queued: int = 0):
        self.session_id = session_id
        self.run_id = run_id
        self.pending_approvals = {f"tool-{i}": None for i in range(approvals)}
        self.pending_injection_count = queued


class _StubRunRegistry:
    def __init__(self, runs: list[_StubRun] | None = None):
        self._runs = runs or []

    def list_active_runs(self) -> list[_StubRun]:
        return list(self._runs)

    def get_active_run(self, session_id: str) -> _StubRun | None:
        return next((run for run in self._runs if run.session_id == session_id), None)


def _make_execution(
    services: dict | None = None,
    *,
    area_id: str | None = None,
    run_registry: _StubRunRegistry | None = None,
) -> ToolExecution:
    ctx = ToolContext(
        session_state=SessionState(session_id="cur", started_at=datetime.now(UTC), area_id=area_id),
        registry=ToolRegistry(),
        run=RunContext(run_id="run-1"),
        io=IOBridge(),
        background_tasks=BackgroundTaskRegistry(session_id="cur"),
        services=services or {},
        run_registry=run_registry,
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
            {
                "session_id": "20260508_080000_000",
                "name": "Email updates feed",
                "session_type": "channel",
                "started_at": (now - timedelta(days=2)).isoformat(),
                "last_activity": (now - timedelta(days=2)).isoformat(),
                "message_count": 300,
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
    # Non-chat sessions carry a kind tag so callers can honor rules like
    # "never archive channels" from the list alone; plain chats stay untagged.
    assert "Email updates feed [channel]" in result.content
    assert "Daily standup [" not in result.content


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

    assert any(name == "list_sessions" and kwargs.get("area_id") == "proj-1" for name, kwargs in service.calls)
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


def _session_page(count: int) -> list[dict]:
    """`count` sessions, newest first, one hour apart."""
    now = datetime.now(UTC)
    return [
        {
            "session_id": f"s{i}",
            "name": f"Session {i}",
            "last_activity": (now - timedelta(hours=i)).isoformat(),
            "message_count": 1,
        }
        for i in range(count)
    ]


@pytest.mark.asyncio
async def test_list_recent_sessions_pages_with_offset():
    """An archival automation must be able to walk past the newest page —
    without offset, sessions older than `limit` were unreachable."""
    service = _StubSessionService(sessions=_session_page(5))
    execution = _make_execution(services={"session": service})

    result = await list_recent_sessions(execution, ListRecentSessionsInput(limit=2, offset=2))

    assert service.calls[-1][1]["offset"] == 2
    assert "s2" in result.content
    assert "s3" in result.content
    assert "s0" not in result.content
    assert result.data["offset"] == 2
    assert result.data["next_offset"] == 4
    # The footer has to teach the exact next call, not just "more exist".
    assert "Showing 2 sessions (offset 2); more exist — call again with offset=4." in result.content


@pytest.mark.asyncio
async def test_list_recent_sessions_reports_more_on_the_first_page():
    service = _StubSessionService(sessions=_session_page(5))
    execution = _make_execution(services={"session": service})

    result = await list_recent_sessions(execution, ListRecentSessionsInput(limit=3))

    assert result.data["has_more"] is True
    assert "call again with offset=3." in result.content


@pytest.mark.asyncio
async def test_list_recent_sessions_last_page_does_not_invite_another_call():
    service = _StubSessionService(sessions=_session_page(5))
    execution = _make_execution(services={"session": service})

    result = await list_recent_sessions(execution, ListRecentSessionsInput(limit=3, offset=3))

    assert result.data["has_more"] is False
    assert result.data["next_offset"] is None
    assert "call again" not in result.content


@pytest.mark.asyncio
async def test_list_recent_sessions_offset_keeps_area_scope():
    service = _StubSessionService(sessions=_session_page(5))
    execution = _make_execution(services={"session": service}, area_id="ops")

    await list_recent_sessions(execution, ListRecentSessionsInput(limit=2, offset=2))

    assert service.calls[-1][1]["area_id"] == "ops"
    assert service.calls[-1][1]["offset"] == 2


@pytest.mark.asyncio
async def test_list_recent_sessions_within_days_stops_at_the_window_edge():
    """Rows are newest-first, so once the window drops one there is no older
    page worth fetching — don't send the caller after it."""
    now = datetime.now(UTC)
    service = _StubSessionService(
        sessions=[
            {"session_id": "recent", "name": "Today", "last_activity": now.isoformat(), "message_count": 1},
            {
                "session_id": "old",
                "name": "Last month",
                "last_activity": (now - timedelta(days=20)).isoformat(),
                "message_count": 1,
            },
            {
                "session_id": "older",
                "name": "Ancient",
                "last_activity": (now - timedelta(days=40)).isoformat(),
                "message_count": 1,
            },
        ]
    )
    execution = _make_execution(services={"session": service})

    result = await list_recent_sessions(execution, ListRecentSessionsInput(limit=1, within_days=7))

    assert "recent" in result.content
    assert result.data["has_more"] is False
    assert "call again" not in result.content


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

    result = await read_session(execution, ReadSessionInput(session_id="s1", role_filter=["user"]))

    assert "[user]" in result.content
    assert "[assistant]" not in result.content
    assert "[tool" not in result.content


def test_read_session_rejects_an_unknown_role():
    """A typed enum turns 'usr' into a schema error the model can self-correct,
    instead of a filter that silently matches nothing."""
    with pytest.raises(ValidationError):
        ReadSessionInput(session_id="s1", role_filter=["usr"])


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
    return ToolExecution(tool_id="t1", tool_name="session_create", ctx=ctx)


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


# --- live run status + area scoping ---


@pytest.mark.asyncio
async def test_list_recent_sessions_renders_live_and_persisted_status():
    now = datetime.now(UTC)
    service = _StubSessionService(
        sessions=[
            {
                "session_id": "live",
                "name": "Live chat",
                "last_activity": now.isoformat(),
                "message_count": 3,
                "agent_status": None,
            },
            {
                "session_id": "waiting",
                "name": "Blocked chat",
                "last_activity": (now - timedelta(minutes=1)).isoformat(),
                "message_count": 2,
                "agent_status": None,
            },
            {
                "session_id": "agent",
                "name": "Agent run",
                "last_activity": (now - timedelta(minutes=2)).isoformat(),
                "message_count": 9,
                "agent_status": "completed",
            },
        ]
    )
    registry = _StubRunRegistry(
        [
            _StubRun("live", "run-a"),
            _StubRun("waiting", "run-b", approvals=1),
        ]
    )
    execution = _make_execution(services={"session": service}, run_registry=registry)

    result = await list_recent_sessions(execution, ListRecentSessionsInput())

    lines = {line.split(" · ")[0]: line for line in result.content.splitlines()}
    assert lines["- live"].endswith("· running")
    assert lines["- waiting"].endswith("· needs approval")
    assert lines["- agent"].endswith("· completed")


@pytest.mark.asyncio
async def test_list_recent_sessions_omits_status_when_run_registry_is_absent():
    service = _StubSessionService(
        sessions=[
            {
                "session_id": "s1",
                "name": "Plain",
                "last_activity": datetime.now(UTC).isoformat(),
                "message_count": 1,
                "agent_status": None,
            }
        ]
    )
    execution = _make_execution(services={"session": service})

    result = await list_recent_sessions(execution, ListRecentSessionsInput())

    assert result.content.strip().endswith("1 msgs")


@pytest.mark.asyncio
async def test_list_recent_sessions_inside_an_area_stays_in_that_area():
    service = _StubSessionService(sessions=[])
    execution = _make_execution(services={"session": service}, area_id="ops")

    await list_recent_sessions(execution, ListRecentSessionsInput())

    assert service.calls[-1][0] == "list_sessions"
    assert service.calls[-1][1]["area_id"] == "ops"


@pytest.mark.asyncio
async def test_list_recent_sessions_in_a_plain_chat_sees_every_session():
    service = _StubSessionService(sessions=[])
    execution = _make_execution(services={"session": service})

    await list_recent_sessions(execution, ListRecentSessionsInput())

    assert service.calls[-1][1]["area_id"] is AREA_FILTER_UNSET


@pytest.mark.asyncio
async def test_an_area_caller_cannot_widen_any_read_beyond_its_area():
    """The area boundary is not a parameter: an Area custodian's allowlist pins
    tool names, so a widening argument would be a hole nothing else closes."""
    service = _StubSessionService(
        sessions=[],
        messages={"s1": [{"seq": 0, "role": "user", "message": {"role": "user", "content": "hi"}}]},
    )
    execution = _make_execution(services={"session": service}, area_id="ops")

    for model in (ListRecentSessionsInput, ReadSessionInput, SearchTranscriptsInput):
        assert "scope" not in model.model_fields

    await list_recent_sessions(execution, ListRecentSessionsInput())
    await read_session(execution, ReadSessionInput(session_id="s1"))
    await search_transcripts(execution, SearchTranscriptsInput(query="hi"))

    assert [kwargs["area_id"] for _, kwargs in service.calls] == ["ops", "ops", "ops"]


@pytest.mark.asyncio
async def test_read_session_prefixes_a_run_status_header():
    service = _StubSessionService(
        sessions=[],
        messages={"s1": [{"seq": 4, "role": "user", "message": {"role": "user", "content": "hi"}}]},
    )
    registry = _StubRunRegistry([_StubRun("s1", "run-x", approvals=2, queued=1)])
    execution = _make_execution(services={"session": service}, run_registry=registry)

    result = await read_session(execution, ReadSessionInput(session_id="s1"))

    first = result.content.splitlines()[0]
    assert first.startswith("[session s1] running")
    assert "run run-x" in first
    assert "2 approval(s) pending" in first
    assert "1 message(s) queued" in first
    assert result.content.splitlines()[1] == ""


@pytest.mark.asyncio
async def test_read_session_reports_idle_for_a_session_without_a_run():
    service = _StubSessionService(
        sessions=[],
        messages={"s1": [{"seq": 0, "role": "user", "message": {"role": "user", "content": "hi"}}]},
    )
    execution = _make_execution(services={"session": service}, run_registry=_StubRunRegistry())

    result = await read_session(execution, ReadSessionInput(session_id="s1"))

    assert result.content.splitlines()[0] == "[session s1] idle"


@pytest.mark.asyncio
async def test_read_session_omits_the_header_without_a_run_registry():
    service = _StubSessionService(
        sessions=[],
        messages={"s1": [{"seq": 0, "role": "user", "message": {"role": "user", "content": "hi"}}]},
    )
    execution = _make_execution(services={"session": service})

    result = await read_session(execution, ReadSessionInput(session_id="s1"))

    assert not result.content.startswith("[session")


@pytest.mark.asyncio
async def test_list_recent_sessions_oldest_first_for_archival_sweeps():
    # order="oldest" puts the stalest sessions on page ONE — an archival
    # automation reads exactly the rows it wants instead of paging in from
    # the newest end.
    service = _StubSessionService(sessions=_session_page(3))
    execution = _make_execution(services={"session": service})

    result = await list_recent_sessions(execution, ListRecentSessionsInput(limit=3, order="oldest"))

    assert service.calls[-1][1]["newest_first"] is False
    assert result.content.index("s2") < result.content.index("s1") < result.content.index("s0")


@pytest.mark.asyncio
async def test_oldest_first_keeps_paging_open_under_a_time_window():
    # Walking oldest-first INTO a within_days window: dropped rows are the
    # too-old head, so later pages may still match — has_more must survive.
    now = datetime.now(UTC)
    service = _StubSessionService(
        sessions=[
            {
                "session_id": f"s{i}",
                "name": f"Session {i}",
                "last_activity": (now - timedelta(days=40 - i * 15)).isoformat(),
                "message_count": 1,
            }
            for i in range(3)  # 40d, 25d, 10d old — oldest first as the store would return
        ]
    )
    execution = _make_execution(services={"session": service})

    result = await list_recent_sessions(execution, ListRecentSessionsInput(limit=1, order="oldest", within_days=30))

    assert result.data["has_more"] is True


@pytest.mark.asyncio
async def test_list_recent_sessions_projects_only_agent_useful_fields():
    """Store rows carry plumbing columns; shipping them wholesale is payload no
    consumer reads."""
    service = _StubSessionService(
        sessions=[
            {
                "session_id": "s1",
                "name": "Ops",
                "last_activity": datetime.now(UTC).isoformat(),
                "message_count": 3,
                "chat_model": "claude-sonnet",
                "parent_session_id": "p1",
                "parent_tool_call_id": "t1",
                "area_id": "ops",
                "origin_automation_id": "auto-9",
            }
        ]
    )
    execution = _make_execution(services={"session": service})

    result = await list_recent_sessions(execution, ListRecentSessionsInput())

    assert set(result.data["items"][0]) == set(_ITEM_FIELDS)
    assert result.data["items"][0]["session_id"] == "s1"


class _FailingSearchService(_StubSessionService):
    def __init__(self, error: Exception):
        super().__init__(sessions=[])
        self._error = error

    async def search_messages(self, query: str, **kwargs) -> dict:
        raise self._error


@pytest.mark.asyncio
async def test_search_transcripts_explains_an_fts_syntax_error():
    service = _FailingSearchService(sqlite3.OperationalError('fts5: syntax error near """'))
    execution = _make_execution(services={"session": service})

    result = await search_transcripts(execution, SearchTranscriptsInput(query='invoice "'))

    assert result.is_error
    assert result.outcome.error.code == "invalid_arguments"
    assert "Quote the phrase" in result.outcome.error.recovery_action


@pytest.mark.asyncio
async def test_search_transcripts_does_not_swallow_other_errors():
    """A bug in our own code must reach the runner as internal_error, not hide
    behind a retryable 'search failed'."""
    service = _FailingSearchService(RuntimeError("boom"))
    execution = _make_execution(services={"session": service})

    with pytest.raises(RuntimeError):
        await search_transcripts(execution, SearchTranscriptsInput(query="invoice"))
