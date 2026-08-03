from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from arden.context.models import SessionData, SessionState
from arden.server.app import app
from arden.server.bus import BusRegistry
from arden.server.deps import get_bus_registry, require_session_service
from arden.server.runtime import get_runtime
from arden.tools.core import EmptyInput
from arden.tools.core.context import IOBridge, RunContext, ToolContext, ToolExecution
from arden.tools.core.registry import ToolRegistry
from arden.tools.goals import BlockGoalInput, block_goal, complete_goal
from tests.helpers import make_text_response


class _EmptyEventStore:
    async def get_latest_session_event_seq(self, session_id: str) -> int:
        return 0

    async def get_latest_session_checkpoint_seq(self, session_id: str) -> int:
        return 0


class _GoalSessionService:
    def __init__(self):
        self.goal: dict | None = None
        self.store = _EmptyEventStore()

    async def load(self, session_id: str | None = None):
        state = SessionState(session_id=session_id or "sess-1", started_at=datetime.now(UTC))
        return SessionData(state=state, messages=[])

    async def set_goal(self, session_id: str, objective: str, token_budget: int | None = None):
        now = datetime.now(UTC).isoformat()
        self.goal = {
            "session_id": session_id,
            "goal_id": "goal-1",
            "objective": objective,
            "status": "active",
            "evidence": [],
            "blocked_reason": None,
            "token_budget": token_budget,
            "tokens_used": 0,
            "time_used_seconds": 0,
            "created_at": now,
            "updated_at": now,
        }
        return self.goal

    async def get_goal(self, session_id: str):
        return self.goal if self.goal and self.goal["session_id"] == session_id else None

    async def update_goal(self, session_id: str, **kwargs):
        if not self.goal or self.goal["session_id"] != session_id:
            return None
        if status := kwargs.get("status"):
            self.goal["status"] = status
        self.goal["blocked_reason"] = kwargs.get("blocked_reason") if self.goal["status"] == "blocked" else None
        if evidence := kwargs.get("evidence"):
            entry = {"text": evidence, "created_at": datetime.now(UTC).isoformat()}
            if kind := kwargs.get("evidence_kind"):
                entry["kind"] = kind
            if blocked_reason := kwargs.get("evidence_blocked_reason"):
                entry["blocked_reason"] = blocked_reason
            self.goal["evidence"].append(entry)
        self.goal["updated_at"] = datetime.now(UTC).isoformat()
        return self.goal

    async def clear_goal(self, session_id: str):
        existed = self.goal is not None and self.goal["session_id"] == session_id
        self.goal = None
        return existed


def test_goal_routes_trim_objective_and_emit_events():
    svc = _GoalSessionService()
    buses = BusRegistry()
    app.dependency_overrides[require_session_service] = lambda: svc
    app.dependency_overrides[get_bus_registry] = lambda: buses
    try:
        response = TestClient(app).post("/sessions/sess-1/goal", json={"objective": "  ship it  "})
    finally:
        app.dependency_overrides.pop(require_session_service, None)
        app.dependency_overrides.pop(get_bus_registry, None)

    assert response.status_code == 200
    assert response.json()["objective"] == "ship it"
    bus = buses.get("sess-1")
    assert bus is not None
    assert bus._recent[-1].event.type.value == "goal_updated"


def test_goal_route_rejects_blank_objective_after_trim():
    svc = _GoalSessionService()
    app.dependency_overrides[get_bus_registry] = lambda: BusRegistry()
    app.dependency_overrides[require_session_service] = lambda: svc
    try:
        response = TestClient(app).post("/sessions/sess-1/goal", json={"objective": "   "})
    finally:
        app.dependency_overrides.pop(require_session_service, None)
        app.dependency_overrides.pop(get_bus_registry, None)

    assert response.status_code == 422
    assert svc.goal is None


def test_goal_proposal_uses_recent_context_without_persisting(monkeypatch):
    class ProposalSessionService(_GoalSessionService):
        async def load(self, session_id: str | None = None):
            state = SessionState(session_id=session_id or "sess-1", started_at=datetime.now(UTC))
            return SessionData(
                state=state,
                messages=[
                    {"role": "user", "content": "we need to fix the checkout retry bug"},
                    {"role": "assistant", "content": "I found the retry path in payments."},
                ],
            )

    class RuntimeStub:
        config = type("Config", (), {"chat_model": "test-model"})()

    class FakeLLM:
        async def complete(self, model, messages, **kwargs):
            assert model == "test-model"
            assert "text the user would put after `/goal`" in messages[0]["content"]
            assert "Reduce their manual typing" in messages[0]["content"]
            assert "Use enough detail for the actual task" in messages[0]["content"]
            assert "Include the success definition" in messages[0]["content"]
            assert "not turn it into a step-by-step checklist" in messages[0]["content"]
            assert "checkout retry bug" in messages[-1]["content"]
            return make_text_response("Goal: Fix the checkout retry bug.")

    svc = ProposalSessionService()
    app.dependency_overrides[require_session_service] = lambda: svc
    app.dependency_overrides[get_runtime] = lambda: RuntimeStub()
    monkeypatch.setattr("arden.server.routers.session.llm_client", FakeLLM(), raising=False)
    try:
        response = TestClient(app).post("/sessions/sess-1/goal/propose")
    finally:
        app.dependency_overrides.pop(require_session_service, None)
        app.dependency_overrides.pop(get_runtime, None)

    assert response.status_code == 200
    assert response.json() == {"objective": "Fix the checkout retry bug."}
    assert svc.goal is None


@pytest.mark.asyncio
async def test_complete_goal_tool_emits_goal_update():
    svc = _GoalSessionService()
    await svc.set_goal("sess-1", "ship it")
    events = []

    async def emit(event):
        events.append(event)

    ctx = ToolContext(
        session_state=SessionState(session_id="sess-1", started_at=datetime.now(UTC)),
        registry=ToolRegistry(),
        run=RunContext(run_id="run-1"),
        io=IOBridge(emit=emit),
        services={"session": svc},
    )
    result = await complete_goal(
        ToolExecution(tool_id="tool-1", tool_name="goal_complete", ctx=ctx),
        EmptyInput(),
    )

    assert not result.is_error
    assert svc.goal is not None
    assert svc.goal["status"] == "complete"
    assert "visible concise completion report" in result.content
    assert events[-1].type.value == "goal_updated"
    assert events[-1].goal["status"] == "complete"


@pytest.mark.asyncio
async def test_block_goal_requires_repeated_same_blocker_before_terminal():
    svc = _GoalSessionService()
    await svc.set_goal("sess-1", "ship it")
    events = []

    async def emit(event):
        events.append(event.goal["status"])

    ctx = ToolContext(
        session_state=SessionState(session_id="sess-1", started_at=datetime.now(UTC)),
        registry=ToolRegistry(),
        run=RunContext(run_id="run-1"),
        io=IOBridge(emit=emit),
        services={"session": svc},
    )
    execution = ToolExecution(tool_id="tool-1", tool_name="goal_block", ctx=ctx)
    args = BlockGoalInput(reason="Need credentials", evidence="Login requires user credentials.")

    first = await block_goal(execution, args)
    second = await block_goal(execution, args)
    third = await block_goal(execution, args)

    assert not first.is_error
    assert not second.is_error
    assert not third.is_error
    assert "Goal remains active" in first.content
    assert "Goal remains active" in second.content
    assert svc.goal is not None
    assert svc.goal["status"] == "blocked"
    assert svc.goal["blocked_reason"] == "Need credentials"
    assert events == ["active", "active", "blocked"]
