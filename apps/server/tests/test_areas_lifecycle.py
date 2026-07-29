from collections.abc import Awaitable, Callable

import pytest

from arden.areas.lifecycle import AreaLifecycleService, AreaPageService, AreaWikiUnavailable
from arden.revisions import ManagedFileRepository
from arden.wiki.service import WikiService


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
            "autonomy": values.get("autonomy"),
            "paused_at": None,
            "attention": "ambient",
            "interrupts": "asks",
            "default_cwd": values.get("default_cwd"),
            "instructions": values.get("instructions"),
            "knowledge_scope": values.get("knowledge_scope") or f"area:{area_id}",
        }
        self.rows[area_id] = row
        return dict(row)

    async def find_area_by_name(self, name: str) -> dict | None:
        return next((dict(row) for row in self.rows.values() if row["name"].casefold() == name.casefold()), None)

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
    assert [page.resource.path for page in wiki.list_pages()] == ["topics/dex.md"]


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
    page = area_pages._wiki.list_pages()[0].page
    text = page.to_bytes().decode()
    assert "title: O-1A / Visa" in text
    assert "## Open loops" in text


@pytest.mark.asyncio
async def test_detach_page_requires_custodian_to_be_disabled(tmp_path) -> None:
    areas = FakeAreas()
    area_lifecycle = lifecycle(areas)
    area = await area_lifecycle.create(name="Health", page_path="topics/health.md", autonomy="observe")
    area_pages = pages(tmp_path, areas, area_lifecycle)

    with pytest.raises(ValueError, match="Disable the Custodian"):
        await area_pages.detach(area["area_id"])
