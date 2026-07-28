from datetime import UTC, datetime
from pathlib import Path

import pytest

from arden.context.models import AreaContext, SessionState
from arden.revisions import ManagedFileRepository
from arden.tools.area import (
    AreaAutomationRunInput,
    AreaPagePatchInput,
    AreaPageReadInput,
    AreaPageWriteInput,
    area_page_patch,
    area_page_read,
    area_page_write,
    area_run_automation,
)
from arden.tools.core.context import BackgroundTaskRegistry, IOBridge, RunContext, ToolContext, ToolExecution
from arden.tools.core.registry import ToolRegistry
from arden.wiki import WikiService


def execution(
    wiki: WikiService,
    page_path: str | None = "topics/health.md",
    loop_task_id: str | None = "area:area_health",
) -> ToolExecution:
    area = AreaContext(area_id="area_health", name="Health", page_path=page_path)
    ctx = ToolContext(
        session_state=SessionState(session_id="custodian", started_at=datetime.now(UTC), area_id=area.area_id),
        registry=ToolRegistry(),
        run=RunContext(run_id="run-1", loop_task_id=loop_task_id),
        io=IOBridge(),
        services={"wiki": wiki},
        background_tasks=BackgroundTaskRegistry(session_id="custodian"),
        area=area,
    )
    return ToolExecution(tool_id="t1", tool_name="area_page", ctx=ctx)


class WriteProvenance:
    def __init__(self) -> None:
        self.writes: list[tuple[str, str]] = []

    def record_page_write(self, area_id: str, digest: str) -> None:
        self.writes.append((area_id, digest))


def wiki_at(root: Path) -> WikiService:
    return WikiService(
        ManagedFileRepository(
            root / "wiki" / "pages",
            history_root=root / "wiki" / ".wiki-history",
        )
    )


def seed(root: Path) -> WikiService:
    wiki = wiki_at(root)
    wiki.create_page(
        path="topics/health.md",
        title="Health",
        body=b"# Health\n\nOld status\n",
        page_id="area-health-page",
        expected_head=None,
        actor="test",
        origin="test",
        reason="seed",
    )
    return wiki


@pytest.mark.asyncio
async def test_area_page_tools_are_locked_to_active_area_page(tmp_path: Path) -> None:
    wiki = seed(tmp_path)
    wiki.create_page(
        path="topics/visa.md",
        title="Visa",
        body=b"Visa secret\n",
        page_id="visa",
        expected_head=wiki.repository.head,
        actor="test",
        origin="test",
        reason="seed",
    )
    run = execution(wiki)

    read = await area_page_read(run, AreaPageReadInput())
    patched = await area_page_patch(
        run,
        AreaPagePatchInput(
            old_text="Old status",
            new_text="Current status",
            expected_version=read.data["version"],
            expected_head=read.data["head"],
        ),
    )

    assert not read.is_error and "Old status" in read.content
    assert not patched.is_error and b"Current status" in wiki.read_page("area-health-page").content
    assert wiki.read_page("visa").page.body == b"Visa secret\n"


@pytest.mark.asyncio
async def test_area_page_write_preserves_frontmatter(tmp_path: Path) -> None:
    wiki = seed(tmp_path)
    before = wiki.read_page("area-health-page")
    head = wiki.repository.head
    assert head is not None

    result = await area_page_write(
        execution(wiki),
        AreaPageWriteInput(
            content="# Health\n\nNew body",
            expected_version=before.resource.version_id,
            expected_head=head,
        ),
    )

    after = wiki.read_page("area-health-page")
    assert not result.is_error
    assert result.outcome is not None and result.outcome.effect is not None
    assert result.outcome.effect.before_ref == before.resource.version_id
    assert result.outcome.effect.after_ref == after.resource.version_id
    assert after.page.title == "Health"
    assert after.page.body == b"# Health\n\nNew body\n"


@pytest.mark.asyncio
async def test_area_page_writes_record_exact_post_write_digest(tmp_path: Path) -> None:
    wiki = seed(tmp_path)
    run = execution(wiki)
    provenance = WriteProvenance()
    run.ctx.services["area_custodians"] = provenance
    before = wiki.read_page("area-health-page")
    head = wiki.repository.head
    assert head is not None

    result = await area_page_patch(
        run,
        AreaPagePatchInput(
            old_text="Old status",
            new_text="Current status",
            expected_version=before.resource.version_id,
            expected_head=head,
        ),
    )

    assert not result.is_error
    assert provenance.writes[0][0] == "area_health"
    assert len(provenance.writes[0][1]) == 64


@pytest.mark.asyncio
async def test_non_custodian_page_writes_record_no_digest(tmp_path: Path) -> None:
    """A user-directed assistant edit in the room must still wake the
    Custodian — only the Custodian's own runs record self-write digests."""
    wiki = seed(tmp_path)
    run = execution(wiki, loop_task_id=None)  # ordinary room chat, not a custodian run
    provenance = WriteProvenance()
    run.ctx.services["area_custodians"] = provenance
    before = wiki.read_page("area-health-page")
    head = wiki.repository.head
    assert head is not None

    result = await area_page_patch(
        run,
        AreaPagePatchInput(
            old_text="Old status",
            new_text="Current status",
            expected_version=before.resource.version_id,
            expected_head=head,
        ),
    )

    assert not result.is_error
    assert provenance.writes == []


@pytest.mark.asyncio
async def test_area_page_patch_rejects_stale_revision(tmp_path: Path) -> None:
    wiki = seed(tmp_path)
    before = wiki.read_page("area-health-page")
    stale_head = wiki.repository.head
    assert stale_head is not None
    wiki.update_page(
        "area-health-page",
        content=before.page.with_body(b"external version\n").to_bytes(),
        expected_version=before.resource.version_id,
        expected_head=stale_head,
        actor="test",
        origin="test",
        reason="concurrent edit",
    )

    result = await area_page_patch(
        execution(wiki),
        AreaPagePatchInput(
            old_text="Old status",
            new_text="Approved status",
            expected_version=before.resource.version_id,
            expected_head=stale_head,
        ),
    )

    assert result.is_error
    assert result.outcome is not None and result.outcome.error is not None
    assert result.outcome.error.code == "write_conflict"
    assert wiki.read_page("area-health-page").page.body == b"external version\n"


@pytest.mark.asyncio
async def test_area_page_tools_fail_closed_without_attached_page(tmp_path: Path) -> None:
    result = await area_page_read(execution(wiki_at(tmp_path), page_path=None), AreaPageReadInput())

    assert result.is_error
    assert "attached page" in result.content


@pytest.mark.asyncio
async def test_area_automation_run_is_locked_to_owned_children(tmp_path: Path) -> None:
    calls: list[str] = []

    class Automations:
        async def run_now(self, task_id: str) -> None:
            calls.append(task_id)

    run = execution(wiki_at(tmp_path))
    run.ctx.services["automation"] = Automations()

    allowed = await area_run_automation(run, AreaAutomationRunInput(task_id="area:area_health:daily"))
    foreign = await area_run_automation(run, AreaAutomationRunInput(task_id="area:other:daily"))
    recursive = await area_run_automation(run, AreaAutomationRunInput(task_id="area:area_health"))

    assert not allowed.is_error
    assert foreign.is_error and recursive.is_error
    assert calls == ["area:area_health:daily"]
