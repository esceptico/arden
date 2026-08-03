from fastapi.testclient import TestClient

from arden.server.app import app
from arden.server.runtime import get_runtime


class _Config:
    has_any_model = True
    api_key_hash = None


class _Runtime:
    connected = True
    config = _Config()
    replayed_event_ids = None
    pruned = None

    @property
    def automation(self):
        return self

    def config_status(self):
        return {
            "config_version": 1,
            "config_loaded_at": "2026-04-28T00:00:00+00:00",
        }

    def warmup_status(self):
        return {
            "status": "running",
            "phase": "runtime.index_sync",
            "error": None,
            "capabilities": {
                "core": True,
                "search": False,
                "wiki_health": False,
                "automations": False,
            },
        }

    def storage_status(self):
        return {
            "status": "ok",
            "total_bytes": 100,
            "reclaimable_bytes": 0,
            "protected_bytes": 100,
            "reclaimed_bytes": 0,
            "max_bytes": 1000,
            "target_bytes": 850,
            "checked_at": "2026-04-28T00:00:00+00:00",
        }

    async def get_outbox_health(self):
        return {
            "worker_running": True,
            "pending": 2,
            "ready": 1,
            "running": 0,
            "dead": 1,
        }

    async def get_outbox_status(self):
        return {
            "status": "running",
            "worker": {
                "running": True,
                "worker_id": "test-worker",
            },
            "events": {
                "observed_at": "2026-04-28T00:00:00+00:00",
                "total": 3,
                "ready": 1,
                "scheduled": 1,
                "by_status": {
                    "pending": 2,
                    "running": 0,
                    "completed": 0,
                    "dead": 1,
                },
                "by_event_type": {
                    "run.completed": {
                        "pending": 2,
                        "running": 0,
                        "completed": 0,
                        "dead": 1,
                    }
                },
                "oldest_pending_created_at": "2026-04-28T00:00:00+00:00",
                "next_pending_available_at": "2026-04-28T00:00:00+00:00",
                "oldest_running_locked_at": None,
                "newest_dead_updated_at": "2026-04-28T00:00:00+00:00",
                "recent_dead": [],
            },
        }

    async def replay_outbox_dead_events(self, event_ids):
        self.replayed_event_ids = event_ids
        return {
            "status": "queued",
            "requested": event_ids,
            "replayed": event_ids,
            "missing": [],
            "skipped": [],
        }

    async def prune_outbox_completed(self, *, before, limit):
        self.pruned = {"before": before, "limit": limit}
        return {
            "status": "deleted",
            "deleted": 7,
            "before": before.isoformat(),
            "limit": limit,
        }


def test_health_includes_outbox_summary():
    runtime = _Runtime()
    app.dependency_overrides[get_runtime] = lambda: runtime
    try:
        response = TestClient(app).get("/health")
    finally:
        app.dependency_overrides.pop(get_runtime, None)

    assert response.status_code == 200
    data = response.json()
    assert data["config_version"] == 1
    assert data["config_loaded_at"] == "2026-04-28T00:00:00+00:00"
    assert data["outbox"] == {
        "worker_running": True,
        "pending": 2,
        "ready": 1,
        "running": 0,
        "dead": 1,
    }
    assert data["status"] == "ok"
    assert data["warmup"]["status"] == "running"
    assert data["warmup"]["capabilities"]["core"] is True
    assert data["storage"]["protected_bytes"] == 100
    assert data["warmup"]["capabilities"]["automations"] is False


def test_outbox_status_endpoint_returns_detailed_state():
    runtime = _Runtime()
    app.dependency_overrides[get_runtime] = lambda: runtime
    try:
        response = TestClient(app).get("/outbox/status")
    finally:
        app.dependency_overrides.pop(get_runtime, None)

    assert response.status_code == 200
    assert response.json()["events"]["by_status"]["dead"] == 1
    assert response.json()["worker"]["worker_id"] == "test-worker"


def test_outbox_endpoints_have_response_models_in_openapi():
    schema = TestClient(app).get("/openapi.json").json()

    assert schema["paths"]["/outbox/status"]["get"]["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/OutboxStatusResponse"
    }
    assert schema["paths"]["/outbox/dead/replay"]["post"]["responses"]["200"]["content"]["application/json"][
        "schema"
    ] == {"$ref": "#/components/schemas/OutboxReplayResponse"}
    assert schema["paths"]["/outbox/completed"]["delete"]["responses"]["200"]["content"]["application/json"][
        "schema"
    ] == {"$ref": "#/components/schemas/OutboxPruneResponse"}


def test_replay_outbox_dead_events_requires_explicit_ids():
    runtime = _Runtime()
    app.dependency_overrides[get_runtime] = lambda: runtime
    try:
        response = TestClient(app).post("/outbox/dead/replay", json={"event_ids": [3, 5]})
    finally:
        app.dependency_overrides.pop(get_runtime, None)

    assert response.status_code == 200
    assert runtime.replayed_event_ids == [3, 5]
    assert response.json()["replayed"] == [3, 5]


def test_replay_outbox_dead_events_rejects_empty_id_list():
    runtime = _Runtime()
    app.dependency_overrides[get_runtime] = lambda: runtime
    try:
        response = TestClient(app).post("/outbox/dead/replay", json={"event_ids": []})
    finally:
        app.dependency_overrides.pop(get_runtime, None)

    assert response.status_code == 422
    assert runtime.replayed_event_ids is None


def test_prune_outbox_completed_passes_cutoff_and_limit():
    runtime = _Runtime()
    app.dependency_overrides[get_runtime] = lambda: runtime
    try:
        response = TestClient(app).delete("/outbox/completed?older_than_days=14&limit=25")
    finally:
        app.dependency_overrides.pop(get_runtime, None)

    assert response.status_code == 200
    assert response.json()["deleted"] == 7
    assert response.json()["older_than_days"] == 14
    assert response.json()["limit"] == 25
    assert runtime.pruned["limit"] == 25
