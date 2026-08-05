from collections.abc import Awaitable, Callable
from dataclasses import replace

import pytest

from arden.areas.lifecycle import AreaLifecycleService, AreaPageService, AreaWikiUnavailable
from arden.revisions import ManagedFileRepository, RevisionConflictError
from arden.wiki.exceptions import WikiRenameApplyAmbiguousError
from arden.wiki.service import WikiService, WikiValidationError


class FakeAreas:
    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}
        self.archived: dict[str, dict] = {}
        self.next_id = 1

    async def create_area(self, **values) -> dict:
        area_id = f"area_{self.next_id}"
        self.next_id += 1
        row = {
            "area_id": area_id,
            "name": values["name"],
            "page_path": values.get("page_path"),
            "page_id": None,
            "autonomy": values.get("autonomy"),
            "paused_at": None,
            "attention": "ambient",
            "interrupts": "asks",
            "default_cwd": values.get("default_cwd"),
            "instructions": values.get("instructions"),
        }
        self.rows[area_id] = row
        return dict(row)

    async def find_area_by_name(self, name: str) -> dict | None:
        return next((dict(row) for row in self.rows.values() if row["name"].casefold() == name.casefold()), None)

    async def find_area_by_page_id(self, page_id: str) -> dict | None:
        return next((dict(row) for row in self.rows.values() if row["page_id"] == page_id), None)

    async def get_area(self, area_id: str) -> dict | None:
        row = self.rows.get(area_id)
        return dict(row) if row else None

    async def update_area(self, area_id: str, **patch) -> dict | None:
        row = self.rows.get(area_id)
        if row is None:
            return None
        if "paused" in patch:
            patch["paused_at"] = "now" if patch.pop("paused") else None
        row.update(patch)
        return dict(row)

    async def archive_area(self, area_id: str) -> bool:
        row = self.rows.pop(area_id, None)
        if row is None:
            return False
        self.archived[area_id] = row
        return True

    async def restore_area(self, area_id: str) -> dict | None:
        row = self.archived.pop(area_id, None)
        if row is None:
            return None
        self.rows[area_id] = row
        return dict(row)


def lifecycle(
    areas: FakeAreas,
    *,
    sync: Callable[[dict], Awaitable[None]] | None = None,
    disable: Callable[[str], Awaitable[None]] | None = None,
) -> AreaLifecycleService:
    async def noop_sync(area: dict) -> None:
        return None

    async def noop_disable(area_id: str) -> None:
        return None

    return AreaLifecycleService(
        sessions=areas,
        sync_custodian=sync or noop_sync,
        disable_custodian=disable or noop_disable,
    )


def pages(tmp_path, areas: FakeAreas, area_lifecycle: AreaLifecycleService) -> AreaPageService:
    async def project_wiki_state() -> bool:
        return False

    return AreaPageService(
        wiki=WikiService(ManagedFileRepository(tmp_path / "wiki")),
        sessions=areas,
        lifecycle=area_lifecycle,
        project_wiki_state=project_wiki_state,
    )


@pytest.mark.asyncio
async def test_page_creation_projects_the_committed_wiki_head(tmp_path) -> None:
    areas = FakeAreas()
    area_lifecycle = lifecycle(areas)
    area = await area_lifecycle.create(name="Dex")
    projected: list[str] = []

    async def project_wiki_state() -> bool:
        projected.append("projected")
        return False

    service = AreaPageService(
        wiki=WikiService(ManagedFileRepository(tmp_path / "wiki")),
        sessions=areas,
        lifecycle=area_lifecycle,
        project_wiki_state=project_wiki_state,
    )

    created = await service.create(area["area_id"])

    assert created["page_path"] == "topics/dex.md"
    assert projected == ["projected"]


@pytest.mark.asyncio
async def test_page_creation_keeps_attachment_when_projection_is_pending(tmp_path) -> None:
    areas = FakeAreas()
    area_lifecycle = lifecycle(areas)
    area = await area_lifecycle.create(name="Dex")
    wiki = WikiService(ManagedFileRepository(tmp_path / "wiki"))

    async def defer_projection() -> bool:
        return True

    service = AreaPageService(
        wiki=wiki,
        sessions=areas,
        lifecycle=area_lifecycle,
        project_wiki_state=defer_projection,
    )

    created = await service.create(area["area_id"])

    assert created["page_path"] == "topics/dex.md"
    assert sorted(page.resource.path for page in wiki.list_pages()) == ["topics/README.md", "topics/dex.md"]


