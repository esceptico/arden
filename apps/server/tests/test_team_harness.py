"""Tests for the subagent team harness: the shared child identity fragment,
the live <agent_roster> note, the app_followup_task tool (queue vs wake), and the
failure-path truncation + guidance in deliver_result."""

import asyncio
import contextlib
from datetime import UTC, datetime

import pytest

import arden.tools.core.context as context_module
from arden.agent import Result, StopReason, Usage
from arden.context.models import SessionData, SessionState
from arden.core import spawner as spawner_module
from arden.core.isolation import IsolationLevel
from arden.core.prompts import TEAM_CHILD_BLOCK
from arden.core.spawner import create_spawn_fn
from arden.tools.app_control import (
    FollowupTaskInput,
    approve_followup_task,
    followup_task,
    followup_task_tool,
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
from tests.helpers import make_executor


@pytest.mark.asyncio
async def test_child_system_prompt_carries_the_team_identity_fragment(monkeypatch):
    captured = {}

    class FakeAgent:
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


def _live(registry: BackgroundTaskRegistry, task_id: str, **reserve_kwargs) -> asyncio.Task:
    registry.reserve(task_id, command="Agent", limit=None, **reserve_kwargs)
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
        assert "test::a1" in note["content"]
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
            assert "test::a1" in note["content"]
            assert "test::b2" in note["content"]
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

    async def is_archived(self, session_id: str) -> bool:
        return False

    async def list_sessions(self, limit: int = 20, **kwargs) -> list[dict]:
        return [{"session_id": sid} for sid in list(self._sessions)[:limit]]


class _StubAppControl:
    def __init__(self):
        self.dispatched: list[dict] = []

    async def dispatch(self, session_id: str, message: str, *, client_id: str) -> str | None:
        self.dispatched.append({"session_id": session_id, "message": message, "client_id": client_id})
        return "run-woken"


def _agent_session(session_id: str, parent_session_id: str | None) -> SessionData:
    state = SessionState(session_id=session_id, started_at=datetime.now(UTC), name="Agent")
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

        result = await followup_task(execution, FollowupTaskInput(session_id="cur::a1", task="also audit invoices"))

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

    result = await followup_task(execution, FollowupTaskInput(session_id="cur::a1", task="re-check the totals"))

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

    result = await followup_task(execution, FollowupTaskInput(session_id="other::a1", task="do things"))

    assert result.is_error
    assert result.outcome.error.code == "invalid_arguments"
    assert app_control.dispatched == []


@pytest.mark.asyncio
async def test_followup_task_approval_waived_only_for_live_agents():
    registry = BackgroundTaskRegistry(session_id="cur")
    task = _live(registry, "agent-1", child_session_id="cur::a1")
    try:
        execution, _ = _execution(registry)

        live = await approve_followup_task(execution, FollowupTaskInput(session_id="cur::a1", task="go"))
        idle = await approve_followup_task(execution, FollowupTaskInput(session_id="cur::b2", task="go"))

        assert live is APPROVAL_WAIVED
        assert isinstance(idle, ApprovalInfo)
    finally:
        await _cancel(task)


def test_followup_task_policy_mirrors_send_message():
    assert followup_task_tool.policy.requires_approval is True
    assert followup_task_tool.policy.allow_approval_bypass is False
    assert followup_task_tool.policy.permissions == frozenset({"session", "app_control"})


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
        child_session_id="cur::a1",
    )
    assert len(captured) == 1
    return captured[0]["content"]


@pytest.mark.asyncio
async def test_deliver_result_truncates_failed_bodies_and_appends_guidance(tmp_path, monkeypatch):
    monkeypatch.setattr(context_module, "RESULT_BASE", tmp_path)
    registry = BackgroundTaskRegistry(session_id="cur")
    content = await _delivered(registry, status="failed", result="x" * 10_000)

    assert "[truncated]" in content
    assert "x" * 3_600 in content
    assert "x" * 3_601 not in content
    assert "This agent's run failed." in content
    assert 'app_followup_task(session_id="cur::a1")' in content


@pytest.mark.asyncio
async def test_deliver_result_never_truncates_completed_results(tmp_path, monkeypatch):
    monkeypatch.setattr(context_module, "RESULT_BASE", tmp_path)
    registry = BackgroundTaskRegistry(session_id="cur")
    body = "y" * 10_000
    content = await _delivered(registry, status="completed", result=body)

    assert body in content
    assert "[truncated]" not in content
    assert "app_followup_task(" not in content
