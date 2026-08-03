"""The only mutation surface available to the Memory Capture agent."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel

from arden.constants import BUILTIN_MEMORY_CAPTURE_ID
from arden.memory.facts.capture import FACT_CAPTURE_REVIEW_TOOL_NAME
from arden.memory.facts.capture.runner import (
    FactCapture,
    FactCaptureBatch,
    FactCaptureDecision,
    FactCaptureError,
    FactCaptureResult,
)
from arden.tools.core import ToolResult, tool
from arden.tools.core.context import ToolExecution
from arden.tools.core.types import ToolAction, ToolPolicy, ToolScope

FACT_CAPTURE_SERVICE = "fact_capture"


class FactCaptureReviewNext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["next"]


class FactCaptureReviewDecide(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["decide"]
    decision: FactCaptureDecision


FactCaptureReviewAction = Annotated[
    FactCaptureReviewNext | FactCaptureReviewDecide,
    Field(discriminator="action"),
]


class FactCaptureReviewInput(RootModel[FactCaptureReviewAction]):
    """The complete, mutually exclusive review-tool contract."""


def _service(execution: ToolExecution) -> FactCapture | ToolResult:
    if execution.ctx.run.automation_id != BUILTIN_MEMORY_CAPTURE_ID:
        return ToolResult.failure(
            code="forbidden",
            message="Fact capture review is reserved for Memory Capture.",
            preview="Not available",
            recovery_action="Use this tool only inside the built-in Memory Capture run.",
        )
    service = execution.ctx.services.get(FACT_CAPTURE_SERVICE)
    if isinstance(service, FactCapture):
        return service
    return ToolResult.failure(
        code="not_running",
        message="Memory Capture is not currently running.",
        preview="Capture unavailable",
        recovery_action="Start or retry the Memory Capture automation before calling this tool.",
    )


def _result(state: FactCaptureBatch | FactCaptureResult) -> ToolResult:
    if isinstance(state, FactCaptureResult):
        return ToolResult(
            content=f"Capture completed. {state.summary}.",
            preview="Capture complete",
            data={"completed": True},
        )
    return ToolResult(
        content=(
            f"Review this transcript batch, then call {FACT_CAPTURE_REVIEW_TOOL_NAME} with action='decide'.\n\n"
            f"{state.markdown}"
        ),
        preview="Capture decision required",
        data={"completed": False, "session_id": state.session_id},
    )


async def fact_capture_review(execution: ToolExecution, args: FactCaptureReviewInput) -> ToolResult:
    service = _service(execution)
    if isinstance(service, ToolResult):
        return service
    try:
        match args.root:
            case FactCaptureReviewNext():
                state = await service.next()
            case FactCaptureReviewDecide(decision=decision):
                state = await service.decide(decision)
        return _result(state)
    except FactCaptureError as exc:
        return ToolResult.failure(
            code="invalid_capture_decision",
            message=str(exc),
            preview="Decision rejected",
            recovery_action="Correct the decision for the current batch and retry.",
        )


fact_capture_review_tool = tool(
    display_name="Review Fact Capture",
    display_description="Advance one constrained Memory Capture review.",
    description=(
        "Start with action='next'. Review exactly one quoted transcript batch. "
        "Submit exactly one decision, correct any rejected decision, and continue until completion. "
        "This is the only mutation surface for Memory Capture."
    ),
    input_model=FactCaptureReviewInput,
    policy=ToolPolicy(
        action=ToolAction.WRITE,
        scope=ToolScope.INTERNAL,
        permissions=frozenset({FACT_CAPTURE_SERVICE}),
    ),
    execute=fact_capture_review,
)
