from collections.abc import Awaitable, Callable


class AreaLifecycleService:
    def __init__(
        self,
        *,
        sessions,
        sync_custodian: Callable[[dict], Awaitable[None]],
        disable_custodian: Callable[[str], Awaitable[None]],
    ) -> None:
        self._sessions = sessions
        self._sync_custodian = sync_custodian
        self._disable_custodian = disable_custodian

    async def create(self, **values) -> dict:
        existing = await self._sessions.find_area_by_name(values["name"])
        if existing is not None:
            page_path = values.get("page_path")
            if page_path and page_path != existing.get("page_path"):
                return await self.update(existing["area_id"], page_path=page_path)
            return existing
        area = await self._sessions.create_area(**values)
        if area.get("autonomy") is not None:
            try:
                await self._sync_custodian(area)
            except Exception:
                await self._disable_custodian(area["area_id"])
                await self._sessions.archive_area(area["area_id"])
                raise
        return area

    async def update(self, area_id: str, **patch) -> dict:
        before = await self._sessions.get_area(area_id)
        if before is None:
            raise KeyError(area_id)
        updated = await self._sessions.update_area(area_id, **patch)
        if updated is None:
            raise KeyError(area_id)
        try:
            if updated.get("autonomy") is None:
                await self._disable_custodian(area_id)
            else:
                await self._sync_custodian(updated)
        except Exception:
            rollback = {
                key: before.get("paused_at") is not None if key == "paused" else before.get(key)
                for key in patch
            }
            restored = await self._sessions.update_area(area_id, **rollback)
            if restored and restored.get("autonomy") is not None:
                try:
                    await self._sync_custodian(restored)
                except Exception:
                    pass
            raise
        return updated

    async def archive(self, area_id: str) -> bool:
        area = await self._sessions.get_area(area_id)
        if area is None:
            return False
        await self._disable_custodian(area_id)
        archived = await self._sessions.archive_area(area_id)
        if not archived and area.get("autonomy") is not None:
            await self._sync_custodian(area)
        return archived

    async def restore(self, area_id: str) -> dict | None:
        restored = await self._sessions.restore_area(area_id)
        if restored is None:
            return None
        if restored.get("autonomy") is not None:
            try:
                await self._sync_custodian(restored)
            except Exception:
                await self._sessions.archive_area(area_id)
                raise
        return restored
