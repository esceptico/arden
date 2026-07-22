from hashlib import sha256
from pathlib import Path

from pydantic import BaseModel, Field

from arden.agent.types.tools import ToolEffect, ToolOutcome, ToolOutcomeStatus
from arden.areas.paths import resolve_area_page
from arden.tools.core import ToolResult, tool
from arden.tools.core.context import ToolExecution
from arden.tools.core.file_mutation import (
    RevisionConflict,
    atomic_compare_and_swap,
    read_file_snapshot,
    revision_or_absent,
)
from arden.tools.core.formatting import format_lines_with_pagination
from arden.tools.core.types import ToolAction, ToolPolicy, ToolScope

AREA_PAGES_SERVICE = "area_pages"


class AreaPageReadInput(BaseModel):
    offset: int = Field(default=1, ge=1)
    limit: int = Field(default=2_000, ge=1, le=4_000)


class AreaPagePatchInput(BaseModel):
    old_text: str = Field(min_length=1, max_length=100_000)
    new_text: str = Field(max_length=100_000)
    expected_sha256: str = Field(
        min_length=6, max_length=64, description="SHA-256 returned by area_page_read for the page being edited."
    )


class AreaPageWriteInput(BaseModel):
    content: str = Field(
        min_length=1, max_length=100_000, description="Complete Markdown body; frontmatter is preserved."
    )
    expected_sha256: str = Field(
        description="SHA-256 returned by area_page_read, or the literal 'absent' when creating the page."
    )


class AreaAutomationRunInput(BaseModel):
    task_id: str = Field(min_length=1, max_length=200)


def _target(execution: ToolExecution) -> Path | ToolResult:
    area = execution.ctx.area
    vault = execution.ctx.services.get(AREA_PAGES_SERVICE)
    if area is None or not area.page_path:
        return ToolResult.failure(
            code="not_found",
            message="This Area has no attached page.",
            preview="No Area page",
            recovery_action="Attach a page to the Area before using Area page tools.",
        )
    if not isinstance(vault, Path):
        return ToolResult.failure(
            code="not_configured",
            message="Area page service is unavailable.",
            preview="Unavailable",
            recovery_action="Configure the Area page vault before retrying.",
        )
    try:
        return resolve_area_page(vault, area.page_path)
    except ValueError:
        return ToolResult.failure(
            code="invalid_ref",
            message="The attached Area page path is invalid.",
            preview="Invalid Area page",
            recovery_action="Attach a valid relative page path inside the configured Area vault.",
        )


def _frontmatter_prefix(raw: str) -> str:
    if not raw.startswith("---\n"):
        return ""
    end = raw.find("\n---\n", 4)
    return raw[: end + 5] if end >= 0 else ""


def _revision_conflict(target: Path, expected_sha256: str) -> ToolResult | None:
    observed = revision_or_absent(target)
    if observed == expected_sha256:
        return None
    return ToolResult.failure(
        code="write_conflict",
        message=(
            f"Area page changed since it was read. Expected {expected_sha256}, observed {observed}. "
            "Read it again before writing."
        ),
        preview="Write conflict",
        recovery_action="Read the Area page, recompute the change, and retry with its new sha256.",
    )


def _record_write(execution: ToolExecution, written: str) -> None:
    """Self-write provenance: only the Custodian's own automation runs record
    digests. An edit made from any other session (the user working through
    the room's assistant, another agent) must still wake the Custodian."""
    area = execution.ctx.area
    if area is None or execution.ctx.run.loop_task_id != f"area:{area.area_id}":
        return
    provenance = execution.ctx.services.get("area_custodians")
    if provenance is not None:
        # Hash the written string — the CAS writer wrote exactly these UTF-8
        # bytes, so this matches the watcher's read_bytes() digest.
        provenance.record_page_write(area.area_id, sha256(written.encode("utf-8")).hexdigest())


async def area_page_read(execution: ToolExecution, args: AreaPageReadInput) -> ToolResult:
    target = _target(execution)
    if isinstance(target, ToolResult):
        return target
    if not target.is_file():
        return ToolResult.failure(
            code="not_found",
            message="The attached Area page is missing.",
            preview="Page missing",
            recovery_action="Restore the attached page or update the Area page path.",
        )
    snapshot, revision = read_file_snapshot(target)
    raw = snapshot.decode("utf-8")
    content = format_lines_with_pagination(raw, args.offset, args.limit)
    return ToolResult(
        content=content,
        preview=content.splitlines()[0],
        data={
            "page_path": execution.ctx.area.page_path,
            "offset": args.offset,
            "sha256": revision.sha256,
            "size": revision.size,
        },
    )


