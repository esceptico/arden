from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from arden.areas.agent import AreaCustodianReport
from arden.areas.work_store import AreaWorkReportError, AreaWorkStore
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
    submit_area_report,
)
from arden.tools.core.context import BackgroundTaskRegistry, IOBridge, RunContext, ToolContext, ToolExecution
from arden.tools.core.registry import ToolRegistry
from arden.wiki.constants import WIKI_POST_COMMIT_SERVICE
from arden.wiki.service import WikiService


def execution(
    wiki: WikiService,
    page_path: str | None = "topics/health.md",
    loop_task_id: str | None = "area:area_health",
    automation_id: str | None = None,
    services: dict | None = None,
    post_commit=None,
) -> ToolExecution:
    async def noop_post_commit() -> bool:
        return False

    area = AreaContext(area_id="area_health", name="Health", page_path=page_path)
    ctx = ToolContext(
        session_state=SessionState(session_id="custodian", started_at=datetime.now(UTC), area_id=area.area_id),
        registry=ToolRegistry(),
        run=RunContext(run_id="run-1", loop_task_id=loop_task_id, automation_id=automation_id),
        io=IOBridge(),
        services={
            "wiki": wiki,
            WIKI_POST_COMMIT_SERVICE: post_commit or noop_post_commit,
            **(services or {}),
        },
        background_tasks=BackgroundTaskRegistry(session_id="custodian"),
        area=area,
    )
    return ToolExecution(tool_id="t1", tool_name="area_page", ctx=ctx)


def report() -> AreaCustodianReport:
    return AreaCustodianReport.model_validate(
        {
            "asks": [],
            "report": "Checked current work.",
            "next_check_hours": 24,
            "next_check_reason": "No earlier event is expected.",
            "made_progress": False,
            "work_remaining": True,
        }
    )


def test_area_report_rejects_unknown_nested_fields() -> None:
    payload = report().model_dump()
    payload["outcome_changes"] = [
        {
            "op": "create",
            "key": "health",
            "title": "Health",
            "success_criteria": "Stable",
            "priority": 1,
            "unexpected": True,
        }
    ]

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        AreaCustodianReport.model_validate(payload)


class ProjectionRecorder:
    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self) -> bool:
        self.calls += 1
        return False


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
    before = wiki.read_page("area-health-page")

    read = await area_page_read(run, AreaPageReadInput())
    patched = await area_page_patch(
        run,
        AreaPagePatchInput(
            old_text="Old status",
            new_text="Current status",
        ),
    )

    assert not read.is_error and "Old status" in read.content
    assert not patched.is_error and b"Current status" in wiki.read_page("area-health-page").content
    current = wiki.read_page("area-health-page")
    assert "page_id:" not in read.content
    assert read.data is not None and read.data["path"] == "topics/health.md"
    assert "page_id" not in read.serialized_payload()
    assert "page_id" not in patched.serialized_payload()
    assert before.resource.version_id not in read.content
    assert wiki.repository.head not in read.content
    assert current.resource.version_id not in patched.content
    assert wiki.repository.head not in patched.content
    assert wiki.read_page("visa").page.body == b"Visa secret\n"


@pytest.mark.asyncio
async def test_area_page_write_preserves_frontmatter(tmp_path: Path) -> None:
    wiki = seed(tmp_path)
    run = execution(wiki)
    await area_page_read(run, AreaPageReadInput())

    result = await area_page_write(
        run,
        AreaPageWriteInput(
            content="# Health\n\nNew body",
        ),
    )

    after = wiki.read_page("area-health-page")
    assert not result.is_error
    assert result.outcome is not None and result.outcome.effect is not None
    assert result.outcome.effect.before_ref is None
    assert result.outcome.effect.after_ref is None
    assert after.page.title == "Health"
    assert after.page.body == b"# Health\n\nNew body\n"


@pytest.mark.asyncio
async def test_custodian_page_write_projects_without_self_wake(tmp_path: Path) -> None:
    wiki = seed(tmp_path)
    projection = ProjectionRecorder()
    run = execution(wiki, automation_id="area:area_health", post_commit=projection)
    await area_page_read(run, AreaPageReadInput())

    result = await area_page_patch(
        run,
        AreaPagePatchInput(
            old_text="Old status",
            new_text="Current status",
        ),
    )

    assert not result.is_error
    assert projection.calls == 1
    assert wiki.repository.history(limit=1)[0].actor == "automation:area:area_health"


@pytest.mark.asyncio
async def test_non_custodian_page_write_projects_as_external_area_change(tmp_path: Path) -> None:
    wiki = seed(tmp_path)
    projection = ProjectionRecorder()
    run = execution(wiki, loop_task_id=None, post_commit=projection)
    await area_page_read(run, AreaPageReadInput())

    result = await area_page_patch(
        run,
        AreaPagePatchInput(
            old_text="Old status",
            new_text="Current status",
        ),
    )

    assert not result.is_error
    assert projection.calls == 1
    assert wiki.repository.history(limit=1)[0].actor == "automation:area"


@pytest.mark.asyncio
async def test_area_page_write_stays_successful_when_projection_is_pending(tmp_path: Path) -> None:
    wiki = seed(tmp_path)

    async def defer_projection() -> bool:
        return True

    run = execution(wiki, post_commit=defer_projection)
    await area_page_read(run, AreaPageReadInput())
    result = await area_page_patch(
        run,
        AreaPagePatchInput(
            old_text="Old status",
            new_text="Current status",
        ),
    )

    assert not result.is_error
    assert result.data is not None and result.data["projection_pending"] is True
    assert b"Current status" in wiki.read_page("area-health-page").content


