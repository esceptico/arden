"""Tests for the app-control tools: acting on other chat sessions, raising a
needs-you item on Home, and moving the user around the app."""

import asyncio
import contextlib
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from arden.areas.asks import AskStore
from arden.context.models import SessionData, SessionState
from arden.context.store import AREA_FILTER_UNSET
from arden.events.sse import EventType, SSEEvent
from arden.tools.app_control import (
    AppOpenInput,
    AppRequestAttentionInput,
    AreaListInput,
    SessionArchiveInput,
    SessionRenameInput,
    SessionSendMessageInput,
    app_open,
    app_request_attention,
    approve_session_send_message,
    area_list,
    session_archive,
    session_archive_tool,
    session_rename,
    session_send_message,
    session_send_message_tool,
)
from arden.tools.core.context import (
    BackgroundTaskRegistry,
    IOBridge,
    RunContext,
    ToolContext,
    ToolExecution,
)
from arden.tools.core.registry import ToolRegistry
from arden.tools.core.types import APPROVAL_WAIVED, ApprovalInfo


class _StubSessionService:
    def __init__(
        self,
        sessions: dict[str, SessionData],
        archived: set[str] | None = None,
        areas: list[str] | None = None,
    ):
        self._sessions = sessions
        self._archived = archived or set()
        self._areas = areas or []
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

    async def get_area(self, area_id: str | None) -> dict | None:
        return {"area_id": area_id} if area_id in self._areas else None

    async def get_area_by_ref(self, area_ref: str) -> dict | None:
        for area_id in self._areas:
            if area_ref == self._area_ref(area_id):
                return {"area_id": area_id, "area_ref": area_ref, "name": area_id.title()}
        return None

    async def list_areas(self) -> list[dict]:
        return [
            {"area_id": area_id, "area_ref": self._area_ref(area_id), "name": area_id.title()}
            for area_id in self._areas
        ]

    @staticmethod
    def _area_ref(area_id: str) -> str:
        return f"{area_id.removeprefix('area_internal_')}~111111"


class _StubAutomationService:
    def __init__(self, refs: dict[str, str] | None = None):
        self._refs = refs or {}

    async def get_by_ref(self, automation_ref: str):
        if automation_ref not in self._refs:
            raise KeyError(f"Automation {automation_ref} not found")
        return SimpleNamespace(task_id=self._refs[automation_ref], automation_ref=automation_ref)


class _StubWikiService:
    """Only what _invalid_destination reads: the paths that exist."""

    def __init__(self, paths: set[str] | None = None):
        self._paths = paths or set()

    def list_pages(self):
        return tuple(SimpleNamespace(resource=SimpleNamespace(path=path)) for path in self._paths)


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
    background_tasks: BackgroundTaskRegistry | None = None,
    areas: list[str] | None = None,
    automation: _StubAutomationService | None = None,
    wiki: _StubWikiService | None = None,
) -> tuple[ToolExecution, _StubSessionService, _StubAppControl]:
    service = _StubSessionService(sessions if sessions is not None else {}, archived, areas)
    app_control = _StubAppControl()
    services: dict[str, object] = {"session": service, "app_control": app_control}
    if automation is not None:
        services["automation"] = automation
    if wiki is not None:
        services["wiki"] = wiki
    ctx = ToolContext(
        session_state=SessionState(session_id="cur", started_at=datetime.now(UTC), area_id=area_id),
        registry=ToolRegistry(),
        run=RunContext(run_id="run-1"),
        io=IOBridge(),
        background_tasks=background_tasks or BackgroundTaskRegistry(session_id="cur"),
        services=services,
        run_registry=run_registry,
    )
    return ToolExecution(tool_id="t1", tool_name="test", ctx=ctx), service, app_control


@asynccontextmanager
async def _live_agent(session_id: str) -> AsyncIterator[BackgroundTaskRegistry]:
    registry = BackgroundTaskRegistry(session_id="cur")
    task = asyncio.create_task(asyncio.sleep(3600))
    registry.register("agent-1", task, command="scan docs")
    await registry.record_started(task_id="agent-1", command="scan docs", child_session_id=session_id)
    try:
        yield registry
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


