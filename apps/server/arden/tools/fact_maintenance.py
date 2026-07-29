"""The only mutation surface available to the Memory Maintenance agent."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

from arden.constants import BUILTIN_MEMORY_CONSOLIDATE_ID
from arden.memory.facts.maintenance import FACT_MAINTENANCE_REVIEW_TOOL_NAME
from arden.memory.facts.maintenance.agent import FactMaintenanceReviewService, FactMaintenanceReviewState
from arden.memory.facts.maintenance.runner import FactMaintenanceDecision
from arden.memory.facts.maintenance.store import FactMaintenanceError
from arden.tools.core import ToolResult, tool
from arden.tools.core.context import ToolExecution
from arden.tools.core.types import ToolAction, ToolPolicy, ToolScope

FACT_MAINTENANCE_SERVICE = "fact_maintenance"


class FactMaintenanceReviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["next", "decide"]
    decision: FactMaintenanceDecision | None = None

    @model_validator(mode="after")
    def validate_action(self):
        if self.action == "decide" and self.decision is None:
            raise ValueError("decide requires a decision")
        if self.action == "next" and self.decision is not None:
            raise ValueError("next must not include a decision")
        return self


def _service(execution: ToolExecution) -> FactMaintenanceReviewService | ToolResult:
    if execution.ctx.run.automation_id != BUILTIN_MEMORY_CONSOLIDATE_ID:
        return ToolResult.failure(
            code="forbidden",
            message="Fact maintenance review is reserved for Memory Maintenance.",
            preview="Not available",
        )
    service = execution.ctx.services.get(FACT_MAINTENANCE_SERVICE)
    if isinstance(service, FactMaintenanceReviewService):
        return service
    return ToolResult.failure(
        code="not_running",
        message="Memory Maintenance is not currently running.",
        preview="Maintenance unavailable",
    )


def _result(state: FactMaintenanceReviewState) -> ToolResult:
    if state.result is not None:
        result = state.result
        return ToolResult(
            content=(
                "Maintenance completed. "
                f"Reviewed {result.reviewed_clusters}; amended {result.amended_facts}; merged {result.merged_facts}."
            ),
            preview="Maintenance complete",
            data={"completed": True},
        )
    cluster = state.cluster
    if cluster is None:
        raise RuntimeError("maintenance review has neither cluster nor result")
    return ToolResult(
        content=(
            f"Review this cluster, then call {FACT_MAINTENANCE_REVIEW_TOOL_NAME} with action='decide'.\n\n"
            f"{cluster.markdown}"
        ),
        preview="Maintenance decision required",
        data={"completed": False, "target_token": cluster.target_token},
    )


async def fact_maintenance_review(execution: ToolExecution, args: FactMaintenanceReviewInput) -> ToolResult:
    service = _service(execution)
    if isinstance(service, ToolResult):
        return service
    try:
        state = await service.next() if args.action == "next" else await service.decide(args.decision)
        return _result(state)
    except FactMaintenanceError as exc:
        return ToolResult.failure(
            code="invalid_maintenance_decision",
            message=str(exc),
            preview="Decision rejected",
            recovery_action="Correct the decision for the current cluster and retry.",
        )


fact_maintenance_review_tool = tool(
    display_name="Review Fact Maintenance",
    display_description="Advance one constrained Memory Maintenance review.",
    description=(
        "Start with action='next'. Review exactly one prepared, version-pinned cluster. "
        "Submit exactly one decision, correct any rejected decision, and continue until completion. "
        "This is the only mutation surface for Memory Maintenance."
    ),
    input_model=FactMaintenanceReviewInput,
    policy=ToolPolicy(
        action=ToolAction.WRITE,
        scope=ToolScope.INTERNAL,
        permissions=frozenset({FACT_MAINTENANCE_SERVICE}),
    ),
    execute=fact_maintenance_review,
)
