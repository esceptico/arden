"""The only mutation surface available to the Wiki Maintenance agent."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

from arden.constants import BUILTIN_WIKI_MAINTENANCE_ID
from arden.tools.core import ToolResult, tool
from arden.tools.core.context import ToolExecution
from arden.tools.core.types import ToolAction, ToolPolicy, ToolScope
from arden.wiki.constants import WIKI_MAINTENANCE_REVIEW_TOOL_NAME
from arden.wiki.exceptions import WikiAmbiguityError, WikiValidationError
from arden.wiki.maintenance.agent import WikiMaintenanceReviewService, WikiMaintenanceReviewState
from arden.wiki.maintenance.runner import WikiMaintenanceDecision, WikiMaintenanceError

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
            recovery_action="Use this tool only inside the built-in Wiki Maintenance run.",
        )
    service = execution.ctx.services.get(WIKI_MAINTENANCE_SERVICE)
    if isinstance(service, WikiMaintenanceReviewService):
        return service
    return ToolResult.failure(
        code="not_running",
        message="Wiki Maintenance is not currently running.",
        preview="Maintenance unavailable",
        recovery_action="Start or retry the Wiki Maintenance automation before calling this tool.",
    )


def _result(state: WikiMaintenanceReviewState) -> ToolResult:
    if state.failure is not None:
        return ToolResult.failure(
            code=state.failure.code,
            message=state.failure.message,
            preview="Maintenance blocked",
            recovery_action=state.failure.recovery_action,
        )
    if state.result is not None:
        result = state.result
        skipped = (
            f" Skipped {result.skipped_oversized_reports} oversized report(s) without changes."
            if result.skipped_oversized_reports
            else ""
        )
        return ToolResult(
            content=(
                f"Wiki Maintenance completed. Reviewed {result.reviewed_commits} commit(s); "
                f"updated {result.updated_pages} page(s).{skipped}"
            ),
            preview="Maintenance complete",
            data={
                "completed": True,
                "reload_required": result.reload_required,
                "skipped_oversized_reports": result.skipped_oversized_reports,
            },
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
    except WikiMaintenanceError as exc:
        return ToolResult.failure(
            code="invalid_maintenance_decision",
            message=str(exc),
            preview="Decision rejected",
            recovery_action="Correct the decision for the current report and retry.",
        )
    except WikiValidationError as exc:
        ambiguous = isinstance(exc, WikiAmbiguityError)
        return ToolResult.failure(
            code="wiki_identity_ambiguous" if ambiguous else "wiki_validation_conflict",
            message=str(exc),
            preview="Maintenance blocked",
            recovery_action=(
                "Stop this review and resolve the ambiguous page identity before restarting Wiki Maintenance."
                if ambiguous
                else "Stop this review and repair the reported wiki invariant before restarting Wiki Maintenance."
            ),
        )


wiki_maintenance_review_tool = tool(
    display_name="Review Wiki Maintenance",
    display_description="Advance one constrained Wiki Maintenance review.",
    description=(
        "Start with action='next'. Review exactly one prepared wiki change report. "
        "Submit one decision. Choose no_change when the evidence is ambiguous; "
        "this workflow never combines or archives pages. Correct any rejected decision and continue until completion. "
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
