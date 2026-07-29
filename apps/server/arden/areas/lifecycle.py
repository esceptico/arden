import asyncio
from collections.abc import Awaitable, Callable

from arden.wiki.service import WikiService


class AreaWikiUnavailable(RuntimeError):
    pass


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
        except Exception as primary_error:
            rollback = {
                key: before.get("paused_at") is not None if key == "paused" else before.get(key) for key in patch
            }
            try:
                restored = await self._sessions.update_area(area_id, **rollback)
            except Exception as restore_error:
                raise ExceptionGroup("Area update rollback failed", [primary_error, restore_error]) from None
            if restored is None:
                restore_error = RuntimeError(f"Area {area_id} disappeared during rollback")
                raise ExceptionGroup("Area update rollback failed", [primary_error, restore_error]) from None
            try:
                if restored.get("autonomy") is None:
                    await self._disable_custodian(area_id)
                else:
                    await self._sync_custodian(restored)
            except Exception as reconciliation_error:
                raise ExceptionGroup(
                    "Area update rollback reconciliation failed",
                    [primary_error, reconciliation_error],
                ) from None
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


class AreaPageService:
    def __init__(
        self,
        *,
        wiki: WikiService | None,
        sessions,
        lifecycle: AreaLifecycleService,
        project_wiki_state: Callable[[], Awaitable[bool]],
    ) -> None:
        self._wiki = wiki
        self._sessions = sessions
        self._lifecycle = lifecycle
        self._project_wiki_state = project_wiki_state

    async def create(self, area_id: str) -> dict:
        if self._wiki is None:
            raise AreaWikiUnavailable("Managed wiki is unavailable")
        area = await self._sessions.get_area(area_id)
        if area is None:
            raise KeyError(area_id)
        if area.get("page_path"):
            raise ValueError("Area already has a page")
        slug = self._slug(area["name"])
        suffix = 1
        snapshot = await asyncio.to_thread(self._wiki.snapshot)
        paths = {record.resource.path for record in snapshot.pages}
        while True:
            candidate_slug = slug if suffix == 1 else f"{slug}-{suffix}"
            page_path = f"topics/{candidate_slug}.md"
            if page_path not in paths:
                break
            suffix += 1
        page = await asyncio.to_thread(
            self._wiki.create_page,
            path=page_path,
            title=area["name"],
            body=f"# {area['name']}\n\n## Open loops\n\n## Related\n".encode(),
            expected_head=snapshot.head,
            actor="user:area",
            origin="area",
            reason=f"attach wiki page to Area {area_id}",
        )
        try:
            updated = await self._lifecycle.update(area_id, page_path=page_path)
        except Exception:
            await asyncio.to_thread(
                self._wiki.archive_page,
                page.page.page_id,
                expected_version=page.resource.version_id,
                base_head=self._wiki.repository.head,
                actor="backend",
                origin="area.rollback",
                reason=f"rollback failed Area page attachment {area_id}",
            )
            raise
        await self._project_wiki_state()
        return updated

    async def detach(self, area_id: str) -> dict:
        area = await self._sessions.get_area(area_id)
        if area is None:
            raise KeyError(area_id)
        if area.get("autonomy") is not None:
            raise ValueError("Disable the Custodian before detaching its page")
        return await self._lifecycle.update(area_id, page_path=None)

    @staticmethod
    def _slug(name: str) -> str:
        chars: list[str] = []
        pending_dash = False
        for char in name.casefold():
            if char.isalnum():
                if pending_dash and chars:
                    chars.append("-")
                chars.append(char)
                pending_dash = False
            else:
                pending_dash = True
        return "".join(chars) or "area"
