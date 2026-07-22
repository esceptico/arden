from fastapi.testclient import TestClient

from arden.server.app import app
from arden.server.runtime import get_runtime


class _Store:
    def __init__(self):
        self.cancelled = []
        self.status = "running"
        self.result_text = None
        self.result_ref = None

    async def list_background_agent_runs(self, session_id, include_terminal=True):
        return [
            {
                "task_id": "bg-1",
                "child_run_id": "bg-1",
                "session_id": session_id,
                "parent_run_id": "run-1",
                "parent_tool_call_id": "call-background",
                "agent_type": "background_research",
                "wait": False,
                "status": self.status,
                "command": "research",
                "detail": "read files",
                "result_ref": self.result_ref,
                "created_at": "2026-05-15T00:00:00+00:00",
                "started_at": "2026-05-15T00:00:00+00:00",
                "updated_at": "2026-05-15T00:00:01+00:00",
                "ended_at": None,
                "cancel_requested_at": None,
                "notified_at": None,
            }
        ]

    async def request_background_agent_cancel(self, session_id, task_id):
        self.cancelled.append((session_id, task_id))
        return True

    async def get_background_agent_result(self, session_id, task_id):
        return self.result_text


class _SessionService:
    def __init__(self):
        self.store = _Store()


class _Runtime:
    def __init__(self):
        self.session_service = _SessionService()
        self.steered = []
        self.agent_running = True

    @property
    def run_registry(self):
        return self

    def get_background_registry(self, session_id):
        return self

    def list_pending(self):
        return [("bg-1", "research")]

    def cancel(self, task_id):
        return None

    def queue_steering(self, task_id, text):
        self.steered.append((task_id, text))
        return self.agent_running

    def child_session(self, task_id):
        return None

    def cancel_subtree(self, child_session_id):
        return []


def test_background_tasks_endpoint_returns_durable_snapshot():
    runtime = _Runtime()
    app.dependency_overrides[get_runtime] = lambda: runtime
    try:
        response = TestClient(app).get("/chat/background-tasks?session_id=sess-1")
    finally:
        app.dependency_overrides.pop(get_runtime, None)

    assert response.status_code == 200
    assert response.json()["tasks"][0]["status"] == "running"
    assert response.json()["tasks"][0]["detail"] == "read files"
    assert response.json()["tasks"][0]["child_run_id"] == "bg-1"
    assert response.json()["tasks"][0]["parent_tool_call_id"] == "call-background"
    assert response.json()["tasks"][0]["agent_type"] == "background_research"
    assert response.json()["tasks"][0]["wait"] is False


def test_background_task_cancel_requests_durable_cancel():
    runtime = _Runtime()
    app.dependency_overrides[get_runtime] = lambda: runtime
    try:
        response = TestClient(app).post("/chat/background-tasks/bg-1/cancel?session_id=sess-1")
    finally:
        app.dependency_overrides.pop(get_runtime, None)

    assert response.status_code == 200
    assert response.json()["status"] == "cancel_requested"
    assert runtime.session_service.store.cancelled == [("sess-1", "bg-1")]


def test_child_agents_endpoint_returns_same_durable_snapshot():
    runtime = _Runtime()
    app.dependency_overrides[get_runtime] = lambda: runtime
    try:
        response = TestClient(app).get("/chat/child-agents?session_id=sess-1")
    finally:
        app.dependency_overrides.pop(get_runtime, None)

    assert response.status_code == 200
    assert response.json()["tasks"][0]["child_run_id"] == "bg-1"
    assert response.json()["tasks"][0]["parent_tool_call_id"] == "call-background"
    assert response.json()["tasks"][0]["agent_type"] == "background_research"


def test_child_agent_cancel_requests_same_durable_cancel():
    runtime = _Runtime()
    app.dependency_overrides[get_runtime] = lambda: runtime
    try:
        response = TestClient(app).post("/chat/child-agents/bg-1/cancel?session_id=sess-1")
    finally:
        app.dependency_overrides.pop(get_runtime, None)

    assert response.status_code == 200
    assert response.json()["status"] == "cancel_requested"
    assert response.json()["child_run_id"] == "bg-1"
    assert runtime.session_service.store.cancelled == [("sess-1", "bg-1")]


def test_child_agent_result_endpoint_returns_running_state_without_result():
    runtime = _Runtime()
    app.dependency_overrides[get_runtime] = lambda: runtime
    try:
        response = TestClient(app).get("/chat/child-agents/bg-1/result?session_id=sess-1")
    finally:
        app.dependency_overrides.pop(get_runtime, None)

    assert response.status_code == 200
    assert response.json() == {
        "task_id": "bg-1",
        "child_run_id": "bg-1",
        "session_id": "sess-1",
        "status": "running",
        "terminal": False,
        "result": None,
        "result_ref": None,
    }


def test_child_agent_result_endpoint_returns_durable_result():
    runtime = _Runtime()
    runtime.session_service.store.status = "completed"
    runtime.session_service.store.result_text = "final report"
    runtime.session_service.store.result_ref = "bg_results/bg-1.txt"
    app.dependency_overrides[get_runtime] = lambda: runtime
    try:
        response = TestClient(app).get("/chat/child-agents/bg-1/result?session_id=sess-1")
    finally:
        app.dependency_overrides.pop(get_runtime, None)

    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    assert response.json()["terminal"] is True
    assert response.json()["result"] == "final report"
    assert response.json()["result_ref"] == "bg_results/bg-1.txt"


def test_inject_child_agent_delivers_to_running_agent():
    runtime = _Runtime()
    app.dependency_overrides[get_runtime] = lambda: runtime
    try:
        response = TestClient(app).post(
            "/chat/child-agents/bg-1/inject?session_id=sess-1",
            json={"message": "also check pricing"},
        )
    finally:
        app.dependency_overrides.pop(get_runtime, None)

    assert response.status_code == 202
    assert response.json() == {"status": "delivered", "child_run_id": "bg-1"}
    assert runtime.steered == [("bg-1", "also check pricing")]


def test_inject_child_agent_404_when_not_running():
    runtime = _Runtime()
    runtime.agent_running = False
    app.dependency_overrides[get_runtime] = lambda: runtime
    try:
        response = TestClient(app).post(
            "/chat/child-agents/bg-1/inject?session_id=sess-1",
            json={"message": "too late"},
        )
    finally:
        app.dependency_overrides.pop(get_runtime, None)

    assert response.status_code == 404


def test_inject_child_agent_rejects_empty_message():
    runtime = _Runtime()
    app.dependency_overrides[get_runtime] = lambda: runtime
    try:
        response = TestClient(app).post(
            "/chat/child-agents/bg-1/inject?session_id=sess-1",
            json={"message": ""},
        )
    finally:
        app.dependency_overrides.pop(get_runtime, None)

    assert response.status_code == 422
    assert runtime.steered == []


def test_child_agent_result_endpoint_wait_timeout_returns_current_state():
    runtime = _Runtime()
    app.dependency_overrides[get_runtime] = lambda: runtime
    try:
        response = TestClient(app).get(
            "/chat/child-agents/bg-1/result?session_id=sess-1&wait=true&timeout_seconds=0.001"
        )
    finally:
        app.dependency_overrides.pop(get_runtime, None)

    assert response.status_code == 200
    assert response.json()["status"] == "running"
    assert response.json()["terminal"] is False
    assert response.json()["result"] is None
