"""The only mutation surface available to the Wiki Maintenance agent."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

from arden.constants import BUILTIN_WIKI_MAINTENANCE_ID
from arden.tools.core import ToolResult, tool
from arden.tools.core.context import ToolExecution
from arden.tools.core.types import ToolAction, ToolPolicy, ToolScope
from arden.wiki.constants import WIKI_MAINTENANCE_REVIEW_TOOL_NAME
from arden.wiki.maintenance.agent import WikiMaintenanceReviewService, WikiMaintenanceReviewState
from arden.wiki.maintenance.runner import WikiMaintenanceDecision, WikiMaintenanceError
from arden.wiki.maintenance.store import WikiMaintenanceReviewConflictError

WIKI_MAINTENANCE_SERVICE = "wiki_maintenance"


class WikiMaintenanceReviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["next", "decide"]
    decision: WikiMaintenanceDecision | None = None

    @model_validator(mode="after")
    def validate_action(self):
        if self.action == "decide" and self.decision is None:
            raise ValueError("decide requires a decision")
        if self.action == "next" and self.decision is not None:
            raise ValueError("next must not include a decision")
        return self


def _service(execution: ToolExecution) -> WikiMaintenanceReviewService | ToolResult:
    if execution.ctx.run.automation_id != BUILTIN_WIKI_MAINTENANCE_ID:
        return ToolResult.failure(
            code="forbidden",
            message="Wiki maintenance review is reserved for Wiki Maintenance.",
            preview="Not available",
        )
    service = execution.ctx.services.get(WIKI_MAINTENANCE_SERVICE)
    if isinstance(service, WikiMaintenanceReviewService):
        return service
    return ToolResult.failure(
        code="not_running",
        message="Wiki Maintenance is not currently running.",
        preview="Maintenance unavailable",
    )


def _result(state: WikiMaintenanceReviewState) -> ToolResult:
    if state.result is not None:
        result = state.result
        status = "blocked for a durable user decision" if result.blocked else "completed"
        return ToolResult(
            content=(
                f"Wiki Maintenance {status}. Reviewed {result.reviewed_commits} commit(s); "
                f"updated {result.updated_pages} page(s)."
            ),
            preview="Maintenance complete",
            data={"completed": True, "blocked": result.blocked, "reload_required": result.reload_required},
        )
    report = state.report
    if report is None:
        raise RuntimeError("wiki maintenance review has neither report nor result")
    return ToolResult(
        content=(
            f"Review this report, then call {WIKI_MAINTENANCE_REVIEW_TOOL_NAME} "
            f"with action='decide'.\n\n{report.markdown}"
        ),
        preview="Maintenance decision required",
        data={"completed": False},
    )


async def wiki_maintenance_review(execution: ToolExecution, args: WikiMaintenanceReviewInput) -> ToolResult:
    service = _service(execution)
    if isinstance(service, ToolResult):
        return service
    try:
        state = await service.next() if args.action == "next" else await service.decide(args.decision)
        return _result(state)
    except (WikiMaintenanceError, WikiMaintenanceReviewConflictError) as exc:
        return ToolResult.failure(
            code="invalid_maintenance_decision",
            message=str(exc),
            preview="Decision rejected",
            recovery_action="Correct the decision for the current report and retry.",
        )


wiki_maintenance_review_tool = tool(
    display_name="Review Wiki Maintenance",
    display_description="Advance one constrained Wiki Maintenance review.",
    description=(
        "Start with action='next'. Review exactly one prepared, version-pinned wiki change report. "
        "Submit one decision. Duplicate pages may be proposed only as outcome='needs_review' with a concern and a nested "
        "merge object containing canonical_page_token and loser_page_token; the backend applies it only after durable "
        "user acceptance. Correct any rejected "
        "decision and continue until completion. "
        "This is the only mutation surface for Wiki Maintenance."
    ),
    input_model=WikiMaintenanceReviewInput,
    policy=ToolPolicy(
        action=ToolAction.WRITE,
        scope=ToolScope.INTERNAL,
        permissions=frozenset({WIKI_MAINTENANCE_SERVICE}),
    ),
    execute=wiki_maintenance_review,
)
