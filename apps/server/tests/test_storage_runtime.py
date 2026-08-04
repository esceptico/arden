from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from arden.server.runtime.core import Runtime
from arden.server.schemas import StorageExecuteRequest
from arden.server.state import RunRegistry
from arden.storage_budget import StorageCleanupPlan, StoragePlanAction


class _Store:
    def __init__(self):
        self.receipts: list[dict] = []

    async def incremental_vacuum(self):
        return 0

    async def record_storage_cleanup_run(self, **receipt):
        self.receipts.append(receipt)
        return "storage-test"


class _Sessions:
    def __init__(self):
        self.store = _Store()
        self.deleted: list[str] = []

    async def permanently_delete_current(self, session_id: str):
        self.deleted.append(session_id)
        return True


@pytest.mark.asyncio
async def test_execute_revalidates_session_protections_after_plan():
    action = StoragePlanAction(
        tier=3,
        kind="delete_current_session",
        category_id="chat_history",
        resource_id="became-protected",
        estimated_reclaimable_bytes=50,
        destructive=True,
        description="Permanently delete an inactive current chat",
    )
    initial = StorageCleanupPlan(
        plan_id="a" * 64,
        before_bytes=100,
        target_bytes=50,
        estimated_after_bytes=50,
        estimated_reclaimable_bytes=50,
        attainable=True,
        actions=(action,),
        blockers=(),
        created_at="2026-08-04T00:00:00+00:00",
    )
    protected = StorageCleanupPlan(
        plan_id="b" * 64,
        before_bytes=100,
        target_bytes=50,
        estimated_after_bytes=100,
        estimated_reclaimable_bytes=0,
        attainable=False,
        actions=(),
        blockers=("1 chat(s) protected: currently open chat",),
        created_at="2026-08-04T00:00:01+00:00",
    )
    sessions = _Sessions()
    runtime = Runtime.__new__(Runtime)
    runtime.stores = SimpleNamespace(sessions=sessions)
    runtime.run_registry = RunRegistry()
    runtime._storage_status = {"total_bytes": 100}
    runtime._storage_cleanup_plan = AsyncMock(side_effect=[initial, protected])
    runtime.run_storage_maintenance_once = AsyncMock(return_value=runtime._storage_status)
    request = StorageExecuteRequest(plan_id=initial.plan_id, target_gb=1, current_session_id="became-protected")

    result = await Runtime.execute_storage_cleanup(runtime, request)

    assert sessions.deleted == []
    assert result["actions_completed"] == 0
    assert sessions.store.receipts[0]["actions"][0]["status"] == "skipped_after_revalidation"
