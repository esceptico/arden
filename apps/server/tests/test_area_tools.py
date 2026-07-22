from datetime import UTC, datetime
from pathlib import Path

import pytest

from arden.context.models import AreaContext, SessionState
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
from arden.tools.core.file_mutation import file_revision
from arden.tools.core.registry import ToolRegistry


def execution(
    vault: Path,
    page_path: str | None = "topics/health.md",
    loop_task_id: str | None = "area:area_health",
) -> ToolExecution:
    area = AreaContext(area_id="area_health", name="Health", page_path=page_path)
    ctx = ToolContext(
        session_state=SessionState(session_id="custodian", started_at=datetime.now(UTC), area_id=area.area_id),
        registry=ToolRegistry(),
        run=RunContext(run_id="run-1", loop_task_id=loop_task_id),
        io=IOBridge(),
        services={"area_pages": vault},
        background_tasks=BackgroundTaskRegistry(session_id="custodian"),
        area=area,
    )
    return ToolExecution(tool_id="t1", tool_name="area_page", ctx=ctx)


class WriteProvenance:
    def __init__(self) -> None:
        self.writes: list[tuple[str, str]] = []

    def record_page_write(self, area_id: str, digest: str) -> None:
        self.writes.append((area_id, digest))


def seed(vault: Path) -> Path:
    page = vault / "topics" / "health.md"
    page.parent.mkdir(parents=True)
    page.write_text("---\ntitle: Health\n---\n\n# Health\n\nOld status\n", encoding="utf-8")
    return page


@pytest.mark.asyncio
async def test_area_page_tools_are_locked_to_active_area_page(tmp_path: Path) -> None:
    vault = tmp_path / "memory"
    page = seed(vault)
    other = vault / "topics" / "visa.md"
    other.write_text("Visa secret", encoding="utf-8")
    run = execution(vault)

    read = await area_page_read(run, AreaPageReadInput())
    patched = await area_page_patch(
        run,
        AreaPagePatchInput(
            old_text="Old status",
            new_text="Current status",
            expected_sha256=read.data["sha256"],
        ),
    )

    assert not read.is_error and "Old status" in read.content
    assert not patched.is_error and "Current status" in page.read_text()
    assert other.read_text() == "Visa secret"


@pytest.mark.asyncio
async def test_area_page_write_preserves_frontmatter(tmp_path: Path) -> None:
    vault = tmp_path / "memory"
    page = seed(vault)
    expected = file_revision(page).sha256

    result = await area_page_write(
        execution(vault),
        AreaPageWriteInput(
            content="# Health\n\nNew body",
            expected_sha256=expected,
        ),
    )

    assert not result.is_error
    assert result.outcome is not None and result.outcome.effect is not None
    assert result.outcome.effect.before_ref == expected
    assert result.outcome.effect.after_ref == file_revision(page).sha256
    assert page.read_text().startswith("---\ntitle: Health\n---")
    assert page.read_text().endswith("# Health\n\nNew body\n")


@pytest.mark.asyncio
async def test_area_page_writes_record_exact_post_write_digest(tmp_path: Path) -> None:
    vault = tmp_path / "memory"
    seed(vault)
    run = execution(vault)
    provenance = WriteProvenance()
    run.ctx.services["area_custodians"] = provenance

    result = await area_page_patch(
        run,
        AreaPagePatchInput(
            old_text="Old status",
            new_text="Current status",
            expected_sha256=file_revision(vault / "topics" / "health.md").sha256,
        ),
    )

    assert not result.is_error
    assert provenance.writes[0][0] == "area_health"
    assert len(provenance.writes[0][1]) == 64


@pytest.mark.asyncio
async def test_non_custodian_page_writes_record_no_digest(tmp_path: Path) -> None:
    """A user-directed assistant edit in the room must still wake the
    Custodian — only the Custodian's own runs record self-write digests."""
    vault = tmp_path / "memory"
    seed(vault)
    run = execution(vault, loop_task_id=None)  # ordinary room chat, not a custodian run
    provenance = WriteProvenance()
    run.ctx.services["area_custodians"] = provenance

    result = await area_page_patch(
        run,
        AreaPagePatchInput(
            old_text="Old status",
            new_text="Current status",
            expected_sha256=file_revision(vault / "topics" / "health.md").sha256,
        ),
    )

    assert not result.is_error
    assert provenance.writes == []


@pytest.mark.asyncio
async def test_area_page_patch_rejects_stale_revision(tmp_path: Path) -> None:
    vault = tmp_path / "memory"
    page = seed(vault)
    expected = file_revision(page).sha256
    page.write_text("external version\n", encoding="utf-8")

    result = await area_page_patch(
        execution(vault),
        AreaPagePatchInput(
            old_text="Old status",
            new_text="Approved status",
            expected_sha256=expected,
        ),
    )

    assert result.is_error
    assert result.outcome is not None and result.outcome.error is not None
    assert result.outcome.error.code == "write_conflict"
    assert page.read_text(encoding="utf-8") == "external version\n"


@pytest.mark.asyncio
async def test_area_page_tools_fail_closed_without_attached_page(tmp_path: Path) -> None:
    result = await area_page_read(execution(tmp_path / "memory", page_path=None), AreaPageReadInput())

    assert result.is_error
    assert "attached page" in result.content


@pytest.mark.asyncio
async def test_area_automation_run_is_locked_to_owned_children(tmp_path: Path) -> None:
    calls: list[str] = []

    class Automations:
        async def run_now(self, task_id: str) -> None:
            calls.append(task_id)

    run = execution(tmp_path / "memory")
    run.ctx.services["automation"] = Automations()

    allowed = await area_run_automation(run, AreaAutomationRunInput(task_id="area:area_health:daily"))
    foreign = await area_run_automation(run, AreaAutomationRunInput(task_id="area:other:daily"))
    recursive = await area_run_automation(run, AreaAutomationRunInput(task_id="area:area_health"))

    assert not allowed.is_error
    assert foreign.is_error and recursive.is_error
    assert calls == ["area:area_health:daily"]
