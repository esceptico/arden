"""Tests for the app-control tools: acting on other chat sessions, raising a
needs-you item on Home, and moving the user around the app."""

import tempfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

from arden.areas.asks import AskStore
from arden.context.models import SessionData, SessionState
from arden.context.store import AREA_FILTER_UNSET
from arden.events.sse import EventType, SSEEvent
from arden.tools.app_control import (
    ArchiveSessionInput,
    OpenInAppInput,
    RenameSessionInput,
    RequestAttentionInput,
    SendToSessionInput,
    archive_session,
    archive_session_tool,
    open_in_app,
    rename_session,
    request_attention,
    send_to_session,
    send_to_session_tool,
)
from arden.tools.core.context import (
    BackgroundTaskRegistry,
    IOBridge,
    RunContext,
    ToolContext,
    ToolExecution,
)
from arden.tools.core.registry import ToolRegistry


class _StubSessionService:
    def __init__(self, sessions: dict[str, SessionData], archived: set[str] | None = None):
        self._sessions = sessions
        self._archived = archived or set()
        self.renamed: list[tuple[str, str]] = []
        self.archived: list[str] = []
        self.announced: list[tuple[str, int]] = []
        self.list_calls: list[dict] = []

    async def load(self, session_id: str) -> SessionData | None:
        return self._sessions.get(session_id)

    async def is_archived(self, session_id: str) -> bool:
        return session_id in self._archived

    async def list_sessions(self, limit: int = 20, **kwargs) -> list[dict]:
        self.list_calls.append({"limit": limit, **kwargs})
        return [{"session_id": sid} for sid in list(self._sessions)[:limit]]

    async def rename(self, session_id: str, name: str) -> bool:
        self.renamed.append((session_id, name))
        return True

    async def archive(self, session_id: str) -> bool:
        self.archived.append(session_id)
        return session_id in self._sessions

    async def announce_row(self, session_state: SessionState, message_count: int) -> None:
        self.announced.append((session_state.name, message_count))


class _StubAppControl:
    def __init__(self):
        self.dispatched: list[dict] = []
        self.emitted: list[SSEEvent] = []
        self.asks = AskStore(Path(tempfile.mkdtemp()) / "asks.json")

    async def emit(self, event: SSEEvent) -> None:
        self.emitted.append(event)

    async def dispatch(self, session_id: str, message: str, *, client_id: str) -> str | None:
        self.dispatched.append({"session_id": session_id, "message": message, "client_id": client_id})
        return "run-target"


class _StubRunRegistry:
    def __init__(self, active: dict[str, object] | None = None):
        self._active = active or {}

    def get_active_run(self, session_id: str):
        return self._active.get(session_id)


def _session(session_id: str, name: str | None, messages: int = 2) -> SessionData:
    return SessionData(
        state=SessionState(session_id=session_id, started_at=datetime.now(UTC), name=name),
        messages=[{"role": "user", "content": "hi"} for _ in range(messages)],
    )


def _make_execution(
    *,
    sessions: dict[str, SessionData] | None = None,
    archived: set[str] | None = None,
    run_registry: _StubRunRegistry | None = None,
    area_id: str | None = None,
) -> tuple[ToolExecution, _StubSessionService, _StubAppControl]:
    service = _StubSessionService(sessions if sessions is not None else {}, archived)
    app_control = _StubAppControl()
    ctx = ToolContext(
        session_state=SessionState(session_id="cur", started_at=datetime.now(UTC), area_id=area_id),
        registry=ToolRegistry(),
        run=RunContext(run_id="run-1"),
        io=IOBridge(),
        background_tasks=BackgroundTaskRegistry(session_id="cur"),
        services={"session": service, "app_control": app_control},
        run_registry=run_registry,
    )
    return ToolExecution(tool_id="t1", tool_name="test", ctx=ctx), service, app_control


@pytest.mark.asyncio
async def test_send_to_session_refuses_its_own_session():
    execution, _, app_control = _make_execution(sessions={"cur": _session("cur", "Current")})

    result = await send_to_session(execution, SendToSessionInput(session_id="cur", message="go"))

    assert result.is_error
    assert result.outcome.error.code == "invalid_arguments"
    assert not app_control.dispatched


@pytest.mark.asyncio
async def test_send_to_session_reports_unknown_session_with_recent_ids():
    execution, service, app_control = _make_execution(sessions={"s1": _session("s1", "Ops")})

    result = await send_to_session(execution, SendToSessionInput(session_id="nope", message="go"))

    assert result.is_error
    assert result.outcome.error.code == "not_found"
    assert "s1" in result.content
    assert not app_control.dispatched
    assert service.list_calls[-1]["area_id"] is AREA_FILTER_UNSET