@pytest.mark.asyncio
async def test_page_creation_reports_disabled_wiki() -> None:
    areas = FakeAreas()
    area_lifecycle = lifecycle(areas)
    area = await area_lifecycle.create(name="Dex")

    async def project_wiki_state() -> bool:
        return False

    service = AreaPageService(
        wiki=None,
        sessions=areas,
        lifecycle=area_lifecycle,
        project_wiki_state=project_wiki_state,
    )

    with pytest.raises(AreaWikiUnavailable, match="Managed wiki is unavailable"):
        await service.create(area["area_id"])


@pytest.mark.asyncio
async def test_page_attachment_does_not_implicitly_delegate() -> None:
    areas = FakeAreas()
    synced: list[dict] = []

    async def sync(area: dict) -> None:
        synced.append(area)

    created = await lifecycle(areas, sync=sync).create(name="Health", page_path="topics/health.md")

    assert created["page_path"] == "topics/health.md"
    assert created["autonomy"] is None
    assert synced == []


@pytest.mark.asyncio
async def test_delegate_and_rename_sync_live_custodian() -> None:
    areas = FakeAreas()
    synced: list[dict] = []

    async def sync(area: dict) -> None:
        synced.append(dict(area))

    svc = lifecycle(areas, sync=sync)
    area = await svc.create(name="Health", page_path="topics/health.md")
    delegated = await svc.update(area["area_id"], autonomy="observe")
    renamed = await svc.update(area["area_id"], name="Wellbeing")

    assert delegated["autonomy"] == "observe"
    assert renamed["name"] == "Wellbeing"
    assert [row["name"] for row in synced] == ["Health", "Wellbeing"]


@pytest.mark.asyncio
async def test_autonomy_changes_sync_before_update_returns() -> None:
    areas = FakeAreas()
    synced: list[str] = []

    async def sync(area: dict) -> None:
        synced.append(area["autonomy"])

    svc = lifecycle(areas, sync=sync)
    area = await svc.create(name="Health", page_path="topics/health.md", autonomy="observe")

    await svc.update(area["area_id"], autonomy="act")
    await svc.update(area["area_id"], autonomy="observe")

    assert synced == ["observe", "act", "observe"]


@pytest.mark.asyncio
async def test_archive_disables_and_restore_resyncs_custodian() -> None:
    areas = FakeAreas()
    disabled: list[str] = []
    synced: list[dict] = []

    async def disable(area_id: str) -> None:
        disabled.append(area_id)

    async def sync(area: dict) -> None:
        synced.append(dict(area))

    svc = lifecycle(areas, sync=sync, disable=disable)
    area = await svc.create(name="Health", page_path="topics/health.md", autonomy="observe")
    synced.clear()

    assert await svc.archive(area["area_id"])
    restored = await svc.restore(area["area_id"])

    assert disabled == [area["area_id"]]
    assert restored is not None
    assert synced == [restored]


@pytest.mark.asyncio
async def test_failed_runtime_sync_rolls_back_area_update() -> None:
    areas = FakeAreas()
    synced: list[str] = []

    async def fail_sync(area: dict) -> None:
        synced.append(area["name"])
        if area["name"] == "Broken":
            raise RuntimeError("runtime unavailable")

    svc = lifecycle(areas, sync=fail_sync)
    area = await svc.create(name="Health", page_path="topics/health.md", autonomy="observe")

    with pytest.raises(RuntimeError, match="runtime unavailable"):
        await svc.update(area["area_id"], name="Broken")

    assert (await areas.get_area(area["area_id"]))["name"] == "Health"
    assert synced == ["Health", "Broken", "Health"]


@pytest.mark.asyncio
async def test_failed_enable_disables_partial_custodian_during_rollback() -> None:
    areas = FakeAreas()
    disabled: list[str] = []

    async def fail_sync(_area: dict) -> None:
        raise RuntimeError("enable failed")

    async def disable(area_id: str) -> None:
        disabled.append(area_id)

    svc = lifecycle(areas, sync=fail_sync, disable=disable)
    area = await svc.create(name="Health", page_path="topics/health.md")

    with pytest.raises(RuntimeError, match="enable failed"):
        await svc.update(area["area_id"], autonomy="observe")

    assert (await areas.get_area(area["area_id"]))["autonomy"] is None
    assert disabled == [area["area_id"]]