@pytest.mark.asyncio
async def test_send_message_refuses_its_own_session():
    execution, _, app_control = _make_execution(sessions={"cur": _session("cur", "Current")})

    result = await session_send_message(execution, SessionSendMessageInput(session_id="cur", message="go"))

    assert result.is_error
    assert result.outcome.error.code == "invalid_arguments"
    assert not app_control.dispatched


@pytest.mark.asyncio
async def test_send_message_reports_unknown_session_with_recent_ids():
    execution, service, app_control = _make_execution(sessions={"s1": _session("s1", "Ops")})

    result = await session_send_message(execution, SessionSendMessageInput(session_id="nope", message="go"))

    assert result.is_error
    assert result.outcome.error.code == "not_found"
    assert "s1" in result.content
    assert not app_control.dispatched
    assert service.list_calls[-1]["area_id"] is AREA_FILTER_UNSET


@pytest.mark.asyncio
async def test_unknown_session_hint_stays_inside_the_callers_area():
    """The hint lists real session ids — an Area-scoped agent must not learn
    about sessions its own session_list would never show it."""
    execution, service, _ = _make_execution(sessions={"s1": _session("s1", "Ops")}, area_id="ops")

    result = await session_send_message(execution, SessionSendMessageInput(session_id="nope", message="go"))

    assert result.is_error
    assert service.list_calls[-1]["area_id"] == "ops"


@pytest.mark.asyncio
async def test_send_message_dispatches_with_tool_call_client_id_and_no_approval_override():
    execution, _, app_control = _make_execution(sessions={"s1": _session("s1", "Ops")})

    result = await session_send_message(execution, SessionSendMessageInput(session_id="s1", message="check the digest"))

    assert not result.is_error
    assert app_control.dispatched == [
        {"session_id": "s1", "message": "check the digest", "client_id": "session_send_message:t1"}
    ]
    assert "Ops" in result.content


@pytest.mark.asyncio
async def test_send_message_refuses_an_archived_target():
    """An archived session is out of the sidebar, so its reply lands where the
    user cannot see it — and the approval was granted for a visible answer."""
    execution, _, app_control = _make_execution(
        sessions={"s1": _session("s1", "Ops")},
        archived={"s1"},
    )

    result = await session_send_message(execution, SessionSendMessageInput(session_id="s1", message="go"))

    assert result.is_error
    assert result.outcome.error.code == "conflict"
    assert not app_control.dispatched


@pytest.mark.asyncio
async def test_send_message_policy_matches_bash():
    assert session_send_message_tool.policy.requires_approval is True
    assert session_send_message_tool.policy.allow_approval_bypass is False
    assert session_send_message_tool.policy.permissions == frozenset({"session", "app_control"})


@pytest.mark.asyncio
async def test_send_message_steers_a_live_agent_without_dispatching():
    async with _live_agent("cur::a1") as registry:
        execution, _, app_control = _make_execution(background_tasks=registry)

        result = await session_send_message(
            execution, SessionSendMessageInput(session_id="cur::a1", message="also check pricing")
        )

        assert not result.is_error
        assert app_control.dispatched == []
        drained = registry.drain_injections("agent-1")
        assert len(drained) == 1
        assert drained[0]["role"] == "user"
        assert "<steering_message>" in drained[0]["content"]
        assert "also check pricing" in drained[0]["content"]


@pytest.mark.asyncio
async def test_send_message_waives_approval_only_for_own_live_agents():
    async with _live_agent("cur::a1") as registry:
        execution, _, _ = _make_execution(background_tasks=registry)

        waived = await approve_session_send_message(
            execution, SessionSendMessageInput(session_id="cur::a1", message="go")
        )
        foreign = await approve_session_send_message(execution, SessionSendMessageInput(session_id="s1", message="go"))

        assert waived is APPROVAL_WAIVED
        assert isinstance(foreign, ApprovalInfo)
        assert foreign.preview.startswith("To: s1")


