import asyncio
import json

from pydantic import BaseModel, Field

from arden.agent.types.tools import ToolEffect, ToolOutcome, ToolOutcomeStatus
from arden.areas.agent import CUSTODIAN_ACTOR_PREFIX, AreaCustodianReport
from arden.areas.work_store import AreaWorkReportError, AreaWorkStore
from arden.revisions import RevisionConflictError
from arden.tools.core import ToolResult, tool
from arden.tools.core.context import ToolExecution
from arden.tools.core.formatting import format_lines_with_pagination
from arden.tools.core.types import ToolAction, ToolPolicy, ToolScope
from arden.wiki.constants import WIKI_POST_COMMIT_SERVICE
from arden.wiki.service import WikiService, WikiValidationError

WIKI_SERVICE = "wiki"
AREA_WORK_SERVICE = "area_work"


class AreaPageReadInput(BaseModel):
    offset: int = Field(default=1, ge=1)
    limit: int = Field(default=2_000, ge=1, le=4_000)


class AreaPagePatchInput(BaseModel):
    old_text: str = Field(min_length=1, max_length=100_000)
    new_text: str = Field(max_length=100_000)
    expected_version: str = Field(min_length=1, max_length=128)
    expected_head: str = Field(min_length=1, max_length=128)


class AreaPageWriteInput(BaseModel):
    content: str = Field(min_length=1, max_length=100_000, description="Complete Markdown body.")
    expected_version: str = Field(min_length=1, max_length=128)
    expected_head: str = Field(min_length=1, max_length=128)


class AreaAutomationRunInput(BaseModel):
    task_id: str = Field(min_length=1, max_length=200)


def _target(execution: ToolExecution):
    area = execution.ctx.area
    wiki = execution.ctx.services.get(WIKI_SERVICE)
    if area is None or not area.page_path:
        return ToolResult.failure(
            code="not_found",
            message="This Area has no attached page.",
            preview="No Area page",
            recovery_action="Attach a page to the Area before using Area page tools.",
        )
    if not isinstance(wiki, WikiService):
        return ToolResult.failure(
            code="not_configured",
            message="The managed wiki is unavailable.",
            preview="Wiki unavailable",
            recovery_action="Enable canonical memory before retrying.",
        )
    snapshot = wiki.snapshot()
    record = next((item for item in snapshot.pages if item.resource.path == area.page_path), None)
    if record is None or record.page.lifecycle != "active":
        return ToolResult.failure(
            code="not_found",
            message="The attached Area page is missing.",
            preview="Page missing",
            recovery_action="Restore the attached wiki page or update the Area page path.",
        )
    return wiki, snapshot.head, record


def _write_failure(exc: Exception) -> ToolResult:
    if isinstance(exc, RevisionConflictError):
        return ToolResult.failure(
            code="write_conflict",
            message="The Area wiki page changed after it was read.",
            preview="Write conflict",
            recovery_action="Read the Area page again, recompute the edit, and retry.",
        )
    if isinstance(exc, WikiValidationError | ValueError | UnicodeError):
        return ToolResult.failure(
            code="invalid_input",
            message=f"The Area page edit is invalid: {exc}",
            preview="Invalid page edit",
            recovery_action="Read the page and submit valid canonical Markdown.",
        )
    return ToolResult.failure(
        code="wiki_error",
        message="The Area page could not be updated.",
        preview="Page update failed",
        retryable=True,
        recovery_action="Read the Area page again and retry once; report the failure if it persists.",
    )


def _custodian_area_id(execution: ToolExecution) -> str | None:
    area = execution.ctx.area
    task_id = f"area:{area.area_id}" if area is not None else None
    if task_id is None or execution.ctx.run.loop_task_id != task_id or execution.ctx.run.automation_id != task_id:
        return None
    return area.area_id


async def area_page_read(execution: ToolExecution, args: AreaPageReadInput) -> ToolResult:
    target = await asyncio.to_thread(_target, execution)
    if isinstance(target, ToolResult):
        return target
    _wiki, head, record = target
    raw = record.content.decode("utf-8")
    content = format_lines_with_pagination(raw, args.offset, args.limit)
    metadata = {
        "page_id": record.page.page_id,
        "page_path": record.resource.path,
        "offset": args.offset,
        "version": record.resource.version_id,
        "head": head,
        "size": len(record.content),
    }
    return ToolResult(
        content=(
            f"Area page metadata: {json.dumps(metadata, ensure_ascii=False, separators=(',', ':'))}\n\n"
            f"Area page content:\n{content}"
        ),
        preview=record.page.title,
        data=metadata,
    )


