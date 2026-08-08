import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from arden.config import Config
from arden.server.runtime.core import Runtime
from arden.server.schemas import StorageExecuteRequest, StoragePlanRequest
from arden.server.state import RunRegistry
from arden.storage_budget import StorageCleanupPlan, StoragePlanAction, StorageSessionCandidate

_TARGET_GB = 0.01


class _Events:
    async def list_tool_result_content_hashes(self) -> set[str]:
        return set()


class _Store:
    def __init__(self, sessions: list[StorageSessionCandidate] | None = None):
        self.receipts: list[dict] = []
        self.sessions = sessions or []
        self.events = _Events()

    async def database_reclaim_status(self) -> dict:
        return {"mode": "incremental"}

    async def list_storage_cleanup_candidates(self) -> list[StorageSessionCandidate]:
        return list(self.sessions)

    async def incremental_vacuum(self):
        return 0

    async def record_storage_cleanup_run(self, **receipt):
        self.receipts.append(receipt)
        return "storage-test"


class _Sessions:
    def __init__(self, sessions: list[StorageSessionCandidate] | None = None):
        self.store = _Store(sessions)
        self.deleted: list[str] = []

    async def permanently_delete_current(self, session_id: str):
        self.deleted.append(session_id)
        return True


def _runtime(root: Path, *, sessions: list[StorageSessionCandidate] | None = None, **config) -> Runtime:
    runtime = Runtime.__new__(Runtime)
    runtime.stores = SimpleNamespace(sessions=_Sessions(sessions))
    runtime.run_registry = RunRegistry()
    runtime.config_runtime = SimpleNamespace(
        config=Config(_env_file=None, arden_dir=root, storage_current_minimum=1, **config)
    )
    runtime._storage_status = {"total_bytes": 0}
    runtime.run_storage_maintenance_once = AsyncMock(return_value=runtime._storage_status)
    return runtime


def _expired_backup(root: Path, name: str, *, size: int, age_days: int) -> Path:
    path = root / "archive" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"b" * size)
    stamp = (datetime.now(UTC) - timedelta(days=age_days)).timestamp()
    os.utime(path, (stamp, stamp))
    return path


def _rotating_log(root: Path, *, size: int) -> Path:
    path = root / "logs" / "arden.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"l" * size)
    return path


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


@pytest.mark.asyncio
async def test_growth_of_a_non_candidate_file_keeps_a_pending_plan_executable(tmp_path: Path) -> None:
    """A plan must survive the log file and the live database growing under it —
    plan identity is the work set, not the size of the whole tree."""
    old = _expired_backup(tmp_path, "old.db", size=10 * 1024 * 1024, age_days=20)
    older = _expired_backup(tmp_path, "older.db", size=1024 * 1024, age_days=21)
    log = _rotating_log(tmp_path, size=1024)
    runtime = _runtime(tmp_path)

    plan = await Runtime.plan_storage_cleanup(runtime, StoragePlanRequest(target_gb=_TARGET_GB))
    log.write_bytes(b"l" * 5 * 1024 * 1024)
    (tmp_path / "sessions.db-wal").write_bytes(b"w" * 2 * 1024 * 1024)

    result = await Runtime.execute_storage_cleanup(
        runtime, StorageExecuteRequest(target_gb=_TARGET_GB, plan_id=plan["plan_id"])
    )

    assert [action["resource_id"] for action in plan["actions"]] == ["archive/older.db", "archive/old.db"]
    assert result["actions_completed"] == 2
    assert old.exists() is False
    assert older.exists() is False


@pytest.mark.asyncio
async def test_changed_candidate_set_still_rejects_a_stale_plan(tmp_path: Path) -> None:
    _expired_backup(tmp_path, "old.db", size=10 * 1024 * 1024, age_days=20)
    _expired_backup(tmp_path, "older.db", size=1024 * 1024, age_days=21)
    _rotating_log(tmp_path, size=1024)
    runtime = _runtime(tmp_path)

    plan = await Runtime.plan_storage_cleanup(runtime, StoragePlanRequest(target_gb=_TARGET_GB))
    _expired_backup(tmp_path, "older.db", size=3 * 1024 * 1024, age_days=21)

    with pytest.raises(HTTPException) as error:
        await Runtime.execute_storage_cleanup(
            runtime, StorageExecuteRequest(target_gb=_TARGET_GB, plan_id=plan["plan_id"])
        )

    assert error.value.status_code == 409


@pytest.mark.asyncio
async def test_cold_chat_deletion_falls_back_to_the_persisted_setting(tmp_path: Path) -> None:
    cold = StorageSessionCandidate(
        session_id="cold-chat",
        archived=True,
        storage_state="cold",
        last_activity=(datetime.now(UTC) - timedelta(days=200)).isoformat(),
        logical_bytes=20 * 1024 * 1024,
        message_count=4,
    )
    _rotating_log(tmp_path, size=10 * 1024 * 1024)
    disabled = _runtime(
        tmp_path,
        sessions=[cold],
        storage_allow_archived_cleanup=True,
        storage_allow_delete_cold_chats=False,
    )
    enabled = _runtime(
        tmp_path,
        sessions=[cold],
        storage_allow_archived_cleanup=True,
        storage_allow_delete_cold_chats=True,
    )
    request = StoragePlanRequest(target_gb=_TARGET_GB)

    without_setting = await Runtime.plan_storage_cleanup(disabled, request)
    with_setting = await Runtime.plan_storage_cleanup(enabled, request)
    overridden = await Runtime.plan_storage_cleanup(
        enabled, StoragePlanRequest(target_gb=_TARGET_GB, allow_delete_cold_chats=False)
    )

    assert [action["kind"] for action in without_setting["actions"]] == []
    assert [action["kind"] for action in with_setting["actions"]] == ["delete_cold_session"]
    assert [action["kind"] for action in overridden["actions"]] == []