@pytest.mark.asyncio
async def test_send_to_session_dispatches_with_tool_call_client_id_and_no_approval_override():
    execution, _, app_control = _make_execution(sessions={"s1": _session("s1", "Ops")})

    result = await send_to_session(execution, SendToSessionInput(session_id="s1", message="check the digest"))

    assert not result.is_error
    assert app_control.dispatched == [
        {"session_id": "s1", "message": "check the digest", "client_id": "send_to_session:t1"}
    ]
    assert "Ops" in result.content


@pytest.mark.asyncio
async def test_send_to_session_refuses_an_archived_target():
    """An archived session is out of the sidebar, so its reply lands where the
    user cannot see it — and the approval was granted for a visible answer."""
    execution, _, app_control = _make_execution(
        sessions={"s1": _session("s1", "Ops")},
        archived={"s1"},
    )

    result = await send_to_session(execution, SendToSessionInput(session_id="s1", message="go"))

    assert result.is_error
    assert result.outcome.error.code == "conflict"
    assert not app_control.dispatched


@pytest.mark.asyncio
async def test_send_to_session_policy_matches_bash():
    assert send_to_session_tool.policy.requires_approval is True
    assert send_to_session_tool.policy.allow_approval_bypass is False
    assert send_to_session_tool.policy.permissions == frozenset({"session", "app_control"})


@pytest.mark.asyncio
async def test_rename_session_renames_and_announces_row():
    execution, service, _ = _make_execution(sessions={"s1": _session("s1", "Old name", messages=3)})

    result = await rename_session(execution, RenameSessionInput(session_id="s1", name="Invoice triage"))

    assert not result.is_error
    assert service.renamed == [("s1", "Invoice triage")]
    assert service.announced == [("Invoice triage", 3)]


@pytest.mark.asyncio
async def test_rename_session_reports_unknown_session():
    execution, service, _ = _make_execution(sessions={"s1": _session("s1", "Ops")})

    result = await rename_session(execution, RenameSessionInput(session_id="ghost", name="Nope"))

    assert result.is_error
    assert result.outcome.error.code == "not_found"
    assert service.renamed == []


@pytest.mark.asyncio
async def test_rename_session_refuses_an_archived_target():
    """Renaming publishes a sidebar row, which would resurrect an archived chat
    at the top of the list until the next /sessions load."""
    execution, service, _ = _make_execution(sessions={"s1": _session("s1", "Ops")}, archived={"s1"})

    result = await rename_session(execution, RenameSessionInput(session_id="s1", name="Nope"))

    assert result.is_error
    assert result.outcome.error.code == "conflict"
    assert service.renamed == []
    assert service.announced == []


@pytest.mark.asyncio
async def test_archive_session_refuses_own_session():
    execution, service, _ = _make_execution(sessions={"cur": _session("cur", "Current")})

    result = await archive_session(execution, ArchiveSessionInput(session_id="cur"))

    assert result.is_error
    assert result.outcome.error.code == "invalid_arguments"
    assert service.archived == []


@pytest.mark.asyncio
async def test_archive_session_refuses_a_session_with_a_live_run():
    execution, service, _ = _make_execution(
        sessions={"s1": _session("s1", "Ops")},
        run_registry=_StubRunRegistry({"s1": object()}),
    )

    result = await archive_session(execution, ArchiveSessionInput(session_id="s1"))

    assert result.is_error
    assert result.outcome.error.code == "conflict"
    assert service.archived == []


@pytest.mark.asyncio
async def test_archive_session_archives():
    execution, service, _ = _make_execution(
        sessions={"s1": _session("s1", "Ops")},
        run_registry=_StubRunRegistry(),
    )

    result = await archive_session(execution, ArchiveSessionInput(session_id="s1"))

    assert not result.is_error
    assert service.archived == ["s1"]
    assert "Settings → Archive" in result.content


@pytest.mark.asyncio
async def test_archive_session_reports_an_already_archived_session_as_such():
    execution, service, _ = _make_execution(
        sessions={"s1": _session("s1", "Ops")},
        archived={"s1"},
        run_registry=_StubRunRegistry(),
    )

    result = await archive_session(execution, ArchiveSessionInput(session_id="s1"))

    assert result.is_error
    assert result.outcome.error.code == "conflict"
    assert "already out of the sidebar" in result.outcome.error.recovery_action
    assert service.archived == []


@pytest.mark.asyncio
async def test_archive_session_needs_no_approval():
    assert archive_session_tool.policy.requires_approval is False


def _attention(key: str = "invoice-4021", kind: str = "question") -> RequestAttentionInput:
    return RequestAttentionInput(
        text="Invoice #4021 is 12 days overdue",
        kind=kind,
        why_now="The 15-day escalation window closes tomorrow.",
        what_next="I send the chase email you approve.",
        key=key,
    )