async def area_page_patch(execution: ToolExecution, args: AreaPagePatchInput) -> ToolResult:
    target = await asyncio.to_thread(_target, execution)
    if isinstance(target, ToolResult):
        return target
    wiki, head, record = target
    if record.resource.version_id != args.expected_version or head != args.expected_head:
        return _write_failure(RevisionConflictError("Area page revision changed"))
    raw = record.content.decode("utf-8")
    matches = raw.count(args.old_text)
    if matches != 1:
        return ToolResult.failure(
            code="not_found" if matches == 0 else "ambiguous_ref",
            message="old_text was not found." if matches == 0 else f"old_text matches {matches} places.",
            preview="Patch not applied",
            recovery_action="Read the page and include enough exact surrounding text for one match.",
        )
    content = raw.replace(args.old_text, args.new_text, 1).encode()
    custodian_area_id = _custodian_area_id(execution)
    try:
        updated = await asyncio.to_thread(
            wiki.update_page,
            record.page.page_id,
            content=content,
            expected_version=args.expected_version,
            expected_head=args.expected_head,
            actor=(
                f"{CUSTODIAN_ACTOR_PREFIX}{custodian_area_id}"
                if custodian_area_id is not None
                else f"automation:{execution.ctx.run.automation_id or 'area'}"
            ),
            origin="area.page",
            reason="update attached Area page",
        )
    except Exception as exc:
        return _write_failure(exc)
    projection_pending = await execution.ctx.services[WIKI_POST_COMMIT_SERVICE]()
    return _updated_result(
        record,
        updated,
        wiki.repository.head,
        "Patched this Area's wiki page.",
        "Area page patched",
        projection_pending=projection_pending,
    )


async def area_page_write(execution: ToolExecution, args: AreaPageWriteInput) -> ToolResult:
    target = await asyncio.to_thread(_target, execution)
    if isinstance(target, ToolResult):
        return target
    wiki, head, record = target
    if record.resource.version_id != args.expected_version or head != args.expected_head:
        return _write_failure(RevisionConflictError("Area page revision changed"))
    content = record.page.with_body((args.content.rstrip() + "\n").encode()).to_bytes()
    custodian_area_id = _custodian_area_id(execution)
    try:
        updated = await asyncio.to_thread(
            wiki.update_page,
            record.page.page_id,
            content=content,
            expected_version=args.expected_version,
            expected_head=args.expected_head,
            actor=(
                f"{CUSTODIAN_ACTOR_PREFIX}{custodian_area_id}"
                if custodian_area_id is not None
                else f"automation:{execution.ctx.run.automation_id or 'area'}"
            ),
            origin="area.page",
            reason="replace attached Area page body",
        )
    except Exception as exc:
        return _write_failure(exc)
    projection_pending = await execution.ctx.services[WIKI_POST_COMMIT_SERVICE]()
    return _updated_result(
        record,
        updated,
        wiki.repository.head,
        "Updated this Area's wiki page.",
        "Area page updated",
        projection_pending=projection_pending,
    )


def _updated_result(
    before,
    after,
    head: str | None,
    content: str,
    preview: str,
    *,
    projection_pending: bool,
) -> ToolResult:
    metadata = {
        "page_id": after.page.page_id,
        "page_path": after.resource.path,
        "version": after.resource.version_id,
        "head": head,
        "size": len(after.content),
    }
    if projection_pending:
        metadata["projection_pending"] = True
    return ToolResult(
        content=(f"{content}\nArea page metadata: {json.dumps(metadata, ensure_ascii=False, separators=(',', ':'))}"),
        preview=preview,
        data=metadata,
        outcome=ToolOutcome(
            status=ToolOutcomeStatus.SUCCEEDED,
            effect=ToolEffect(
                operation="edit",
                target=after.resource.path,
                before_ref=before.resource.version_id,
                after_ref=after.resource.version_id,
            ),
        ),
    )


