import asyncio
from collections.abc import Callable
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException

from arden.config import Config
from arden.logging import get_logger
from arden.server.schemas import StorageExecuteRequest, StoragePlanRequest
from arden.server.state import RunRegistry
from arden.services.session import SessionService
from arden.storage_budget import (
    StorageCleanupPlan,
    StorageSessionCandidate,
    build_storage_cleanup_plan,
    enforce_storage_budget,
    execute_storage_file_action,
    inspect_storage,
    list_storage_backups,
    set_backup_keep,
)

_logger = get_logger(__name__)


class StorageRuntime:
    """Own storage inventory, cleanup execution, and periodic maintenance."""

    def __init__(
        self,
        *,
        get_config: Callable[[], Config],
        get_session_service: Callable[[], SessionService | None],
        run_registry: RunRegistry,
        mark_ready: Callable[[], None],
    ) -> None:
        self._get_config = get_config
        self._get_session_service = get_session_service
        self._run_registry = run_registry
        self._mark_ready = mark_ready
        self._task: asyncio.Task[None] | None = None
        self._status: dict = {
            "status": "pending",
            "total_bytes": 0,
            "reclaimable_bytes": 0,
            "protected_bytes": 0,
            "reclaimed_bytes": 0,
            "max_bytes": None,
            "target_bytes": None,
            "checked_at": None,
            "categories": [],
            "reclaimed_by_category": {},
            "database_reclaim_mode": None,
            "last_plan": None,
            "last_run": None,
            "next_maintenance_at": None,
        }

    def status(self) -> dict:
        return dict(self._status)

    async def run_once(self) -> dict:
        service = self._require_session_service()
        config = self._get_config()
        try:
            await service.store.events.prune_expired_tool_results()
            referenced = await service.store.events.list_tool_result_content_hashes()
            reclaim_status = await service.store.database_reclaim_status()
            report = await asyncio.to_thread(
                enforce_storage_budget,
                config.arden_dir,
                max_space_gb=config.max_space_gb,
                referenced_tool_result_hashes=referenced,
                backup_retention_days=config.storage_backup_retention_days,
                expire_backups=True,
                database_reclaim_mode=str(reclaim_status["mode"]),
            )
            latest_run = await service.store.latest_storage_cleanup_run()
        except Exception as error:
            self._status = {
                **self._status,
                "status": "error",
                "error": f"{type(error).__name__}: {error}",
                "checked_at": datetime.now(UTC).isoformat(),
            }
            self._mark_ready()
            raise
        self._status = {
            **report.to_dict(),
            "last_plan": self._status.get("last_plan"),
            "last_run": latest_run,
            "next_maintenance_at": self._status.get("next_maintenance_at"),
        }
        self._mark_ready()
        return self.status()

    async def plan(self, request: StoragePlanRequest) -> dict:
        plan = await self._build_plan(request)
        self._status["last_plan"] = plan.to_dict()
        return plan.to_dict()

    async def execute(self, request: StorageExecuteRequest) -> dict:
        service = self._require_session_service()
        config = self._get_config()
        initial = await self._build_plan(request)
        if initial.plan_id != request.plan_id:
            raise HTTPException(status_code=409, detail="Storage changed; review the refreshed cleanup plan")

        started_at = datetime.now(UTC).isoformat()
        completed = 0
        before = initial.before_bytes
        receipts: list[dict] = []
        try:
            for action in initial.actions:
                if action.kind in {"stale_tool_result", "expired_backup"}:
                    fresh = await self._build_plan(request)
                    if not _contains_action(fresh, action.kind, action.resource_id):
                        receipts.append({**asdict(action), "status": "skipped_after_revalidation"})
                        continue
                    referenced = await service.store.events.list_tool_result_content_hashes()
                    await asyncio.to_thread(
                        execute_storage_file_action,
                        config.arden_dir,
                        action,
                        referenced_tool_result_hashes=referenced,
                        backup_retention_days=config.storage_backup_retention_days,
                    )
                    completed += 1
                    receipts.append({**asdict(action), "status": "completed"})
                    continue

                async with self._run_registry.session_lock(action.resource_id):
                    fresh = await self._build_plan(request)
                    if not _contains_action(fresh, action.kind, action.resource_id):
                        receipts.append({**asdict(action), "status": "skipped_after_revalidation"})
                        continue
                    if action.kind == "cold_convert_session":
                        changed = await service.cold_convert(action.resource_id) is not None
                    elif action.kind == "delete_cold_session":
                        changed = await service.permanently_delete(action.resource_id)
                    elif action.kind == "delete_current_session":
                        changed = await service.permanently_delete_current(action.resource_id)
                    else:
                        raise ValueError(f"Unsupported storage cleanup action: {action.kind}")
                    completed += int(changed)
                    receipts.append({**asdict(action), "status": "completed" if changed else "skipped"})
            await service.store.incremental_vacuum()
            await self.run_once()
        except Exception as error:
            await self._record_run(
                service,
                initial,
                started_at=started_at,
                before=before,
                receipts=receipts,
                status="error",
                error=f"{type(error).__name__}: {error}",
            )
            _logger.exception("Storage cleanup plan %s failed", initial.plan_id)
            raise

        after = int(self._status["total_bytes"])
        reclaimed = max(0, before - after)
        await self._record_run(
            service,
            initial,
            started_at=started_at,
            before=before,
            receipts=receipts,
            status="completed",
        )
        _logger.info(
            "Storage cleanup completed plan=%s actions=%d reclaimed_bytes=%d",
            initial.plan_id,
            completed,
            reclaimed,
        )
        return {
            "plan_id": initial.plan_id,
            "reclaimed_bytes": reclaimed,
            "actions_completed": completed,
            "status": self.status(),
        }

    async def backups(self) -> list[dict]:
        config = self._get_config()
        backups = await asyncio.to_thread(
            list_storage_backups,
            config.arden_dir,
            backup_retention_days=config.storage_backup_retention_days,
        )
        return [asdict(backup) for backup in backups]

    async def set_backup_keep(self, relative_path: str, *, keep: bool) -> list[dict]:
        await asyncio.to_thread(set_backup_keep, self._get_config().arden_dir, relative_path, keep=keep)
        return await self.backups()

    def start(self, *, interval_seconds: float = 3600) -> None:
        if interval_seconds <= 0:
            raise ValueError("Storage maintenance interval must be positive")
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._run_periodically(interval_seconds), name="arden-storage-maintenance")

    async def stop(self) -> None:
        task = self._task
        if task is None:
            return
        self._task = None
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def _run_periodically(self, interval_seconds: float) -> None:
        while True:
            self._status["next_maintenance_at"] = (datetime.now(UTC) + timedelta(seconds=interval_seconds)).isoformat()
            await asyncio.sleep(interval_seconds)
            try:
                await self.run_once()
            except Exception:
                _logger.exception("Storage budget maintenance failed")

    async def _build_plan(self, request: StoragePlanRequest | StorageExecuteRequest) -> StorageCleanupPlan:
        service = self._require_session_service()
        config = self._get_config()
        referenced = await service.store.events.list_tool_result_content_hashes()
        reclaim_status = await service.store.database_reclaim_status()
        inventory = await asyncio.to_thread(
            inspect_storage,
            config.arden_dir,
            max_space_gb=config.max_space_gb,
            referenced_tool_result_hashes=referenced,
            backup_retention_days=config.storage_backup_retention_days,
            database_reclaim_mode=str(reclaim_status["mode"]),
        )
        candidates = await service.store.list_storage_cleanup_candidates()
        pinned = set(request.pinned_session_ids)
        inactive_before = datetime.now(UTC) - timedelta(days=config.storage_current_inactive_days)
        protected: list[StorageSessionCandidate] = []
        for candidate in candidates:
            reasons = list(candidate.protected_reasons)
            if candidate.session_id == request.current_session_id:
                reasons.append("currently open chat")
            if candidate.session_id in pinned:
                reasons.append("pinned chat")
            if self._run_registry.get_active_run(candidate.session_id) is not None:
                reasons.append("active run")
            if not candidate.archived:
                last_activity = datetime.fromisoformat(candidate.last_activity)
                if last_activity > inactive_before:
                    reasons.append(f"active within {config.storage_current_inactive_days} days")
            protected.append(replace(candidate, protected_reasons=tuple(sorted(set(reasons)))))

        limit_gb = request.target_gb if request.target_gb is not None else config.max_space_gb
        target_bytes = inventory.report.total_bytes if limit_gb is None else int(limit_gb * (1024**3) * 0.85)
        allow_archived = (
            config.storage_allow_archived_cleanup
            if request.allow_archived_chats is None
            else request.allow_archived_chats
        )
        allow_current = (
            config.storage_allow_current_cleanup if request.allow_current_chats is None else request.allow_current_chats
        )
        allow_delete_cold = (
            config.storage_allow_delete_cold_chats
            if request.allow_delete_cold_chats is None
            else request.allow_delete_cold_chats
        )
        return build_storage_cleanup_plan(
            inventory,
            target_bytes=target_bytes,
            sessions=protected,
            allow_archived_chats=allow_archived,
            allow_delete_cold_chats=allow_delete_cold,
            allow_current_chats=allow_current,
            current_chat_minimum=config.storage_current_minimum,
        )

    async def _record_run(
        self,
        service: SessionService,
        plan: StorageCleanupPlan,
        *,
        started_at: str,
        before: int,
        receipts: list[dict],
        status: str,
        error: str | None = None,
    ) -> None:
        after = int(self._status.get("total_bytes") or before)
        receipt = {
            "plan_id": plan.plan_id,
            "started_at": started_at,
            "before_bytes": before,
            "target_bytes": plan.target_bytes,
            "after_bytes": after,
            "reclaimed_bytes": max(0, before - after),
            "actions": receipts,
            "status": status,
        }
        if error is not None:
            receipt["error"] = error
        run_id = await service.store.record_storage_cleanup_run(**receipt)
        self._status["last_run"] = {"run_id": run_id, **receipt}

    def _require_session_service(self) -> SessionService:
        service = self._get_session_service()
        if service is None:
            raise RuntimeError("Session service is not initialized")
        return service


def _contains_action(plan: StorageCleanupPlan, kind: str, resource_id: str) -> bool:
    return any(action.kind == kind and action.resource_id == resource_id for action in plan.actions)
