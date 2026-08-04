from fastapi.testclient import TestClient

from arden.server.app import app
from arden.server.runtime import get_runtime


def _status() -> dict:
    return {
        "status": "quota_blocked",
        "total_bytes": 1_000,
        "reclaimable_bytes": 200,
        "protected_bytes": 800,
        "reclaimed_bytes": 0,
        "max_bytes": 900,
        "target_bytes": 765,
        "checked_at": "2026-08-04T00:00:00+00:00",
        "categories": [
            {
                "id": "chat_history",
                "label": "Chat history",
                "total_bytes": 1_000,
                "reclaimable_bytes": 200,
                "item_count": 1,
                "policy_tier": 2,
                "protection_reason": "Chats require an explicit cleanup plan.",
                "description": "Live chats.",
                "measurement_kind": "physical",
            }
        ],
        "reclaimed_by_category": {},
        "database_reclaim_mode": "incremental",
    }


class _Runtime:
    planned = None
    executed = None
    kept = None

    def storage_status(self):
        return _status()

    async def plan_storage_cleanup(self, request):
        self.planned = request
        return {
            "plan_id": "a" * 64,
            "before_bytes": 1_000,
            "target_bytes": 765,
            "estimated_after_bytes": 700,
            "estimated_reclaimable_bytes": 300,
            "attainable": True,
            "actions": [
                {
                    "tier": 2,
                    "kind": "cold_convert_session",
                    "category_id": "chat_history",
                    "resource_id": "session-1",
                    "estimated_reclaimable_bytes": 300,
                    "destructive": False,
                    "description": "Convert an archived chat to restorable cold storage",
                }
            ],
            "blockers": [],
            "created_at": "2026-08-04T00:00:00+00:00",
        }

    async def execute_storage_cleanup(self, request):
        self.executed = request
        return {
            "plan_id": request.plan_id,
            "reclaimed_bytes": 300,
            "actions_completed": 1,
            "status": _status(),
        }

    async def list_storage_backups(self):
        return [
            {
                "relative_path": "archive/old.db",
                "size_bytes": 20,
                "modified_at": "2026-07-01T00:00:00+00:00",
                "kept": False,
                "expired": True,
            }
        ]

    async def set_storage_backup_keep(self, relative_path, *, keep):
        self.kept = (relative_path, keep)
        return [{**(await self.list_storage_backups())[0], "kept": keep}]


def test_storage_plan_and_execute_are_separate_explicit_steps():
    runtime = _Runtime()
    app.dependency_overrides[get_runtime] = lambda: runtime
    client = TestClient(app)
    payload = {
        "target_gb": 1.0,
        "allow_archived_chats": True,
        "current_session_id": "open-session",
        "pinned_session_ids": ["pinned-session"],
    }
    try:
        plan = client.post("/storage/plan", json=payload)
        execute = client.post("/storage/execute", json={**payload, "plan_id": "a" * 64})
    finally:
        app.dependency_overrides.pop(get_runtime, None)

    assert plan.status_code == 200
    assert plan.json()["actions"][0]["kind"] == "cold_convert_session"
    assert execute.status_code == 200
    assert execute.json()["actions_completed"] == 1
    assert runtime.planned.current_session_id == "open-session"
    assert runtime.executed.plan_id == "a" * 64


def test_storage_backups_support_manual_keep_exceptions():
    runtime = _Runtime()
    app.dependency_overrides[get_runtime] = lambda: runtime
    client = TestClient(app)
    try:
        listed = client.get("/storage/backups")
        kept = client.put(
            "/storage/backups/keep",
            json={"relative_path": "archive/old.db", "keep": True},
        )
    finally:
        app.dependency_overrides.pop(get_runtime, None)

    assert listed.status_code == 200
    assert listed.json()[0]["expired"] is True
    assert kept.status_code == 200
    assert kept.json()[0]["kept"] is True
    assert runtime.kept == ("archive/old.db", True)