async def area_page_patch(execution: ToolExecution, args: AreaPagePatchInput) -> ToolResult:
    target = _target(execution)
    if isinstance(target, ToolResult):
        return target
    if conflict := _revision_conflict(target, args.expected_sha256):
        return conflict
    raw = target.read_text(encoding="utf-8") if target.exists() else ""
    matches = raw.count(args.old_text)
    if matches == 0:
        return ToolResult.failure(
            code="not_found",
            message="old_text was not found in the Area page.",
            preview="Patch not applied",
            recovery_action="Read the page and copy the exact block, including whitespace.",
        )
    if matches > 1:
        return ToolResult.failure(
            code="ambiguous_ref",
            message=f"old_text matches {matches} places.",
            preview="Patch not applied",
            recovery_action="Include more surrounding lines so the block is unique.",
        )
    written = raw.replace(args.old_text, args.new_text, 1)
    try:
        revision = atomic_compare_and_swap(target, written, args.expected_sha256)
    except RevisionConflict:
        return _revision_conflict(target, args.expected_sha256) or ToolResult.failure(
            code="write_conflict",
            message="Area page changed during the write. Read it again before retrying.",
            preview="Write conflict",
        )
    _record_write(execution, written)
    return ToolResult(
        content="Patched this Area's page.",
        preview="Area page patched",
        data={"sha256": revision.sha256, "size": revision.size},
        outcome=ToolOutcome(
            status=ToolOutcomeStatus.SUCCEEDED,
            effect=ToolEffect(
                operation="edit",
                target=str(target),
                before_ref=args.expected_sha256,
                after_ref=revision.sha256,
            ),
        ),
    )


async def area_page_write(execution: ToolExecution, args: AreaPageWriteInput) -> ToolResult:
    target = _target(execution)
    if isinstance(target, ToolResult):
        return target
    if conflict := _revision_conflict(target, args.expected_sha256):
        return conflict
    existing = target.read_text(encoding="utf-8") if target.exists() else ""
    body = args.content.strip() + "\n"
    prefix = _frontmatter_prefix(existing)
    written = f"{prefix}\n{body}" if prefix else body
    try:
        revision = atomic_compare_and_swap(target, written, args.expected_sha256)
    except RevisionConflict:
        return _revision_conflict(target, args.expected_sha256) or ToolResult.failure(
            code="write_conflict",
            message="Area page changed during the write. Read it again before retrying.",
            preview="Write conflict",
        )
    _record_write(execution, written)
    return ToolResult(
        content="Updated this Area's page.",
        preview="Area page updated",
        data={"sha256": revision.sha256, "size": revision.size},
        outcome=ToolOutcome(
            status=ToolOutcomeStatus.SUCCEEDED,
            effect=ToolEffect(
                operation="create" if args.expected_sha256 == "absent" else "replace",
                target=str(target),
                before_ref=args.expected_sha256,
                after_ref=revision.sha256,
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
            message="Automation service unavailable.",
            preview="Unavailable",
            recovery_action="Enable automation support before retrying.",
        )
    try:
        await service.run_now(args.task_id)
    except KeyError:
        return ToolResult.failure(
            code="not_found",
            message="Area automation not found.",
            preview="Not found",
            recovery_action="Refresh the Area work context and retry with an exact child automation ID.",
        )
    except RuntimeError:
        return ToolResult.failure(
            code="temporarily_unavailable",
            message="The Area automation could not be started.",
            preview="Unavailable",
            retryable=True,
            recovery_action="Check the current Area automation state before retrying.",
        )
    return ToolResult(content=f"Started Area automation {args.task_id}.", preview="Area automation started")


_AREA_PERMISSION = frozenset({AREA_PAGES_SERVICE})

area_page_read_tool = tool(
    display_name="AreaPageRead",
    description=(
        "Read the current Area's attached page and its SHA-256 revision. "
        "The path is fixed by the Area and cannot be overridden."
    ),
    input_model=AreaPageReadInput,
    policy=ToolPolicy(action=ToolAction.READ, scope=ToolScope.INTERNAL, permissions=_AREA_PERMISSION),
    execute=area_page_read,
)

area_page_patch_tool = tool(
    display_name="AreaPagePatch",
    description=(
        "Replace one exact block in the current Area's attached page using the sha256 from area_page_read. "
        "Cannot edit any other page."
    ),
    input_model=AreaPagePatchInput,
    policy=ToolPolicy(action=ToolAction.WRITE, scope=ToolScope.INTERNAL, permissions=_AREA_PERMISSION),
    execute=area_page_patch,
)

area_page_write_tool = tool(
    display_name="AreaPageWrite",
    description=(
        "Replace the Markdown body of the current Area's attached page while preserving frontmatter. "
        "Pass the sha256 from area_page_read as expected_sha256."
    ),
    input_model=AreaPageWriteInput,
    policy=ToolPolicy(action=ToolAction.WRITE, scope=ToolScope.INTERNAL, permissions=_AREA_PERMISSION),
    execute=area_page_write,
)

area_run_automation_tool = tool(
    display_name="RunAreaAutomation",
    description="Run a child automation owned by the current Area. Cannot target the Custodian itself or another Area.",
    input_model=AreaAutomationRunInput,
    policy=ToolPolicy(action=ToolAction.EXECUTE, scope=ToolScope.INTERNAL, permissions=frozenset({"automation"})),
    execute=area_run_automation,
)
