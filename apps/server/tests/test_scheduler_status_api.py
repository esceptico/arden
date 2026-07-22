from fastapi.testclient import TestClient

from arden.server.app import app
from arden.server.runtime import get_runtime


class _Runtime:
    @property
    def automation(self):
        return self

    async def get_scheduler_status(self):
        return {
            "status": "running",
            "started_at": "2026-04-28T00:00:00+00:00",
            "last_tick_at": "2026-04-28T00:01:00+00:00",
            "last_tick_error": None,
            "last_activity_at": "2026-04-28T00:01:00+00:00",
            "running_tasks": 1,
            "registered_handlers": ["knowledge_reflection", "knowledge_retention", "knowledge_health"],
            "store": {
                "observed_at": "2026-04-28T00:02:00+00:00",
                "tasks": {
                    "total": 4,
                    "enabled": 3,
                    "disabled": 1,
                    "running": 1,
                    "due": 0,
                    "next_run_at": "2026-04-28T01:00:00+00:00",
                    "oldest_running_since": "2026-04-28T00:00:00+00:00",
                },
                "event_queue": {
                    "total": 2,
                    "ready": 1,
                    "scheduled": 0,
                    "claimed": 1,
                    "oldest_pending_created_at": "2026-04-28T00:00:00+00:00",
                    "next_attempt_at": None,
                    "oldest_claimed_at": "2026-04-28T00:01:00+00:00",
                },
                "count_state": {
                    "total": 1,
                    "oldest_updated_at": "2026-04-28T00:00:00+00:00",
                },
            },
        }


def test_scheduler_status_endpoint_returns_runtime_and_store_state():
    runtime = _Runtime()
    app.dependency_overrides[get_runtime] = lambda: runtime
    try:
        response = TestClient(app).get("/scheduler/status")
    finally:
        app.dependency_overrides.pop(get_runtime, None)

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "running"
    assert data["running_tasks"] == 1
    assert data["registered_handlers"] == ["knowledge_reflection", "knowledge_retention", "knowledge_health"]
    assert data["store"]["tasks"]["total"] == 4
    assert data["store"]["event_queue"]["claimed"] == 1


def test_scheduler_status_endpoint_has_response_model_in_openapi():
    schema = TestClient(app).get("/openapi.json").json()

    assert schema["paths"]["/scheduler/status"]["get"]["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/SchedulerStatusResponse"
    }