@pytest.mark.asyncio
async def test_failed_rollback_sync_surfaces_primary_and_reconciliation_errors() -> None:
    areas = FakeAreas()
    health_syncs = 0

    async def fail_sync(area: dict) -> None:
        nonlocal health_syncs
        if area["name"] == "Broken":
            raise RuntimeError("primary sync failed")
        health_syncs += 1
        if health_syncs > 1:
            raise RuntimeError("rollback sync failed")

    svc = lifecycle(areas, sync=fail_sync)
    area = await svc.create(name="Health", page_path="topics/health.md", autonomy="observe")

    with pytest.raises(ExceptionGroup) as raised:
        await svc.update(area["area_id"], name="Broken")

    assert [str(error) for error in raised.value.exceptions] == ["primary sync failed", "rollback sync failed"]
    assert (await areas.get_area(area["area_id"]))["name"] == "Health"


@pytest.mark.asyncio
async def test_missing_row_during_rollback_surfaces_primary_and_restore_errors() -> None:
    class MissingRollbackAreas(FakeAreas):
        def __init__(self) -> None:
            super().__init__()
            self.updates = 0

        async def update_area(self, area_id: str, **patch) -> dict | None:
            self.updates += 1
            if self.updates == 2:
                return None
            return await super().update_area(area_id, **patch)

    areas = MissingRollbackAreas()

    async def fail_sync(_area: dict) -> None:
        raise RuntimeError("primary sync failed")

    svc = lifecycle(areas, sync=fail_sync)
    area = await svc.create(name="Health", page_path="topics/health.md")

    with pytest.raises(ExceptionGroup) as raised:
        await svc.update(area["area_id"], autonomy="observe")

    assert [str(error) for error in raised.value.exceptions] == [
        "primary sync failed",
        f"Area {area['area_id']} disappeared during rollback",
    ]


@pytest.mark.asyncio
async def test_create_page_writes_safe_topic_and_attaches_it(tmp_path) -> None:
    areas = FakeAreas()
    area_lifecycle = lifecycle(areas)
    area = await area_lifecycle.create(name="O-1A / Visa")
    area_pages = pages(tmp_path, areas, area_lifecycle)

    updated = await area_pages.create(area["area_id"])

    assert updated["page_path"] == "topics/o-1a-visa.md"
    page = next(
        record.page for record in area_pages._wiki.list_pages() if record.resource.path == "topics/o-1a-visa.md"
    )
    text = page.to_bytes().decode()
    assert "title: O-1A / Visa" in text
    assert "## Open loops" in text


@pytest.mark.asyncio
async def test_area_rename_moves_its_bound_page_without_a_redirect_and_rewrites_links(tmp_path) -> None:
    areas = FakeAreas()
    synced: list[dict] = []

    async def sync(area: dict) -> None:
        synced.append(dict(area))

    area_lifecycle = lifecycle(areas, sync=sync)
    area = await area_lifecycle.create(name="Dex", autonomy="observe")
    area_pages = pages(tmp_path, areas, area_lifecycle)
    attached = await area_pages.create(area["area_id"])
    wiki = area_pages._wiki
    head = wiki.snapshot().head
    wiki.create_page(
        path="topics/related.md",
        title="Related",
        body=b"# Related\n\n[[topics/dex]]\n",
        expected_head=head,
    )

    renamed = await area_pages.update(area["area_id"], name="Arden")

    assert renamed["name"] == "Arden"
    assert renamed["page_path"] == "topics/arden.md"
    assert renamed["page_id"] == attached["page_id"]
    assert sorted(record.resource.path for record in wiki.list_pages()) == [
        "topics/README.md",
        "topics/arden.md",
        "topics/related.md",
    ]
    related = next(record for record in wiki.list_pages() if record.resource.path == "topics/related.md")
    assert "[[topics/arden]]" in wiki.read_page(related.page.page_id).page.body.decode()
    assert synced[-1]["name"] == "Arden"


@pytest.mark.asyncio
async def test_stale_bound_page_rename_does_not_change_the_area(tmp_path) -> None:
    areas = FakeAreas()
    area_lifecycle = lifecycle(areas)
    area = await area_lifecycle.create(name="Dex")
    area_pages = pages(tmp_path, areas, area_lifecycle)
    attached = await area_pages.create(area["area_id"])
    wiki = area_pages._wiki
    plan = wiki.prepare_rename(
        attached["page_id"],
        new_path="topics/arden.md",
        new_title="Arden",
        expected_version=next(
            record for record in wiki.list_pages() if record.page.page_id == attached["page_id"]
        ).resource.version_id,
        base_head=wiki.repository.head,
    )
    current = wiki.read_page(attached["page_id"])
    wiki.update_page(
        attached["page_id"],
        content=current.content + b"\nLater edit.\n",
        expected_version=current.resource.version_id,
        expected_head=wiki.repository.head,
    )

    with pytest.raises(RevisionConflictError):
        await area_pages.apply_rename_plan(plan)

    unchanged = await areas.get_area(area["area_id"])
    assert unchanged is not None
    assert unchanged["name"] == "Dex"
    assert unchanged["page_path"] == "topics/dex.md"


