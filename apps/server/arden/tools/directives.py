import asyncio
import difflib
import json

from pydantic import BaseModel, ConfigDict, Field

from arden.agent.types.tools import ToolEffect, ToolOutcome, ToolOutcomeStatus
from arden.constants import OFFLOAD_THRESHOLD
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
_DIRECTIVES_OBSERVATION_ID = "directives"

DESCRIPTION = """Set custom directives that shape your behavior.

These directives persist across conversations and are injected into your system prompt.
Use this when the user asks you to change how you behave — style, tone, things to do or avoid.

Pass the FULL desired directives — this replaces any previous content.
Read current directives first (if any), then write the updated version."""


class DirectivesSetInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    directives: str = Field(description="The full custom directives text.")


class DirectivesGetInput(EmptyInput):
    pass


async def directives_get(execution: ToolExecution, args: DirectivesGetInput) -> ToolResult:
    content, revision, size = await asyncio.to_thread(_load_directives_snapshot)
    result = ToolResult(
        content=content or "",
        preview="Directives loaded" if content else "No directives",
        data={"size": size},
    )
    execution.ctx.run.observe_resource(
        _DIRECTIVES_OBSERVATION_ID,
        version=None if revision == "absent" else revision,
        container_version=None,
        content_read=len(result.serialized_payload().encode("utf-8")) <= OFFLOAD_THRESHOLD,
    )
    return result


async def approve_directives_set(execution: ToolExecution, args: DirectivesSetInput) -> ApprovalInfo:
    current = (await asyncio.to_thread(load_directives)) or ""
    diff = _diff(current, args.directives)
    return ApprovalInfo(
        description="Update directives",
        preview=None,
        diff=diff,
    )


async def directives_set(execution: ToolExecution, args: DirectivesSetInput) -> ToolResult:
    try:
        observation = execution.ctx.run.resource_observation(_DIRECTIVES_OBSERVATION_ID)
        revision, unchanged = await asyncio.to_thread(
            _set_directives_snapshot,
            args.directives,
            None if observation is None else observation.version,
            False if observation is None else observation.content_read,
        )
    except _FreshReadRequired:
        return ToolResult.failure(
            code="fresh_read_required",
            message="Read the current directives before replacing them.",
            preview="Read directives first",
            recovery_action="Call directives_get, then retry.",
        )
    except RevisionConflict:
        return ToolResult.failure(
            code="write_conflict",
            message="Directives changed since they were read.",
            preview="Write conflict",
            recovery_action="Call directives_get, recompute the update, and retry.",
        )
    execution.ctx.run.observe_resource(
        _DIRECTIVES_OBSERVATION_ID,
        version=revision.sha256,
        container_version=None,
        content_read=True,
    )
    if unchanged:
        return ToolResult(
            content="Directives already have the requested content.",
            preview="Directives unchanged",
            data={"size": revision.size},
        )
    outcome = ToolOutcome(
        status=ToolOutcomeStatus.SUCCEEDED,
        effect=ToolEffect(
            operation="replace",
            target=str(DIRECTIVES_PATH),
        ),
    )
    if not args.directives.strip():
        return ToolResult(
            content="Directives cleared.",
            preview="Cleared",
            data={"size": revision.size},
            outcome=outcome,
        )
    return ToolResult(
        content=f"Directives updated:\n{args.directives}",
        preview="Directives set",
        data={"size": revision.size},
        outcome=outcome,
    )


directives_get_tool = tool(
    display_name="Get Directives",
    display_description="Read persistent behavior directives.",
    description="Read the current persistent behavior directives.",
    input_model=DirectivesGetInput,
    policy=ToolPolicy(action=ToolAction.READ, scope=ToolScope.INTERNAL, deferred=True),
    execute=directives_get,
)


directives_set_tool = tool(
    display_name="Set Directives",
    display_description="Replace persistent behavior directives.",
    description=DESCRIPTION,
    input_model=DirectivesSetInput,
    policy=ToolPolicy(
        action=ToolAction.WRITE,
        scope=ToolScope.INTERNAL,
        requires_approval=True,
        deferred=True,
        destructive=True,
        open_world=False,
        idempotent=True,
    ),
    approval=approve_directives_set,
    execute=directives_set,
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


class _FreshReadRequired(Exception):
    pass


def _set_directives_snapshot(
    directives: str,
    observed_revision: str | None,
    content_read: bool,
):
    content = directives.strip()
    current, revision, _size = _load_directives_snapshot()
    if revision != "absent" and current == (content or None):
        return read_file_snapshot(DIRECTIVES_PATH)[1], True
    if revision != "absent" and (not content_read or observed_revision is None):
        raise _FreshReadRequired
    expected = "absent" if revision == "absent" else observed_revision
    return atomic_compare_and_swap(DIRECTIVES_PATH, json.dumps({"content": content}), expected), False


def save_directives(content: str, expected_revision: str | None = None):
    content = content.strip()
    expected = expected_revision or revision_or_absent(DIRECTIVES_PATH)
    return atomic_compare_and_swap(DIRECTIVES_PATH, json.dumps({"content": content}), expected)
