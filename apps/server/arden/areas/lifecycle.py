import asyncio
from collections.abc import Awaitable, Callable

from arden.revisions import RevisionConflictError
from arden.wiki.exceptions import WikiRenameApplyAmbiguousError
from arden.wiki.models import RenamePlan
from arden.wiki.service import WikiService, WikiValidationError


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
        return await self.update_after_page_change(before, **patch)

    async def update_after_page_change(self, before: dict, **patch) -> dict:
        area_id = before["area_id"]
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

    async def update(self, area_id: str, **patch) -> dict:
        before = await self._sessions.get_area(area_id)
        if before is None:
            raise KeyError(area_id)
        if before["page_id"] is not None and "name" in patch:
            return await self.rename_area(before, patch)
        if before["page_id"] is not None and "page_path" in patch and patch["page_path"] != before["page_path"]:
            raise ValueError("Rename an attached Area page; do not patch page_path")
        return await self._lifecycle.update(area_id, **patch)

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
            updated = await self._lifecycle.update(area_id, page_path=page_path, page_id=page.page.page_id)
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
        return await self._lifecycle.update_after_page_change(area, page_path=None, page_id=None)

    async def rename_area(self, before: dict, patch: dict) -> dict:
        """Rename an Area and its bound page in one recoverable workflow."""

        if self._wiki is None:
            raise AreaWikiUnavailable("Managed wiki is unavailable")
        page_id = before["page_id"]
        current = await asyncio.to_thread(self._wiki.read_page, page_id)
        if current.resource.path != before["page_path"]:
            raise ValueError("Area page binding needs repair before it can be renamed")
        new_name = patch["name"].strip()
        new_path = f"topics/{self._slug(new_name)}.md"
        requested_path = patch.get("page_path")
        if requested_path not in (None, before["page_path"], new_path):
            raise ValueError("Area name determines its attached page path")
        if new_path == current.resource.path and new_name == current.page.title:
            return await self._lifecycle.update_after_page_change(before, **patch)
        owner = await self._sessions.find_area_by_name(new_name)
        if owner is not None and owner["area_id"] != before["area_id"]:
            raise ValueError("An active Area with that name already exists")
        plan = await asyncio.to_thread(
            self._wiki.prepare_rename,
            page_id,
            new_path=new_path,
            new_title=new_name,
            expected_version=current.resource.version_id,
            base_head=self._wiki.repository.head,
        )
        area_patch = dict(patch)
        area_patch.pop("name")
        area_patch.pop("page_path", None)
        await self._apply_bound_rename_plan(plan, before, area_patch)
        await self._project_wiki_state()
        updated = await self._sessions.get_area(before["area_id"])
        if updated is None:
            raise RuntimeError("Area disappeared after page rename")
        return updated

    async def apply_rename_plan(self, plan: RenamePlan):
        """Apply a generic rename plan without a redirect when it owns an Area page."""

        area = await self._sessions.find_area_by_page_id(plan.page_id)
        if area is None:
            return await asyncio.to_thread(self._wiki.apply_rename, plan)
        self._require_bound_rename_path(plan.new_path, plan.new_title)
        return await self._apply_bound_rename_plan(plan, area, {})

    async def validate_rename_request(self, page_id: str, new_path: str, new_title: str) -> None:
        if await self._sessions.find_area_by_page_id(page_id) is not None:
            self._require_bound_rename_path(new_path, new_title)

    async def _apply_bound_rename_plan(self, plan: RenamePlan, area: dict, area_patch: dict):
        if not plan.rewrite_links:
            raise WikiValidationError("Area page renames must rewrite incoming links")
        already_renamed = area["page_path"] == plan.new_path
        if area["page_path"] not in {plan.old_path, plan.new_path}:
            raise ValueError("Area page binding needs repair before it can be renamed")
        owner = await self._sessions.find_area_by_name(plan.new_title)
        if owner is not None and owner["area_id"] != area["area_id"]:
            raise ValueError("An active Area with that name already exists")
        commit = await asyncio.to_thread(
            self._wiki.apply_rename_without_redirect,
            plan,
            actor="user:area",
            origin="area.rename",
            reason=f"rename Area page {plan.old_path} to {plan.new_path}",
        )
        current = await asyncio.to_thread(self._wiki.read_page, plan.page_id)
        if current.resource.path != plan.new_path:
            raise RevisionConflictError("Area rename commit is no longer current; prepare a fresh plan")
        try:
            await self._lifecycle.update_after_page_change(
                area,
                **area_patch,
                name=plan.new_title,
                page_path=plan.new_path,
                page_id=plan.page_id,
            )
        except Exception as error:
            latest = await self._sessions.get_area(area["area_id"])
            if already_renamed or (latest is not None and latest.get("page_path") == plan.new_path):
                # The page commit was a prior partial success.  Leave it in
                # place so the same idempotent plan can finish the Area row.
                raise WikiRenameApplyAmbiguousError("Area update may have completed after the page rename") from error
            try:
                await asyncio.to_thread(self._rollback_bound_rename, plan)
            except Exception as rollback_error:
                raise ExceptionGroup("Area page rename rollback failed", [error, rollback_error]) from None
            # The original idempotency key now names a forward commit that is
            # no longer HEAD.  Force a fresh snapshot and plan on retry.
            raise RevisionConflictError(
                "Area update failed after rename and was rolled back; prepare a fresh plan"
            ) from error
        return commit

    def _rollback_bound_rename(self, plan) -> None:
        current = self._wiki.read_page(plan.page_id)
        reverse = self._wiki.prepare_rename(
            plan.page_id,
            new_path=plan.old_path,
            new_title=plan.old_title,
            expected_version=current.resource.version_id,
            base_head=self._wiki.repository.head,
        )
        self._wiki.apply_rename_without_redirect(
            reverse,
            actor="backend",
            origin="area.rollback",
            reason=f"rollback Area page rename {plan.new_path} to {plan.old_path}",
        )

    @classmethod
    def _require_bound_rename_path(cls, new_path: str, new_title: str) -> None:
        name = new_title.strip()
        if name != new_title or new_path != f"topics/{cls._slug(name)}.md":
            raise ValueError("An attached Area name determines its page path")

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
