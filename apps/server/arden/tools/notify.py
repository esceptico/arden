from dataclasses import dataclass

from pydantic import BaseModel, Field

from arden.agent import ToolOutcomeStatus
from arden.logging import get_logger
from arden.notifiers.base import Notifier
from arden.tools.core import ToolResult, tool
from arden.tools.core.context import ToolExecution
from arden.tools.core.types import ApprovalInfo, ToolAction, ToolPolicy, ToolScope
from arden.utils import truncate

_logger = get_logger(__name__)
_SERVICE_NAME = "notifiers"

NOTIFY_DESCRIPTION = (
    "Send a notification to the user via their configured channels. "
    "Use this when the user asked to be notified, told, or written to about something."
)


class NotifyInput(BaseModel):
    subject: str = Field(description="Short notification subject/title")
    body: str = Field(description="Notification body — concise, informative")
    names: list[str] | None = Field(
        default=None,
        max_length=50,
        description="Notifier names to use, e.g. ['work-telegram'] (omit to send to all)",
    )


@dataclass
class _ResolvedNotifiers:
    targets: list[Notifier]
    unknown: list[str]
    available: list[str]


async def approve_notify(_execution: ToolExecution, args: NotifyInput) -> ApprovalInfo:
    targets = ", ".join(args.names) if args.names else "all configured channels"
    return ApprovalInfo(
        description=f"Notify via {targets}: {args.subject}",
        preview=truncate(args.body, 1_500),
        diff=None,
    )


def _resolve_notifiers(execution: ToolExecution, names: list[str] | None = None) -> _ResolvedNotifiers:
    all_notifiers: dict[str, Notifier] = execution.ctx.services[_SERVICE_NAME].notifiers
    available = list(all_notifiers)
    if not names:
        return _ResolvedNotifiers(targets=list(all_notifiers.values()), unknown=[], available=available)
    targets, unknown = [], []
    for name in names:
        if (notifier := all_notifiers.get(name)) is not None:
            targets.append(notifier)
        else:
            unknown.append(name)
    return _ResolvedNotifiers(targets=targets, unknown=unknown, available=available)


async def notify(execution: ToolExecution, args: NotifyInput) -> ToolResult:
    resolved = _resolve_notifiers(execution, args.names)

    if resolved.unknown:
        msg = f"Unknown notifier(s): {', '.join(resolved.unknown)}. Available: {', '.join(resolved.available)}"
        return ToolResult.failure(
            code="invalid_ref",
            message=msg,
            preview="Unknown notifier",
            recovery_action="Retry with one of the available notifier names.",
        )

    if not resolved.targets:
        return ToolResult.failure(
            code="not_found",
            message="No notifiers configured.",
            preview="No notifiers",
            recovery_action="Configure a notification channel before retrying.",
        )

    sent: list[str] = []
    uncertain: list[str] = []

    for notifier in resolved.targets:
        try:
            await notifier.send(args.subject, args.body)
            sent.append(notifier.channel)
        except Exception:
            _logger.exception("Notifier %s delivery outcome is uncertain", notifier.channel)
            uncertain.append(notifier.channel)

    if uncertain:
        if not sent:
            return ToolResult.failure(
                code="mutation_uncertain",
                message=f"Notification delivery outcome is unknown for: {', '.join(uncertain)}.",
                preview="Delivery uncertain",
                status=ToolOutcomeStatus.UNCERTAIN,
                retryable=False,
                recovery_action="Inspect the target channels before sending another notification.",
            )
        return ToolResult.failure(
            code="partial_failure",
            message=f"Sent to: {', '.join(sent)}. Outcome unknown for: {', '.join(uncertain)}.",
            preview=f"Partial ({len(sent)}/{len(sent) + len(uncertain)})",
            status=ToolOutcomeStatus.UNCERTAIN,
            retryable=False,
            recovery_action="Inspect the uncertain channels before sending another notification.",
        )

    return ToolResult(
        content=f"Notification sent to: {', '.join(sent)}",
        preview=f"Sent ({len(sent)})",
    )


notify_tool = tool(
    display_name="Notify",
    display_description="Send a notification to the user.",
    description=NOTIFY_DESCRIPTION,
    input_model=NotifyInput,
    policy=ToolPolicy(
        action=ToolAction.WRITE,
        scope=ToolScope.EXTERNAL,
        requires_approval=True,
        permissions=frozenset({"notifiers"}),
        deferred=True,
        destructive=False,
        open_world=True,
        idempotent=False,
    ),
    approval=approve_notify,
    execute=notify,
)
