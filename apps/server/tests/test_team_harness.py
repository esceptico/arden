"""Tests for the subagent team harness and bounded result delivery."""

import asyncio
import contextlib
import json
from datetime import UTC, datetime

import pytest

import arden.tools.core.background_tasks as background_tasks_module
from arden.agent import AgentHooks, Result, StopReason, Usage
from arden.context.models import SessionData, SessionState
from arden.core import spawner as spawner_module
from arden.core.isolation import IsolationLevel
from arden.core.prompts import TEAM_CHILD_BLOCK
from arden.core.public_refs import public_ref
from arden.core.spawner import create_spawn_fn
from arden.tools.app_control import (
    AppFollowupTaskInput,
    app_followup_task,
    app_followup_task_tool,
    approve_app_followup_task,
)
from arden.tools.core.background_tasks import BackgroundTaskRegistry
from arden.tools.core.context import (
    IOBridge,
    RunContext,
    ToolContext,
    ToolExecution,
)
from arden.tools.core.registry import ToolRegistry
from arden.tools.core.types import APPROVAL_WAIVED, ApprovalInfo
from tests.helpers import make_executor


@pytest.mark.asyncio
async def test_child_system_prompt_carries_the_team_identity_fragment(monkeypatch):
    captured = {}

    class FakeAgent:
        def __init__(self):
            self.hooks = AgentHooks()

        async def stream(self, messages):
            captured["messages"] = messages
            yield Result(text="done", stop_reason=StopReason.END_TURN, steps=1, usage=Usage())

    monkeypatch.setattr(spawner_module, "Agent", lambda **kwargs: FakeAgent())

    executor = make_executor()
    ctx = ToolContext(
        session_state=SessionState(session_id="test", started_at=datetime.now(UTC)),
        registry=executor.registry,
        run=RunContext(run_id="run-1", current_depth=0, max_depth=3),
        io=IOBridge(),
        background_tasks=BackgroundTaskRegistry(session_id="test"),
    )
    spawn = create_spawn_fn(executor=executor, model="test-model", max_depth=3, current_depth=0)

    result = await spawn(ctx, "child task", system_prompt="child persona", isolation=IsolationLevel.SHARED)

    assert result.text == "done"
    prompt = captured["messages"][0]["content"]
    assert prompt.startswith("child persona")
    assert TEAM_CHILD_BLOCK in prompt
    # The envelope names the child must recognize are stated verbatim.
    assert "<steering_message>" in prompt
    assert "<app_followup_task>" in prompt
    assert "<background_agent_result" in prompt
    assert "<agent_roster>" in prompt


def _agent_ref(task_id: str) -> str:
    return public_ref("agent", task_id, empty_slug="agent")


def _live(registry: BackgroundTaskRegistry, task_id: str, **reserve_kwargs) -> asyncio.Task:
    registry.reserve(task_id, command="Agent", limit=None, agent_ref=_agent_ref(task_id), **reserve_kwargs)
    task = asyncio.create_task(asyncio.sleep(3600))
    registry.register(task_id, task, command="Agent")
    return task


async def _cancel(*tasks: asyncio.Task) -> None:
    for task in tasks:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


@pytest.mark.asyncio
async def test_roster_note_renders_live_children_and_only_on_change():
    registry = BackgroundTaskRegistry(session_id="test")
    task = _live(
        registry,
        "agent-1",
        child_session_id="test::a1",
        summary="Audit the billing pipeline",
        agent_type="research",
    )
    try:
        note = registry.roster_note_if_changed()
        assert note is not None
        assert note["role"] == "user"
        assert note["is_meta"] is True
        assert "<agent_roster>" in note["content"]
        assert _agent_ref("agent-1") in note["content"]
        assert "research" in note["content"]
        assert "running" in note["content"]
        assert "Audit the billing pipeline" in note["content"]

        # Unchanged roster → no repeat note.
        assert registry.roster_note_if_changed() is None

        # A new spawn changes the set → a fresh note listing both.
        second = _live(registry, "agent-2", child_session_id="test::b2", summary="Second task", agent_type="explorer")
        try:
            note = registry.roster_note_if_changed()
            assert note is not None
            assert _agent_ref("agent-1") in note["content"]
            assert _agent_ref("agent-2") in note["content"]
        finally:
            await _cancel(second)
    finally:
        await _cancel(task)
    # Roster emptied: the signature updates silently — no "nothing running" spam.
    assert registry.roster_note_if_changed() is None


class _StubSessionService:
    def __init__(self, sessions: dict[str, SessionData]):
        self._sessions = sessions

    async def load(self, session_id: str) -> SessionData | None:
        return self._sessions.get(session_id)

    async def resolve_session_ref(self, session_ref: str, **_kwargs) -> str | None:
        return next(
            (session_id for session_id, data in self._sessions.items() if data.state.public_ref == session_ref), None
        )

    async def is_archived(self, session_id: str) -> bool:
        return False

    async def list_sessions(self, limit: int = 20, **kwargs) -> list[dict]:
        return [{"session_ref": data.state.public_ref} for data in list(self._sessions.values())[:limit]]


