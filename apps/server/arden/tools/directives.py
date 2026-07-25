import asyncio
import difflib
import json

from pydantic import BaseModel, Field

from arden.agent.types.tools import ToolEffect, ToolOutcome, ToolOutcomeStatus
from arden.settings import ARDEN_DIR
from arden.tools.core import EmptyInput, ToolResult, tool
from arden.tools.core.context import ToolExecution
from arden.tools.core.file_mutation import (
    RevisionConflict,
    atomic_compare_and_swap,
    read_file_snapshot,
    revision_or_absent,
)
from arden.tools.core.types import ApprovalInfo, ToolAction, ToolPolicy, ToolScope

DIRECTIVES_PATH = ARDEN_DIR / "directives.json"

DESCRIPTION = """Set custom directives that shape your behavior.

These directives persist across conversations and are injected into your system prompt.
Use this when the user asks you to change how you behave — style, tone, things to do or avoid.

Pass the FULL desired directives — this replaces any previous content.
Read current directives first (if any), then write the updated version."""


class SetDirectivesInput(BaseModel):
    directives: str = Field(description="The full custom directives text.")
    expected_sha256: str = Field(
        description="SHA-256 returned by get_directives, or the literal 'absent' when none exist."
    )


class GetDirectivesInput(EmptyInput):
    pass


async def get_directives(execution: ToolExecution, args: GetDirectivesInput) -> ToolResult:
    content, revision, size = await asyncio.to_thread(_load_directives_snapshot)
    return ToolResult(
        content=content or "",
        preview="Directives loaded" if content else "No directives",
        data={"sha256": revision, "size": size},
    )


async def approve_set_directives(execution: ToolExecution, args: SetDirectivesInput) -> ApprovalInfo:
    current = (await asyncio.to_thread(load_directives)) or ""
    diff = _diff(current, args.directives)
    return ApprovalInfo(
        description="Update directives",
        preview=None,
        diff=f"{diff.rstrip()}\n\nExpected SHA-256: {args.expected_sha256}",
    )


async def set_directives(execution: ToolExecution, args: SetDirectivesInput) -> ToolResult:
    try:
        revision = await asyncio.to_thread(save_directives, args.directives, args.expected_sha256)
    except RevisionConflict as conflict:
        return ToolResult.failure(
            code="write_conflict",
            message=(
                "Directives changed since they were read. "
                f"Expected {conflict.expected}, observed {conflict.observed}. Read them again before writing."
            ),
            preview="Write conflict",
            recovery_action="Call get_directives and retry with its new sha256.",
        )
    outcome = ToolOutcome(
        status=ToolOutcomeStatus.SUCCEEDED,
        effect=ToolEffect(
            operation="replace",
            target=str(DIRECTIVES_PATH),
            before_ref=args.expected_sha256,
            after_ref=revision.sha256,
        ),
    )
    if not args.directives.strip():
        return ToolResult(
            content="Directives cleared.",
            preview="Cleared",
            data={"sha256": revision.sha256, "size": revision.size},
            outcome=outcome,
        )
    return ToolResult(
        content=f"Directives updated:\n{args.directives}",
        preview="Directives set",
        data={"sha256": revision.sha256, "size": revision.size},
        outcome=outcome,
    )


get_directives_tool = tool(
    display_name="Get Directives",
    display_description="Read persistent behavior directives.",
    description="Read the current persistent behavior directives and their revision before replacing them.",
    input_model=GetDirectivesInput,
    policy=ToolPolicy(action=ToolAction.READ, scope=ToolScope.INTERNAL),
    execute=get_directives,
)


set_directives_tool = tool(
    display_name="Set Directives",
    display_description="Replace persistent behavior directives.",
    description=DESCRIPTION,
    input_model=SetDirectivesInput,
    policy=ToolPolicy(action=ToolAction.WRITE, scope=ToolScope.INTERNAL, requires_approval=True),
    approval=approve_set_directives,
    execute=set_directives,
)


def _diff(old: str, new: str) -> str:
    old_lines = old.splitlines(keepends=True)
    new_lines = new.splitlines(keepends=True)
    diff = difflib.unified_diff(old_lines, new_lines, fromfile="directives", tofile="directives", lineterm="")
    return "\n".join(diff)


def load_directives() -> str | None:
    try:
        return _load_directives_snapshot()[0]
    except OSError:
        return None


def _load_directives_snapshot() -> tuple[str | None, str, int]:
    try:
        raw, revision = read_file_snapshot(DIRECTIVES_PATH)
    except FileNotFoundError:
        return None, "absent", 0
    try:
        data = json.loads(raw)
        text = data.get("content", "").strip()
    except (json.JSONDecodeError, AttributeError):
        text = ""
    return text or None, revision.sha256, revision.size


def save_directives(content: str, expected_sha256: str | None = None):
    content = content.strip()
    expected = expected_sha256 or revision_or_absent(DIRECTIVES_PATH)
    return atomic_compare_and_swap(DIRECTIVES_PATH, json.dumps({"content": content}), expected)