@pytest.mark.asyncio
async def test_bound_page_rename_plan_syncs_its_area(tmp_path) -> None:
    areas = FakeAreas()
    area_lifecycle = lifecycle(areas)
    area = await area_lifecycle.create(name="Dex")
    area_pages = pages(tmp_path, areas, area_lifecycle)
    attached = await area_pages.create(area["area_id"])
    wiki = area_pages._wiki
    current = wiki.read_page(attached["page_id"])
    plan = wiki.prepare_rename(
        attached["page_id"],
        new_path="topics/arden.md",
        new_title="Arden",
        expected_version=current.resource.version_id,
        base_head=wiki.repository.head,
    )

    await area_pages.apply_rename_plan(plan)

    updated = await areas.get_area(area["area_id"])
    assert updated is not None
    assert updated["name"] == "Arden"
    assert updated["page_path"] == "topics/arden.md"
    assert updated["page_id"] == attached["page_id"]
    assert all(record.page.lifecycle != "redirect" for record in wiki.list_pages())


@pytest.mark.asyncio
async def test_bound_rename_retries_when_area_write_completed_but_response_was_lost(tmp_path) -> None:
    areas = FakeAreas()
    area_lifecycle = lifecycle(areas)
    area = await area_lifecycle.create(name="Dex")
    area_pages = pages(tmp_path, areas, area_lifecycle)
    attached = await area_pages.create(area["area_id"])
    wiki = area_pages._wiki
    current = wiki.read_page(attached["page_id"])
    plan = wiki.prepare_rename(
        attached["page_id"],
        new_path="topics/arden.md",
        new_title="Arden",
        expected_version=current.resource.version_id,
        base_head=wiki.repository.head,
    )
    update = area_lifecycle.update_after_page_change
    lost = True

    async def update_then_lose_response(before, **patch):
        nonlocal lost
        result = await update(before, **patch)
        if lost:
            lost = False
            raise RuntimeError("Area update response lost")
        return result

    area_lifecycle.update_after_page_change = update_then_lose_response
    with pytest.raises(WikiRenameApplyAmbiguousError, match="may have completed"):
        await area_pages.apply_rename_plan(plan)

    assert (await areas.get_area(area["area_id"]))["page_path"] == "topics/arden.md"
    assert wiki.read_page(attached["page_id"]).resource.path == "topics/arden.md"
    await area_pages.apply_rename_plan(plan)


@pytest.mark.asyncio
async def test_bound_rename_rollback_forces_a_fresh_plan(tmp_path) -> None:
    areas = FakeAreas()
    area_lifecycle = lifecycle(areas)
    area = await area_lifecycle.create(name="Dex")
    area_pages = pages(tmp_path, areas, area_lifecycle)
    attached = await area_pages.create(area["area_id"])
    wiki = area_pages._wiki
    current = wiki.read_page(attached["page_id"])
    plan = wiki.prepare_rename(
        attached["page_id"],
        new_path="topics/arden.md",
        new_title="Arden",
        expected_version=current.resource.version_id,
        base_head=wiki.repository.head,
    )

    update = area_lifecycle.update_after_page_change

    async def fail_area_update(*_args, **_kwargs):
        raise RuntimeError("Area database unavailable")

    area_lifecycle.update_after_page_change = fail_area_update
    with pytest.raises(RevisionConflictError, match="was rolled back"):
        await area_pages.apply_rename_plan(plan)

    restored = wiki.read_page(attached["page_id"])
    assert restored.resource.path == "topics/dex.md"
    fresh = wiki.prepare_rename(
        attached["page_id"],
        new_path="topics/arden.md",
        new_title="Arden",
        expected_version=restored.resource.version_id,
        base_head=wiki.repository.head,
    )
    area_lifecycle.update_after_page_change = update
    await area_pages.apply_rename_plan(fresh)
    assert wiki.read_page(attached["page_id"]).resource.path == "topics/arden.md"