@pytest.mark.asyncio
async def test_area_page_patch_rejects_stale_revision(tmp_path: Path) -> None:
    wiki = seed(tmp_path)
    before = wiki.read_page("area-health-page")
    stale_head = wiki.repository.head
    assert stale_head is not None
    run = execution(wiki)
    await area_page_read(run, AreaPageReadInput())
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
        run,
        AreaPagePatchInput(
            old_text="Old status",
            new_text="Approved status",
        ),
    )

    assert result.is_error
    assert result.outcome is not None and result.outcome.error is not None
    assert result.outcome.error.code == "write_conflict"
    assert wiki.read_page("area-health-page").page.body == b"external version\n"


@pytest.mark.asyncio
async def test_area_page_patch_allows_an_unrelated_wiki_commit(tmp_path: Path) -> None:
    wiki = seed(tmp_path)
    run = execution(wiki)
    await area_page_read(run, AreaPageReadInput())
    wiki.create_page(
        path="topics/unrelated.md",
        title="Unrelated",
        expected_head=wiki.repository.head,
        actor="test",
        origin="test",
        reason="unrelated change",
    )

    result = await area_page_patch(
        run,
        AreaPagePatchInput(old_text="Old status", new_text="Current status"),
    )

    assert not result.is_error
    assert wiki.read_page("area-health-page").page.body == b"# Health\n\nCurrent status\n"


@pytest.mark.asyncio
async def test_area_page_write_requires_a_fresh_full_read(tmp_path: Path) -> None:
    result = await area_page_write(execution(seed(tmp_path)), AreaPageWriteInput(content="New body"))

    assert result.is_error
    assert result.outcome is not None and result.outcome.error is not None
    assert result.outcome.error.code == "fresh_read_required"


@pytest.mark.asyncio
async def test_area_page_write_requires_the_complete_page(tmp_path: Path) -> None:
    wiki = seed(tmp_path)
    run = execution(wiki)
    await area_page_read(run, AreaPageReadInput(limit=1))

    result = await area_page_write(run, AreaPageWriteInput(content="New body"))

    assert result.is_error
    assert result.outcome is not None and result.outcome.error is not None
    assert result.outcome.error.code == "fresh_read_required"


@pytest.mark.asyncio
async def test_area_page_write_rejects_a_read_that_will_be_offloaded(tmp_path: Path) -> None:
    wiki = seed(tmp_path)
    current = wiki.read_page("area-health-page")
    wiki.update_page(
        current.page.page_id,
        content=current.page.with_body(("x" * 60_000).encode()).to_bytes(),
        expected_version=current.resource.version_id,
        expected_head=wiki.repository.head,
    )
    run = execution(wiki)

    await area_page_read(run, AreaPageReadInput())
    result = await area_page_write(run, AreaPageWriteInput(content="New body"))

    assert result.is_error
    assert result.outcome is not None and result.outcome.error is not None
    assert result.outcome.error.code == "fresh_read_required"
    assert wiki.read_page("area-health-page").page.body == ("x" * 60_000).encode()


@pytest.mark.asyncio
async def test_area_page_patch_accepts_a_partial_read(tmp_path: Path) -> None:
    wiki = seed(tmp_path)
    run = execution(wiki)
    await area_page_read(run, AreaPageReadInput(limit=1))

    result = await area_page_patch(run, AreaPagePatchInput(old_text="Old status", new_text="New status"))
    replacement = await area_page_write(run, AreaPageWriteInput(content="Overwrite"))

    assert not result.is_error
    assert replacement.is_error
    assert replacement.outcome is not None and replacement.outcome.error is not None
    assert replacement.outcome.error.code == "fresh_read_required"
    assert wiki.read_page("area-health-page").page.body == b"# Health\n\nNew status\n"


def test_area_page_inputs_reject_hashes_and_extra_fields() -> None:
    for model in (AreaPageReadInput, AreaPagePatchInput, AreaPageWriteInput):
        assert "expected_version" not in model.model_json_schema()["properties"]
        assert "expected_head" not in model.model_json_schema()["properties"]

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        AreaPagePatchInput.model_validate({"old_text": "old", "new_text": "new", "expected_head": "a" * 64})


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


@pytest.mark.asyncio
async def test_area_report_is_committed_inside_the_trusted_custodian_run(tmp_path: Path) -> None:
    class Work(AreaWorkStore):
        def __init__(self) -> None:
            self.calls: list[tuple[str, str, AreaCustodianReport]] = []

        async def apply_report(self, area_id: str, run_ref: str, submitted: AreaCustodianReport) -> bool:
            self.calls.append((area_id, run_ref, submitted))
            return True

    work = Work()
    run = execution(
        wiki_at(tmp_path),
        automation_id="area:area_health",
        services={"area_work": work},
    )

    result = await submit_area_report(run, report())

    assert not result.is_error
    assert result.data == {"accepted": True}
    assert work.calls == [("area_health", "run:run-1", report())]


@pytest.mark.asyncio
async def test_area_report_rejects_untrusted_identity_and_state_conflicts(tmp_path: Path) -> None:
    class Work(AreaWorkStore):
        def __init__(self) -> None:
            pass

        async def apply_report(self, area_id: str, run_ref: str, submitted: AreaCustodianReport) -> bool:
            raise AreaWorkReportError("Outcome changed since the run started")

    work = Work()
    untrusted = await submit_area_report(
        execution(wiki_at(tmp_path), services={"area_work": work}),
        report(),
    )
    conflict = await submit_area_report(
        execution(
            wiki_at(tmp_path),
            automation_id="area:area_health",
            services={"area_work": work},
        ),
        report(),
    )

    assert untrusted.is_error and "only from the current Area custodian" in untrusted.content
    assert conflict.is_error and "changed since the run started" in conflict.content