class _StubAppControl:
    def __init__(self):
        self.dispatched: list[dict] = []

    async def dispatch(self, session_id: str, message: str, *, client_id: str) -> str | None:
        self.dispatched.append({"session_id": session_id, "message": message, "client_id": client_id})
        return "run-woken"


def _agent_session(session_id: str, parent_session_id: str | None) -> SessionData:
    state = SessionState(
        session_id=session_id,
        public_ref=_agent_ref(session_id),
        started_at=datetime.now(UTC),
        name="Agent",
    )
    state.session_type = "agent"
    state.parent_session_id = parent_session_id
    return SessionData(state=state, messages=[])


def _execution(
    registry: BackgroundTaskRegistry,
    sessions: dict[str, SessionData] | None = None,
) -> tuple[ToolExecution, _StubAppControl]:
    app_control = _StubAppControl()
    ctx = ToolContext(
        session_state=SessionState(session_id="cur", started_at=datetime.now(UTC)),
        registry=ToolRegistry(),
        run=RunContext(run_id="run-1"),
        io=IOBridge(),
        background_tasks=registry,
        services={"session": _StubSessionService(sessions or {}), "app_control": app_control},
    )
    return ToolExecution(tool_id="t1", tool_name="app_followup_task", ctx=ctx), app_control


@pytest.mark.asyncio
async def test_followup_task_queues_into_a_live_agents_inbox():
    registry = BackgroundTaskRegistry(session_id="cur")
    task = _live(registry, "agent-1", child_session_id="cur::a1")
    try:
        execution, app_control = _execution(registry)

        result = await app_followup_task(
            execution, AppFollowupTaskInput(agent_ref=_agent_ref("agent-1"), task="also audit invoices")
        )

        assert not result.is_error
        assert app_control.dispatched == []
        drained = registry.drain_injections("agent-1")
        assert len(drained) == 1
        assert "<app_followup_task>" in drained[0]["content"]
        assert "also audit invoices" in drained[0]["content"]
    finally:
        await _cancel(task)


@pytest.mark.asyncio
async def test_followup_task_wakes_a_finished_agent_with_a_hidden_continuation():
    registry = BackgroundTaskRegistry(session_id="cur")
    execution, app_control = _execution(registry, sessions={"cur::a1": _agent_session("cur::a1", "cur")})

    result = await app_followup_task(
        execution, AppFollowupTaskInput(agent_ref=_agent_ref("cur::a1"), task="re-check the totals")
    )

    assert not result.is_error
    assert len(app_control.dispatched) == 1
    dispatched = app_control.dispatched[0]
    assert dispatched["session_id"] == "cur::a1"
    assert "<app_followup_task>" in dispatched["message"]
    assert "re-check the totals" in dispatched["message"]
    # bg: prefix = the continuation run's input is meta-hidden in the transcript.
    assert dispatched["client_id"].startswith("bg:")


@pytest.mark.asyncio
async def test_followup_task_refuses_a_session_that_is_not_your_agent():
    registry = BackgroundTaskRegistry(session_id="cur")
    execution, app_control = _execution(registry, sessions={"other::a1": _agent_session("other::a1", "other")})

    result = await app_followup_task(
        execution, AppFollowupTaskInput(agent_ref=_agent_ref("other::a1"), task="do things")
    )

    assert result.is_error
    assert result.outcome.error.code == "invalid_arguments"
    assert app_control.dispatched == []


@pytest.mark.asyncio
async def test_followup_task_approval_waived_only_for_live_agents():
    registry = BackgroundTaskRegistry(session_id="cur")
    task = _live(registry, "agent-1", child_session_id="cur::a1")
    try:
        execution, _ = _execution(registry)

        live = await approve_app_followup_task(
            execution, AppFollowupTaskInput(agent_ref=_agent_ref("agent-1"), task="go")
        )
        idle = await approve_app_followup_task(
            execution, AppFollowupTaskInput(agent_ref=_agent_ref("cur::b2"), task="go")
        )

        assert live is APPROVAL_WAIVED
        assert isinstance(idle, ApprovalInfo)
    finally:
        await _cancel(task)


def test_followup_task_policy_mirrors_send_message():
    assert app_followup_task_tool.policy.requires_approval is True
    assert app_followup_task_tool.policy.allow_approval_bypass is False
    assert app_followup_task_tool.policy.permissions == frozenset({"session", "app_control"})


async def _delivered(registry: BackgroundTaskRegistry, *, status: str, result: str) -> str:
    captured: list[dict] = []

    async def on_result(messages: list[dict]) -> None:
        captured.extend(messages)

    registry.on_result = on_result
    await registry.deliver_result(
        task_id="agent-1",
        result=result,
        label="Agent",
        status=status,
        emit=None,
        agent_ref=_agent_ref("agent-1"),
        child_session_id="cur::a1",
    )
    assert len(captured) == 1
    return captured[0]["content"]