@pytest.mark.asyncio
async def test_bound_page_rejects_a_path_not_derived_from_its_area_name(tmp_path) -> None:
    areas = FakeAreas()
    area_lifecycle = lifecycle(areas)
    area = await area_lifecycle.create(name="Dex")
    area_pages = pages(tmp_path, areas, area_lifecycle)
    attached = await area_pages.create(area["area_id"])
    wiki = area_pages._wiki
    current = wiki.read_page(attached["page_id"])
    plan = wiki.prepare_rename(
        attached["page_id"],
        new_path="projects/arden.md",
        new_title="Arden",
        expected_version=current.resource.version_id,
        base_head=wiki.repository.head,
    )

    with pytest.raises(ValueError, match="name determines"):
        await area_pages.validate_rename_request(attached["page_id"], plan.new_path, plan.new_title)
    with pytest.raises(ValueError, match="name determines"):
        await area_pages.apply_rename_plan(plan)

    unchanged = await areas.get_area(area["area_id"])
    assert unchanged is not None
    assert unchanged["name"] == "Dex"
    assert wiki.read_page(attached["page_id"]).resource.path == "topics/dex.md"


@pytest.mark.asyncio
async def test_path_only_area_keeps_existing_name_update_behavior(tmp_path) -> None:
    areas = FakeAreas()
    area_lifecycle = lifecycle(areas)
    area = await area_lifecycle.create(name="Legacy", page_path="topics/legacy.md")
    area_pages = pages(tmp_path, areas, area_lifecycle)

    updated = await area_pages.update(area["area_id"], name="Current")

    assert updated["name"] == "Current"
    assert updated["page_path"] == "topics/legacy.md"
    assert updated["page_id"] is None


@pytest.mark.asyncio
async def test_bound_page_rename_rejects_tampered_and_no_rewrite_plans(tmp_path) -> None:
    areas = FakeAreas()
    area_lifecycle = lifecycle(areas)
    area = await area_lifecycle.create(name="Dex")
    area_pages = pages(tmp_path, areas, area_lifecycle)
    attached = await area_pages.create(area["area_id"])
    wiki = area_pages._wiki
    current = wiki.read_page(attached["page_id"])
    plan = wiki.prepare_rename(
        attached["page_id"],
        new_path="topics/arden.md",
        new_title="Arden",
        expected_version=current.resource.version_id,
        base_head=wiki.repository.head,
    )

    with pytest.raises(WikiValidationError, match="pinned snapshot"):
        await area_pages.apply_rename_plan(replace(plan, moved_content=plan.moved_content + b"\nTampered."))

    no_rewrite = wiki.prepare_rename(
        attached["page_id"],
        new_path="topics/arden.md",
        new_title="Arden",
        expected_version=current.resource.version_id,
        base_head=wiki.repository.head,
        rewrite_links=False,
    )
    with pytest.raises(WikiValidationError, match="must rewrite"):
        await area_pages.apply_rename_plan(no_rewrite)

    unchanged = await areas.get_area(area["area_id"])
    assert unchanged is not None
    assert unchanged["name"] == "Dex"
    assert unchanged["page_path"] == "topics/dex.md"


@pytest.mark.asyncio
async def test_area_rename_applies_other_patch_fields_and_projects_once(tmp_path) -> None:
    areas = FakeAreas()
    synced: list[dict] = []
    projected: list[str] = []

    async def sync(area: dict) -> None:
        synced.append(dict(area))

    async def project() -> bool:
        projected.append("projected")
        return False

    area_lifecycle = lifecycle(areas, sync=sync)
    area = await area_lifecycle.create(name="Dex")
    area_pages = AreaPageService(
        wiki=WikiService(ManagedFileRepository(tmp_path / "wiki")),
        sessions=areas,
        lifecycle=area_lifecycle,
        project_wiki_state=project,
    )
    await area_pages.create(area["area_id"])
    projected.clear()

    updated = await area_pages.update(
        area["area_id"],
        name="Arden",
        autonomy="observe",
        attention="active",
    )

    assert updated["name"] == "Arden"
    assert updated["autonomy"] == "observe"
    assert updated["attention"] == "active"
    assert len(synced) == 1
    assert projected == ["projected"]

    current = area_pages._wiki.read_page(updated["page_id"])
    reverse = area_pages._wiki.prepare_rename(
        updated["page_id"],
        new_path="topics/dex.md",
        new_title="Dex",
        expected_version=current.resource.version_id,
        base_head=area_pages._wiki.repository.head,
    )
    await area_pages.apply_rename_plan(reverse)
    assert projected == ["projected"]


@pytest.mark.asyncio
async def test_detach_page_requires_custodian_to_be_disabled(tmp_path) -> None:
    areas = FakeAreas()
    area_lifecycle = lifecycle(areas)
    area = await area_lifecycle.create(name="Health", page_path="topics/health.md", autonomy="observe")
    area_pages = pages(tmp_path, areas, area_lifecycle)

    with pytest.raises(ValueError, match="Disable the Custodian"):
        await area_pages.detach(area["area_id"])