async def area_run_automation(execution: ToolExecution, args: AreaAutomationRunInput) -> ToolResult:
    area = execution.ctx.area
    service = execution.ctx.services.get("automation")
    prefix = f"area:{area.area_id}:" if area is not None else None
    if prefix is None or not args.task_id.startswith(prefix):
        return ToolResult.failure(
            code="permission_denied",
            message="Only child automations owned by the current Area can be run.",
            preview="Outside Area boundary",
            recovery_action="Use an automation ID returned by this Area's work context.",
        )
    if service is None:
        return ToolResult.failure(
            code="not_configured",
            message="Area automation service unavailable.",
            preview="Unavailable",
            recovery_action="End the run and report that Area automation is unavailable.",
        )
    try:
        await service.run_now(args.task_id)
    except KeyError:
        return ToolResult.failure(
            code="not_found",
            message="Area automation not found.",
            preview="Not found",
            recovery_action="Retry with an exact child automation ID from the current Area.",
        )
    except RuntimeError:
        return ToolResult.failure(
            code="temporarily_unavailable",
            message="The Area automation could not be started.",
            preview="Unavailable",
            retryable=True,
            recovery_action="Wait for the current Area run to settle, then retry once.",
        )
    return ToolResult(content=f"Started Area automation {args.task_id}.", preview="Area automation started")


async def submit_area_report(execution: ToolExecution, report: AreaCustodianReport) -> ToolResult:
    area_id = _custodian_area_id(execution)
    if area_id is None:
        return ToolResult.failure(
            code="forbidden",
            message="Area reports are accepted only from the current Area custodian run.",
            preview="Report not accepted",
            recovery_action="Call this tool only as the final action of the current Area custodian run.",
        )
    store = execution.ctx.get_client(AREA_WORK_SERVICE, AreaWorkStore)
    if store is None:
        return ToolResult.failure(
            code="not_configured",
            message="Area work storage is unavailable.",
            preview="Report unavailable",
            recovery_action="End the run and report that Area work storage is unavailable.",
        )
    try:
        applied = await store.apply_report(area_id, f"run:{execution.ctx.run.run_id}", report)
    except AreaWorkReportError as exc:
        return ToolResult.failure(
            code="invalid_area_report",
            message=str(exc),
            preview="Report rejected",
            recovery_action="Refresh current Area work, correct the report, and retry.",
        )
    return ToolResult(
        content="Area report accepted. End this run." if applied else "Area report was already accepted. End this run.",
        preview="Area report accepted",
        data={"accepted": True},
    )


_WIKI_PERMISSION = frozenset({WIKI_SERVICE})

area_page_read_tool = tool(
    display_name="AreaPageRead",
    display_description="Read the current Area wiki page.",
    description="Read the current Area's attached managed wiki page and exact version tokens.",
    input_model=AreaPageReadInput,
    policy=ToolPolicy(action=ToolAction.READ, scope=ToolScope.INTERNAL, permissions=_WIKI_PERMISSION),
    execute=area_page_read,
)

area_page_patch_tool = tool(
    display_name="AreaPagePatch",
    display_description="Patch the current Area wiki page.",
    description="Replace one exact block in the attached managed page using its version and wiki head.",
    input_model=AreaPagePatchInput,
    policy=ToolPolicy(
        action=ToolAction.WRITE,
        scope=ToolScope.INTERNAL,
        permissions=frozenset({WIKI_SERVICE, WIKI_POST_COMMIT_SERVICE}),
    ),
    execute=area_page_patch,
)

area_page_write_tool = tool(
    display_name="AreaPageWrite",
    display_description="Replace the current Area wiki page body.",
    description="Replace only the Markdown body of the attached managed page using exact version tokens.",
    input_model=AreaPageWriteInput,
    policy=ToolPolicy(
        action=ToolAction.WRITE,
        scope=ToolScope.INTERNAL,
        permissions=frozenset({WIKI_SERVICE, WIKI_POST_COMMIT_SERVICE}),
    ),
    execute=area_page_write,
)

area_run_automation_tool = tool(
    display_name="RunAreaAutomation",
    display_description="Run an automation for this Area.",
    description="Run a child automation owned by the current Area.",
    input_model=AreaAutomationRunInput,
    policy=ToolPolicy(action=ToolAction.EXECUTE, scope=ToolScope.INTERNAL, permissions=frozenset({"automation"})),
    execute=area_run_automation,
)

submit_area_report_tool = tool(
    display_name="SubmitAreaReport",
    display_description="Commit the current Area custodian report.",
    description=(
        "Submit exactly one final Area report. State conflicts are returned for correction in this run. "
        "After acceptance, end the run."
    ),
    input_model=AreaCustodianReport,
    policy=ToolPolicy(
        action=ToolAction.WRITE,
        scope=ToolScope.INTERNAL,
        permissions=frozenset({AREA_WORK_SERVICE}),
        idempotent=True,
    ),
    execute=submit_area_report,
)