@pytest.mark.asyncio
async def test_deliver_result_truncates_failed_bodies_and_appends_guidance(tmp_path, monkeypatch):
    monkeypatch.setattr(background_tasks_module, "RESULT_BASE", tmp_path)
    registry = BackgroundTaskRegistry(session_id="cur")
    content = await _delivered(registry, status="failed", result="x" * 10_000)

    assert "[truncated]" in content
    assert "x" * 3_600 in content
    assert "x" * 3_601 not in content
    assert "This agent's run failed." in content
    assert f'app_followup_task(agent_ref="{_agent_ref("agent-1")}")' in content


@pytest.mark.asyncio
async def test_deliver_result_keeps_completed_results_under_the_bound(tmp_path, monkeypatch):
    monkeypatch.setattr(background_tasks_module, "RESULT_BASE", tmp_path)
    registry = BackgroundTaskRegistry(session_id="cur")
    body = "y" * 10_000
    content = await _delivered(registry, status="completed", result=body)

    assert body in content
    assert "[truncated]" not in content
    assert "app_followup_task(" not in content


@pytest.mark.asyncio
async def test_deliver_result_bounds_large_completion_after_persisting_exact_body(tmp_path, monkeypatch):
    monkeypatch.setattr(background_tasks_module, "RESULT_BASE", tmp_path)
    order: list[tuple[str, object]] = []

    async def record_event(**kwargs):
        order.append(("record", kwargs["result_text"]))

    async def on_result(messages: list[dict]) -> None:
        order.append(("inject", messages[0]["content"]))

    registry = BackgroundTaskRegistry(
        session_id="cur",
        record_event=record_event,
        on_result=on_result,
    )
    body = "H" * 30_000 + "M" * 30_000 + "T" * 30_000

    await registry.deliver_result(
        task_id="agent-1",
        result=body,
        label="Agent",
        status="completed",
        emit=None,
        agent_ref=_agent_ref("agent-1"),
        child_session_id="cur::a1",
    )

    assert order[0] == ("record", body)
    assert order[1][0] == "inject"
    content = order[1][1]
    assert isinstance(content, str)
    assert len(content) <= background_tasks_module._COMPLETED_NOTIFICATION_CHAR_LIMIT
    assert f'agent_ref="{_agent_ref("agent-1")}"' in content
    assert f'result_ref="background://{_agent_ref("agent-1")}"' in content
    assert "H" * 1_000 in content
    assert "M" * 1_000 not in content
    assert "T" * 1_000 in content
    assert "[Middle omitted from this bounded completion.]" in content
    assert f'agent_result_read(agent_ref="{_agent_ref("agent-1")}", offset=0, limit=4000)' in content
    assert await registry.read_background_result("agent-1") == body


@pytest.mark.asyncio
async def test_deliver_result_bounds_late_steering_and_preserves_it_durably(tmp_path, monkeypatch):
    monkeypatch.setattr(background_tasks_module, "RESULT_BASE", tmp_path)
    recorded: list[str] = []
    injected: list[str] = []

    async def record_event(**kwargs):
        recorded.append(kwargs["result_text"])

    async def on_result(messages: list[dict]) -> None:
        injected.append(messages[0]["content"])

    steering = [f"<steering_message>\n{char * 20_000}\n</steering_message>" for char in ("A", "B", "C")]
    registry = BackgroundTaskRegistry(
        session_id="cur",
        record_event=record_event,
        on_result=on_result,
    )

    await registry.deliver_result(
        task_id="agent-1",
        result="child result",
        label="Agent",
        status="completed",
        emit=None,
        agent_ref=_agent_ref("agent-1"),
        undelivered_steering=steering,
    )

    durable = json.loads(recorded[0])
    assert durable == {
        "format": "background_completion_v1",
        "result": "child result",
        "undelivered_steering": steering,
    }
    assert await registry.read_background_result("agent-1") == recorded[0]
    assert len(injected[0]) <= background_tasks_module._COMPLETED_NOTIFICATION_CHAR_LIMIT
    assert "A" * 1_000 in injected[0]
    assert "C" * 1_000 in injected[0]
    assert "[Middle omitted from this bounded completion.]" in injected[0]
    assert f'agent_result_read(agent_ref="{_agent_ref("agent-1")}", offset=0, limit=4000)' in injected[0]

    await registry.deliver_result(
        task_id="agent-2",
        result="failed child result",
        label="Agent",
        status="failed",
        emit=None,
        agent_ref=_agent_ref("agent-2"),
        undelivered_steering=steering,
    )

    assert json.loads(recorded[1])["undelivered_steering"] == steering
    assert len(injected[1]) <= background_tasks_module._COMPLETED_NOTIFICATION_CHAR_LIMIT
    assert "[truncated]" in injected[1]
    assert f'agent_result_read(agent_ref="{_agent_ref("agent-2")}", offset=0, limit=4000)' in injected[1]
