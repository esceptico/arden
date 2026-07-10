from datetime import UTC, datetime
from pathlib import Path

import pytest

from ntrp.context.models import AreaContext, SessionState
from ntrp.tools.area import (
    AreaPagePatchInput,
    AreaPageReadInput,
    AreaPageWriteInput,
    area_page_patch,
    area_page_read,
    area_page_write,
)
from ntrp.tools.core.context import BackgroundTaskRegistry, IOBridge, RunContext, ToolContext, ToolExecution
from ntrp.tools.core.registry import ToolRegistry


def execution(vault: Path, page_path: str | None = "topics/health.md") -> ToolExecution:
    area = AreaContext(area_id="area_health", name="Health", page_path=page_path)
    ctx = ToolContext(
        session_state=SessionState(session_id="custodian", started_at=datetime.now(UTC), area_id=area.area_id),
        registry=ToolRegistry(),
        run=RunContext(run_id="run-1"),
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
    patched = await area_page_patch(run, AreaPagePatchInput(old_text="Old status", new_text="Current status"))

    assert not read.is_error and "Old status" in read.content
    assert not patched.is_error and "Current status" in page.read_text()
    assert other.read_text() == "Visa secret"


@pytest.mark.asyncio
async def test_area_page_write_preserves_frontmatter(tmp_path: Path) -> None:
    vault = tmp_path / "memory"
    page = seed(vault)

    result = await area_page_write(execution(vault), AreaPageWriteInput(content="# Health\n\nNew body"))

    assert not result.is_error
    assert page.read_text().startswith("---\ntitle: Health\n---")
    assert page.read_text().endswith("# Health\n\nNew body\n")


@pytest.mark.asyncio
async def test_area_page_writes_record_exact_post_write_digest(tmp_path: Path) -> None:
    vault = tmp_path / "memory"
    seed(vault)
    run = execution(vault)
    provenance = WriteProvenance()
    run.ctx.services["area_custodians"] = provenance

    result = await area_page_patch(run, AreaPagePatchInput(old_text="Old status", new_text="Current status"))

    assert not result.is_error
    assert provenance.writes[0][0] == "area_health"
    assert len(provenance.writes[0][1]) == 64


@pytest.mark.asyncio
async def test_area_page_tools_fail_closed_without_attached_page(tmp_path: Path) -> None:
    result = await area_page_read(execution(tmp_path / "memory", page_path=None), AreaPageReadInput())

    assert result.is_error
    assert "attached page" in result.content