@pytest.mark.asyncio
async def test_send_message_refuses_to_dispatch_when_the_agent_finished_mid_approval(monkeypatch):
    """A waived approval must never silently become an unapproved cross-chat run."""
    async with _live_agent("cur::a1") as registry:
        monkeypatch.setattr(registry, "queue_steering", lambda task_id, text: False)
        execution, _, app_control = _make_execution(
            sessions={"cur::a1": _session("cur::a1", "Agent")},
            background_tasks=registry,
        )

        result = await session_send_message(execution, SessionSendMessageInput(session_id="cur::a1", message="go"))

        assert result.is_error
        assert result.outcome.error.code == "conflict"
        assert app_control.dispatched == []


@pytest.mark.asyncio
async def test_rename_session_renames_and_announces_row():
    execution, service, _ = _make_execution(sessions={"s1": _session("s1", "Old name", messages=3)})

    result = await session_rename(execution, SessionRenameInput(session_id="s1", name="Invoice triage"))

    assert not result.is_error
    assert service.renamed == [("s1", "Invoice triage")]
    assert service.announced == [("Invoice triage", 3)]
    assert "Old name → Invoice triage" in result.content


@pytest.mark.asyncio
async def test_rename_session_reports_unknown_session():
    execution, service, _ = _make_execution(sessions={"s1": _session("s1", "Ops")})

    result = await session_rename(execution, SessionRenameInput(session_id="ghost", name="Nope"))

    assert result.is_error
    assert result.outcome.error.code == "not_found"
    assert service.renamed == []


@pytest.mark.asyncio
async def test_rename_session_refuses_an_archived_target():
    """Renaming publishes a sidebar row, which would resurrect an archived chat
    at the top of the list until the next /sessions load."""
    execution, service, _ = _make_execution(sessions={"s1": _session("s1", "Ops")}, archived={"s1"})

    result = await session_rename(execution, SessionRenameInput(session_id="s1", name="Nope"))

    assert result.is_error
    assert result.outcome.error.code == "conflict"
    assert service.renamed == []
    assert service.announced == []


@pytest.mark.asyncio
async def test_archive_session_skips_own_session_but_archives_the_rest():
    execution, service, _ = _make_execution(
        sessions={"cur": _session("cur", "Current"), "s1": _session("s1", "Ops")},
        run_registry=_StubRunRegistry(),
    )

    result = await session_archive(execution, SessionArchiveInput(session_ids=["cur", "s1"]))

    assert not result.is_error
    assert service.archived == ["s1"]
    assert "cur · skipped — this is the session you are running in" in result.content
    assert "Archived 1 of 2" in result.content


@pytest.mark.asyncio
async def test_archive_session_batch_reports_every_outcome():
    # A sweep must not lose good archives to one bad id — each stands alone.
    execution, service, _ = _make_execution(
        sessions={"s1": _session("s1", "Ops"), "s2": _session("s2", "Live"), "s3": _session("s3", "Old")},
        archived={"s3"},
        run_registry=_StubRunRegistry({"s2": object()}),
    )

    result = await session_archive(execution, SessionArchiveInput(session_ids=["s1", "s2", "s3", "ghost", "s1"]))

    assert not result.is_error
    assert service.archived == ["s1"]  # duplicate id archives once
    assert "s1 · archived — Ops" in result.content
    assert "s2 · skipped — live run in progress" in result.content
    assert "s3 · skipped — already archived" in result.content
    assert "ghost · skipped — no such session" in result.content
    assert result.data == {"archived": 1, "requested": 5}


@pytest.mark.asyncio
async def test_archive_session_all_skipped_is_an_error():
    execution, service, _ = _make_execution(
        sessions={"s1": _session("s1", "Ops")},
        archived={"s1"},
        run_registry=_StubRunRegistry(),
    )

    result = await session_archive(execution, SessionArchiveInput(session_ids=["s1", "ghost"]))

    assert result.is_error
    assert result.outcome.error.code == "conflict"
    assert service.archived == []


@pytest.mark.asyncio
async def test_archive_session_needs_no_approval():
    assert session_archive_tool.policy.requires_approval is False


