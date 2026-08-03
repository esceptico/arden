from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

import arden.tools.todos as todos_module
from arden.context.models import SessionData, SessionState
from arden.server.app import app
from arden.server.deps import require_session_service
from arden.tools.core.context import IOBridge, RunContext, ToolContext, ToolExecution
from arden.tools.core.registry import ToolRegistry


class _TodoSessionService:
    def __init__(self):
        self.override: dict | None = None
        self.current: dict | None = None
        self.cleared = 0

    async def load(self, session_id: str | None = None):
        return SessionData(
            state=SessionState(session_id=session_id or "s1", started_at=datetime.now(UTC)),
            messages=[],
        )

    async def set_todo_override(self, session_id, items, explanation=None):
        self.override = {"items": items, "explanation": explanation, "updated_at": "now"}
        return self.override

    async def get_todo_override(self, session_id):
        return self.override

    async def clear_todo_override(self, session_id):
        self.cleared += 1
        had = self.override is not None
        self.override = None
        return had

    async def set_session_todos(self, session_id, items, explanation=None):
        self.current = {"items": items, "explanation": explanation, "updated_at": "now"}
        return self.current

    async def get_session_todos(self, session_id):
        return self.current

    async def clear_session_todos(self, session_id):
        had = self.current is not None
        self.current = None
        return had


@pytest.mark.asyncio
async def test_update_todos_clears_user_override():
    svc = _TodoSessionService()
    svc.override = {"items": [{"content": "user-added", "status": "pending"}]}
    ctx = ToolContext(
        session_state=SessionState(session_id="s1", started_at=datetime.now(UTC)),
        registry=ToolRegistry(),
        run=RunContext(run_id="r1", current_depth=0, max_depth=3),
        io=IOBridge(),
        services={"session": svc},
    )
    execution = ToolExecution(tool_id="t", tool_name="todo_update", ctx=ctx)

    result = await todos_module.todo_update(
        execution,
        todos_module.TodoUpdateInput(items=[todos_module.TodoItemInput(content="agent task", status="pending")]),
    )

    assert "updated" in result.content.lower()
    # The agent's list supersedes the user's manual edit.
    assert svc.cleared == 1
    assert svc.override is None
    # An unfinished list lands in the session-level slot the sidebar reads.
    assert svc.current is not None
    assert svc.current["items"] == [{"content": "agent task", "status": "pending"}]


@pytest.mark.asyncio
async def test_update_todos_all_completed_retires_the_list():
    svc = _TodoSessionService()
    svc.current = {"items": [{"content": "agent task", "status": "in_progress"}]}
    ctx = ToolContext(
        session_state=SessionState(session_id="s1", started_at=datetime.now(UTC)),
        registry=ToolRegistry(),
        run=RunContext(run_id="r1", current_depth=0, max_depth=3),
        io=IOBridge(),
        services={"session": svc},
    )
    execution = ToolExecution(tool_id="t", tool_name="todo_update", ctx=ctx)

    await todos_module.todo_update(
        execution,
        todos_module.TodoUpdateInput(items=[todos_module.TodoItemInput(content="agent task", status="completed")]),
    )

    # A fully-completed list retires the session slot instead of persisting.
    assert svc.current is None


def test_todo_override_endpoints_roundtrip():
    svc = _TodoSessionService()
    app.dependency_overrides[require_session_service] = lambda: svc
    try:
        client = TestClient(app)
        r = client.post("/sessions/s1/todo", json={"items": [{"content": "buy milk", "status": "pending"}]})
        assert r.status_code == 200
        assert r.json()["items"] == [{"content": "buy milk", "status": "pending"}]

        r = client.get("/sessions/s1/todo")
        assert r.status_code == 200
        assert r.json()["items"][0]["content"] == "buy milk"
        assert r.json()["edited"] is True

        r = client.delete("/sessions/s1/todo")
        assert r.status_code == 200
        assert r.json()["status"] == "cleared"
        assert client.get("/sessions/s1/todo").json() is None
    finally:
        app.dependency_overrides.pop(require_session_service, None)


def test_todo_effective_get_and_dismiss():
    svc = _TodoSessionService()
    svc.current = {"items": [{"content": "agent task", "status": "pending"}], "explanation": None, "updated_at": "now"}
    app.dependency_overrides[require_session_service] = lambda: svc
    try:
        client = TestClient(app)
        # No override: GET serves the agent's current list, unedited.
        r = client.get("/sessions/s1/todo")
        assert r.json()["items"][0]["content"] == "agent task"
        assert r.json()["edited"] is False

        # An override wins over the agent list.
        client.post("/sessions/s1/todo", json={"items": [{"content": "user task", "status": "pending"}]})
        r = client.get("/sessions/s1/todo")
        assert r.json()["items"][0]["content"] == "user task"
        assert r.json()["edited"] is True

        # Resetting the override falls back to the agent list.
        r = client.delete("/sessions/s1/todo/override")
        assert r.json()["status"] == "cleared"
        assert client.get("/sessions/s1/todo").json()["items"][0]["content"] == "agent task"

        # Dismiss clears everything.
        r = client.delete("/sessions/s1/todo")
        assert r.json()["status"] == "cleared"
        assert client.get("/sessions/s1/todo").json() is None
        assert svc.current is None and svc.override is None
    finally:
        app.dependency_overrides.pop(require_session_service, None)


def test_todo_override_rejects_bad_status():
    svc = _TodoSessionService()
    app.dependency_overrides[require_session_service] = lambda: svc
    try:
        r = TestClient(app).post("/sessions/s1/todo", json={"items": [{"content": "x", "status": "bogus"}]})
        assert r.status_code == 422
    finally:
        app.dependency_overrides.pop(require_session_service, None)
