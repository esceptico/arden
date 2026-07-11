from hashlib import sha256
from pathlib import Path

from pydantic import BaseModel, Field

from ntrp.areas.paths import atomic_write_text, resolve_area_page
from ntrp.tools.core import ToolResult, tool
from ntrp.tools.core.context import ToolExecution
from ntrp.tools.core.types import ToolAction, ToolPolicy, ToolScope

AREA_PAGES_SERVICE = "area_pages"


class AreaPageReadInput(BaseModel):
    offset: int = Field(default=1, ge=1)
    limit: int = Field(default=2_000, ge=1, le=4_000)


class AreaPagePatchInput(BaseModel):
    old_text: str = Field(min_length=1)
    new_text: str


class AreaPageWriteInput(BaseModel):
    content: str = Field(min_length=1, max_length=100_000, description="Complete Markdown body; frontmatter is preserved.")


class AreaAutomationRunInput(BaseModel):
    task_id: str = Field(min_length=1, max_length=200)


def _target(execution: ToolExecution) -> Path | ToolResult:
    area = execution.ctx.area
    vault = execution.ctx.services.get(AREA_PAGES_SERVICE)
    if area is None or not area.page_path:
        return ToolResult(content="This Area has no attached page.", preview="No Area page", is_error=True)
    if not isinstance(vault, Path):
        return ToolResult(content="Area page service is unavailable.", preview="Unavailable", is_error=True)
    try:
        return resolve_area_page(vault, area.page_path)
    except ValueError as exc:
        return ToolResult(content=f"Invalid Area page: {exc}", preview="Invalid Area page", is_error=True)


def _frontmatter_prefix(raw: str) -> str:
    if not raw.startswith("---\n"):
        return ""
    end = raw.find("\n---\n", 4)
    return raw[: end + 5] if end >= 0 else ""


def _record_write(execution: ToolExecution, target: Path) -> None:
    """Self-write provenance: only the Custodian's own automation runs record
    digests. An edit made from any other session (the user working through
    the room's assistant, another agent) must still wake the Custodian."""
    area = execution.ctx.area
    if area is None or execution.ctx.run.loop_task_id != f"area:{area.area_id}":
        return
    provenance = execution.ctx.services.get("area_custodians")
    if provenance is not None:
        provenance.record_page_write(area.area_id, sha256(target.read_bytes()).hexdigest())


async def area_page_read(execution: ToolExecution, args: AreaPageReadInput) -> ToolResult:
    target = _target(execution)
    if isinstance(target, ToolResult):
        return target
    if not target.is_file():
        return ToolResult(content="The attached Area page is missing.", preview="Page missing", is_error=True)
    lines = target.read_text(encoding="utf-8").splitlines()
    selected = lines[args.offset - 1 : args.offset - 1 + args.limit]
    return ToolResult(
        content="\n".join(selected),
        preview=f"Read {len(selected)} lines",
        data={"page_path": execution.ctx.area.page_path, "offset": args.offset},
    )


async def area_page_patch(execution: ToolExecution, args: AreaPagePatchInput) -> ToolResult:
    target = _target(execution)
    if isinstance(target, ToolResult):
        return target
    raw = target.read_text(encoding="utf-8") if target.exists() else ""
    matches = raw.count(args.old_text)
    if matches != 1:
        return ToolResult(
            content=f"Patch requires exactly one match; found {matches}.",
            preview="Patch not applied",
            is_error=True,
        )
    atomic_write_text(target, raw.replace(args.old_text, args.new_text, 1))
    _record_write(execution, target)
    return ToolResult(content="Patched this Area's page.", preview="Area page patched")


async def area_page_write(execution: ToolExecution, args: AreaPageWriteInput) -> ToolResult:
    target = _target(execution)
    if isinstance(target, ToolResult):
        return target
    existing = target.read_text(encoding="utf-8") if target.exists() else ""
    body = args.content.strip() + "\n"
    prefix = _frontmatter_prefix(existing)
    atomic_write_text(target, f"{prefix}\n{body}" if prefix else body)
    _record_write(execution, target)
    return ToolResult(content="Updated this Area's page.", preview="Area page updated")


async def area_run_automation(execution: ToolExecution, args: AreaAutomationRunInput) -> ToolResult:
    area = execution.ctx.area
    service = execution.ctx.services.get("automation")
    prefix = f"area:{area.area_id}:" if area is not None else None
    if prefix is None or not args.task_id.startswith(prefix):
        return ToolResult(
            content="Only child automations owned by the current Area can be run.",
            preview="Outside Area boundary",
            is_error=True,
        )
    if service is None:
        return ToolResult(content="Automation service unavailable.", preview="Unavailable", is_error=True)
    try:
        await service.run_now(args.task_id)
    except KeyError:
        return ToolResult(content="Area automation not found.", preview="Not found", is_error=True)
    except RuntimeError as exc:
        return ToolResult(content=f"Could not start Area automation: {exc}", preview="Unavailable", is_error=True)
    return ToolResult(content=f"Started Area automation {args.task_id}.", preview="Area automation started")


_AREA_PERMISSION = frozenset({AREA_PAGES_SERVICE})

area_page_read_tool = tool(
    display_name="AreaPageRead",
    description="Read the current Area's attached page. The path is fixed by the Area and cannot be overridden.",
    input_model=AreaPageReadInput,
    policy=ToolPolicy(action=ToolAction.READ, scope=ToolScope.INTERNAL, permissions=_AREA_PERMISSION),
    execute=area_page_read,
)

area_page_patch_tool = tool(
    display_name="AreaPagePatch",
    description="Replace one exact block in the current Area's attached page. Cannot edit any other page.",
    input_model=AreaPagePatchInput,
    policy=ToolPolicy(action=ToolAction.WRITE, scope=ToolScope.INTERNAL, permissions=_AREA_PERMISSION),
    execute=area_page_patch,
)

area_page_write_tool = tool(
    display_name="AreaPageWrite",
    description="Replace the Markdown body of the current Area's attached page while preserving frontmatter.",
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