def _attention(key: str = "invoice-4021", kind: str = "question") -> AppRequestAttentionInput:
    return AppRequestAttentionInput(
        text="Invoice #4021 is 12 days overdue",
        kind=kind,
        why_now="The 15-day escalation window closes tomorrow.",
        what_next="I send the chase email you approve.",
        key=key,
    )


@pytest.mark.asyncio
async def test_request_attention_creates_an_unfiled_ask():
    execution, _, app_control = _make_execution()

    result = await app_request_attention(execution, _attention())

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

    await app_request_attention(execution, _attention())

    assert app_control.asks.get("tool:cur:invoice-4021").area_key == "ops"
    assert [e.keys for e in app_control.emitted] == [["ops"]]


@pytest.mark.asyncio
async def test_request_attention_notify_kind_expires():
    execution, _, app_control = _make_execution()

    await app_request_attention(execution, _attention(kind="notify"))

    assert app_control.asks.get("tool:cur:invoice-4021").expires_at is not None


@pytest.mark.asyncio
async def test_request_attention_question_kind_does_not_expire():
    execution, _, app_control = _make_execution()

    await app_request_attention(execution, _attention(kind="question"))

    assert app_control.asks.get("tool:cur:invoice-4021").expires_at is None


@pytest.mark.asyncio
async def test_request_attention_resurface_is_silent_after_a_decision():
    execution, _, app_control = _make_execution()
    await app_request_attention(execution, _attention())
    app_control.asks.resolve("tool:cur:invoice-4021", "done", None, "acknowledged")

    result = await app_request_attention(execution, _attention())

    assert "Refreshed the existing item" in result.content
    assert result.preview == "refreshed"


@pytest.mark.asyncio
async def test_request_attention_says_when_it_loses_the_shared_unfiled_lane():
    """Every chat outside an Area shares one Home lane, so a stored ask is not
    necessarily a visible one — the result must not claim otherwise."""
    execution, _, app_control = _make_execution()
    await app_request_attention(execution, _attention(key="invoice-4021", kind="question"))

    result = await app_request_attention(execution, _attention(key="ledger-drift", kind="notify"))

    assert not result.is_error
    assert "Home is not showing 'ledger-drift' yet" in result.content
    assert app_control.asks.get("tool:cur:ledger-drift") is not None