@pytest.mark.asyncio
async def test_request_attention_creates_an_unfiled_ask():
    execution, _, app_control = _make_execution()

    result = await request_attention(execution, _attention())

    assert not result.is_error
    ask = app_control.asks.get("tool:cur:invoice-4021")
    assert ask.area_key is None
    assert ask.actions == [{"verb": "open_session", "ref": "cur"}]
    assert ask.reply_session_id == "cur"
    assert ask.source == "agent_tool"
    assert ask.provenance == "run:run-1"
    assert [(e.type, e.keys) for e in app_control.emitted] == [(EventType.AREAS_CHANGED, [])]


@pytest.mark.asyncio
async def test_request_attention_stamps_the_callers_area():
    execution, _, app_control = _make_execution(area_id="ops")

    await request_attention(execution, _attention())

    assert app_control.asks.get("tool:cur:invoice-4021").area_key == "ops"
    assert [e.keys for e in app_control.emitted] == [["ops"]]


@pytest.mark.asyncio
async def test_request_attention_notify_kind_expires():
    execution, _, app_control = _make_execution()

    await request_attention(execution, _attention(kind="notify"))

    assert app_control.asks.get("tool:cur:invoice-4021").expires_at is not None


@pytest.mark.asyncio
async def test_request_attention_question_kind_does_not_expire():
    execution, _, app_control = _make_execution()

    await request_attention(execution, _attention(kind="question"))

    assert app_control.asks.get("tool:cur:invoice-4021").expires_at is None


@pytest.mark.asyncio
async def test_request_attention_resurface_is_silent_after_a_decision():
    execution, _, app_control = _make_execution()
    await request_attention(execution, _attention())
    app_control.asks.resolve("tool:cur:invoice-4021", "done", None, "acknowledged")

    result = await request_attention(execution, _attention())

    assert "Refreshed the existing item" in result.content
    assert result.preview == "refreshed"


@pytest.mark.asyncio
async def test_request_attention_says_when_it_loses_the_shared_unfiled_lane():
    """Every chat outside an Area shares one Home lane, so a stored ask is not
    necessarily a visible one — the result must not claim otherwise."""
    execution, _, app_control = _make_execution()
    await request_attention(execution, _attention(key="invoice-4021", kind="question"))

    result = await request_attention(execution, _attention(key="ledger-drift", kind="notify"))

    assert not result.is_error
    assert "Home is not showing 'ledger-drift' yet" in result.content
    assert app_control.asks.get("tool:cur:ledger-drift") is not None


@pytest.mark.asyncio
async def test_open_in_app_emits_navigation_requested_with_origin_session():
    execution, _, app_control = _make_execution()

    result = await open_in_app(
        execution,
        OpenInAppInput(destination={"kind": "area", "area_id": "ops"}, label="Open the Ops area"),
    )

    assert not result.is_error
    event = app_control.emitted[0]
    assert event.type == EventType.NAVIGATION_REQUESTED
    assert event.origin_session_id == "cur"
    assert event.destination == {"kind": "area", "area_id": "ops"}
    assert event.label == "Open the Ops area"


@pytest.mark.asyncio
async def test_open_in_app_drops_unset_optional_destination_fields():
    execution, _, app_control = _make_execution()

    await open_in_app(execution, OpenInAppInput(destination={"kind": "automation"}, label="Open automations"))

    assert app_control.emitted[0].destination == {"kind": "automation"}


@pytest.mark.asyncio
async def test_rename_session_updates_a_live_runs_in_memory_state():
    # save_session rewrites the whole sessions row from the run's in-memory
    # state at run end — a rename landing mid-run must reach that state, or
    # the finished run silently reverts the name minutes later.
    from arden.server.state import RunRegistry

    registry = RunRegistry()
    live = registry.create_run("s1")
    live.session_state = SessionState(session_id="s1", started_at=datetime.now(UTC), name="Old name")
    execution, service, _ = _make_execution(
        sessions={"s1": _session("s1", "Old name", messages=3)},
        run_registry=registry,
    )

    result = await rename_session(execution, RenameSessionInput(session_id="s1", name="Invoice triage"))

    assert not result.is_error
    assert service.renamed == [("s1", "Invoice triage")]
    assert live.session_state.name == "Invoice triage"


def test_sync_session_name_leaves_settled_runs_alone():
    from arden.server.state import RunRegistry, RunStatus

    registry = RunRegistry()
    run = registry.create_run("s1")
    run.session_state = SessionState(session_id="s1", started_at=datetime.now(UTC), name="Old name")
    run.status = RunStatus.COMPLETED

    registry.sync_session_name("s1", "New name")

    assert run.session_state.name == "Old name"
