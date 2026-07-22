from fastapi.testclient import TestClient

from arden.server.app import app
from arden.server.runtime import get_runtime


class _Runtime:
    @property
    def run_registry(self):
        return self

    def get_status(self):
        return {
            "observed_at": "2026-04-28T00:00:00+00:00",
            "total_retained": 3,
            "active_count": 1,
            "active_runs": [
                {
                    "run_id": "run-1",
                    "session_id": "sess-1",
                    "status": "running",
                    "created_at": "2026-04-28T00:00:00+00:00",
                    "updated_at": "2026-04-28T00:01:00+00:00",
                    "age_seconds": 60,
                    "idle_seconds": 0,
                    "message_count": 5,
                    "pending_injections": 2,
                    "approvals_pending": 0,
                    "task_running": True,
                    "drain_task_running": False,
                    "cancelled": False,
                    "backgrounded": False,
                }
            ],
            "background_task_sessions": [
                {
                    "session_id": "sess-1",
                    "pending_tasks": 1,
                }
            ],
        }


def test_chat_runs_status_endpoint_returns_content_free_run_state():
    runtime = _Runtime()
    app.dependency_overrides[get_runtime] = lambda: runtime
    try:
        response = TestClient(app).get("/chat/runs/status")
    finally:
        app.dependency_overrides.pop(get_runtime, None)

    assert response.status_code == 200
    data = response.json()
    assert data["active_count"] == 1
    assert data["active_runs"][0]["pending_injections"] == 2
    assert data["active_runs"][0]["message_count"] == 5
    assert data["background_task_sessions"][0]["pending_tasks"] == 1
    assert "messages" not in data["active_runs"][0]


def test_chat_runs_status_endpoint_reports_backgrounded_status():
    class RuntimeWithBackgrounded(_Runtime):
        def get_status(self):
            status = super().get_status()
            status["active_runs"][0]["status"] = "backgrounded"
            status["active_runs"][0]["backgrounded"] = True
            return status

    app.dependency_overrides[get_runtime] = lambda: RuntimeWithBackgrounded()
    try:
        response = TestClient(app).get("/chat/runs/status")
    finally:
        app.dependency_overrides.pop(get_runtime, None)

    assert response.status_code == 200
    run = response.json()["active_runs"][0]
    assert run["status"] == "backgrounded"
    assert run["backgrounded"] is True


def test_chat_runs_status_endpoint_has_response_model_in_openapi():
    schema = TestClient(app).get("/openapi.json").json()

    assert schema["paths"]["/chat/runs/status"]["get"]["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ChatRunsStatusResponse"
    }