@pytest.mark.asyncio
async def test_open_in_app_emits_navigation_requested_with_origin_session():
    execution, _, app_control = _make_execution(areas=["ops"])

    result = await app_open(
        execution,
        AppOpenInput(destination={"kind": "area", "area_ref": "ops~111111"}, label="Open the Ops area"),
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

    await app_open(execution, AppOpenInput(destination={"kind": "automation"}, label="Open automations"))

    assert app_control.emitted[0].destination == {"kind": "automation"}


@pytest.mark.asyncio
async def test_open_in_app_refuses_an_unknown_session():
    execution, _, app_control = _make_execution(sessions={"s1": _session("s1", "Ops")})

    result = await app_open(
        execution,
        AppOpenInput(destination={"kind": "session", "session_id": "ghost"}, label="Open the ghost chat"),
    )

    assert result.is_error
    assert result.outcome.error.code == "not_found"
    assert not app_control.emitted


@pytest.mark.asyncio
async def test_open_in_app_refuses_an_unknown_area():
    execution, _, app_control = _make_execution(areas=["ops"])

    result = await app_open(
        execution,
        AppOpenInput(destination={"kind": "area", "area_ref": "finance~111111"}, label="Open the Finance area"),
    )

    assert result.is_error
    assert result.outcome.error.code == "not_found"
    assert "ops~111111" in result.content
    assert not app_control.emitted


@pytest.mark.asyncio
async def test_area_list_exposes_refs_without_internal_ids():
    execution, _, _ = _make_execution(areas=["area_internal_ops"])

    result = await area_list(execution, AreaListInput())

    assert "ops~111111" in result.content
    assert "area_internal_ops" not in result.content


@pytest.mark.asyncio
async def test_open_in_app_refuses_an_unknown_automation():
    execution, _, app_control = _make_execution(
        automation=_StubAutomationService({"digest~111111": "internal-digest-id"})
    )

    result = await app_open(
        execution,
        AppOpenInput(
            destination={"kind": "automation", "automation_ref": "ghost~000000"},
            label="Review the digest",
        ),
    )

    assert result.is_error
    assert result.outcome.error.code == "not_found"
    assert not app_control.emitted


@pytest.mark.asyncio
async def test_open_in_app_refuses_an_automation_ref_when_automations_are_off():
    execution, _, app_control = _make_execution()

    result = await app_open(
        execution,
        AppOpenInput(
            destination={"kind": "automation", "automation_ref": "digest~111111"},
            label="Review the digest",
        ),
    )

    assert result.is_error
    assert result.outcome.error.code == "not_found"
    assert not app_control.emitted


@pytest.mark.asyncio
async def test_open_in_app_resolves_public_automation_ref_to_internal_destination():
    execution, _, app_control = _make_execution(
        automation=_StubAutomationService({"digest~111111": "internal-digest-id"})
    )

    result = await app_open(
        execution,
        AppOpenInput(
            destination={"kind": "automation", "automation_ref": "digest~111111"},
            label="Review the digest",
        ),
    )

    assert not result.is_error
    assert app_control.emitted[0].destination == {"kind": "automation", "task_id": "internal-digest-id"}


def test_open_in_app_rejects_internal_automation_id_field():
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        AppOpenInput.model_validate(
            {
                "destination": {"kind": "automation", "task_id": "internal-digest-id"},
                "label": "Review the digest",
            }
        )


def test_open_in_app_rejects_internal_area_id_field():
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        AppOpenInput.model_validate(
            {
                "destination": {"kind": "area", "area_id": "area_internal_ops"},
                "label": "Open Ops",
            }
        )


@pytest.mark.asyncio
async def test_open_in_app_opens_the_automations_surface_without_a_task_id():
    """The bare surface carries no ref, so there is nothing to validate."""
    execution, _, app_control = _make_execution()

    result = await app_open(execution, AppOpenInput(destination={"kind": "automation"}, label="Open automations"))

    assert not result.is_error
    assert app_control.emitted[0].destination == {"kind": "automation"}


@pytest.mark.asyncio
async def test_open_in_app_still_opens_home_and_settings_without_checks():
    execution, _, app_control = _make_execution()

    home = await app_open(execution, AppOpenInput(destination={"kind": "home"}, label="Go Home"))
    settings = await app_open(
        execution,
        AppOpenInput(destination={"kind": "settings", "tab": "models"}, label="Open model settings"),
    )

    assert not home.is_error
    assert not settings.is_error
    assert [event.destination for event in app_control.emitted] == [
        {"kind": "home"},
        {"kind": "settings", "tab": "models"},
    ]


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

    result = await session_rename(execution, SessionRenameInput(session_id="s1", name="Invoice triage"))

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


@pytest.mark.asyncio
async def test_open_in_app_sends_the_user_to_a_wiki_page():
    """A page is an address. An agent citing one can land the user on it
    rather than at the vault root — the same vocabulary the desktop's route
    history walks."""
    execution, _, app_control = _make_execution(wiki=_StubWikiService({"areas/health.md"}))

    result = await app_open(
        execution,
        AppOpenInput(
            destination={"kind": "memory", "path": "areas/health.md"},
            label="Open the Health page",
        ),
    )

    assert not result.is_error
    assert app_control.emitted[0].destination == {"kind": "memory", "path": "areas/health.md"}


@pytest.mark.asyncio
async def test_open_in_app_refuses_an_unknown_wiki_page():
    execution, _, app_control = _make_execution(wiki=_StubWikiService({"areas/health.md"}))

    result = await app_open(
        execution,
        AppOpenInput(destination={"kind": "memory", "path": "areas/ghost.md"}, label="Open the page"),
    )

    assert result.is_error
    assert result.outcome.error.code == "not_found"
    assert not app_control.emitted


@pytest.mark.asyncio
async def test_open_in_app_still_opens_memory_without_a_page():
    execution, _, app_control = _make_execution()

    result = await app_open(execution, AppOpenInput(destination={"kind": "memory"}, label="Open memory"))

    assert not result.is_error
    assert app_control.emitted[0].destination == {"kind": "memory"}
