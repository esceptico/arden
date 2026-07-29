from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import Any

from pydantic import ValidationError

from arden.agent import ToolOutcomeStatus
from arden.tools.core.base import Tool, ToolResult
from arden.tools.core.context import ToolExecution
from arden.tools.core.types import ApprovalMode, ApprovalWaived


@dataclass(frozen=True)
class ToolCall:
    name: str
    tool: Tool
    execution: ToolExecution | None
    arguments: dict[str, Any]


ToolNext = Callable[[ToolCall], Awaitable[ToolResult]]
ToolMiddleware = Callable[[ToolCall, ToolNext], Awaitable[ToolResult]]


async def require_capabilities(call: ToolCall, next_call: ToolNext) -> ToolResult:
    """Enforce the same service boundary used to expose tool schemas."""

    permissions = call.tool.policy.permissions
    if not permissions:
        return await next_call(call)
    if call.execution is None or not permissions.issubset(call.execution.ctx.capabilities):
        return ToolResult.failure(
            code="permission_denied",
            message=f"Tool is unavailable in the current runtime: {call.name}.",
            preview="Tool unavailable",
            status=ToolOutcomeStatus.DENIED,
            recovery_action="Use a tool exposed in the current system prompt.",
        )
    return await next_call(call)


async def validate_arguments(call: ToolCall, next_call: ToolNext) -> ToolResult:
    if call.tool.input_model is None:
        return await next_call(call)

    try:
        validated = call.tool.input_model.model_validate(call.arguments, extra="forbid")
    except ValidationError as e:
        errors = "; ".join(
            f"{'.'.join(str(l) for l in err['loc'])}: {err['msg']}" for err in e.errors() if err.get("loc")
        )
        return ToolResult.failure(
            code="invalid_arguments",
            message=f"Invalid arguments: {errors}",
            preview="Validation error",
            recovery_action="Retry with arguments matching the tool schema.",
        )

    return await next_call(replace(call, arguments=validated.model_dump()))


async def request_approval(call: ToolCall, next_call: ToolNext) -> ToolResult:
    if call.tool.policy.approval_mode == ApprovalMode.NEVER:
        return await next_call(call)

    info = await call.tool.approval_info(call.execution, **call.arguments)
    if isinstance(info, ApprovalWaived):
        return await next_call(call)
    if info is None:
        return ToolResult.failure(
            code="approval_preview_unavailable",
            message=f"Could not prepare a safe preview for {call.tool.display_name or call.name}.",
            preview="Approval unavailable",
            recovery_action="Inspect the target state and retry; the action was not executed.",
        )

    rejection = await call.execution.request_approval(
        info.description,
        preview=info.preview,
        diff=info.diff,
    )
    if rejection is not None:
        return rejection.to_result()

    return await next_call(call)


DEFAULT_TOOL_MIDDLEWARE: tuple[ToolMiddleware, ...] = (
    validate_arguments,
    require_capabilities,
    request_approval,
)
