import logging
from dataclasses import dataclass

from pydantic import BaseModel, Field
from tenacity import before_sleep_log, retry, stop_after_attempt, wait_exponential

from ntrp.agent import ToolOutcomeStatus
from ntrp.logging import get_logger
from ntrp.notifiers.base import Notifier
from ntrp.tools.core import ToolResult, tool
from ntrp.tools.core.context import ToolExecution
from ntrp.tools.core.types import ApprovalInfo, ToolAction, ToolPolicy, ToolScope
from ntrp.utils import truncate

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


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=2, max=10),
    before_sleep=before_sleep_log(_logger, logging.WARNING),
    reraise=True,
)
async def _send_with_retry(notifier: Notifier, subject: str, body: str) -> None:
    await notifier.send(subject, body)


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
    failed: list[str] = []

    for notifier in resolved.targets:
        try:
            await _send_with_retry(notifier, args.subject, args.body)
            sent.append(notifier.channel)
        except Exception:
            _logger.exception("Notifier %s failed after retries", notifier.channel)
            failed.append(notifier.channel)

    if failed:
        if not sent:
            return ToolResult.failure(
                code="provider_error",
                message=f"Notification delivery failed for: {', '.join(failed)}.",
                preview="Delivery failed",
                retryable=True,
                recovery_action="Retry later or choose another configured notifier.",
            )
        return ToolResult.failure(
            code="partial_failure",
            message=f"Sent to: {', '.join(sent)}. Failed: {', '.join(failed)}.",
            preview=f"Partial ({len(sent)}/{len(sent) + len(failed)})",
            status=ToolOutcomeStatus.UNCERTAIN,
            retryable=True,
            recovery_action="Retry only the failed notifier channels.",
        )

    return ToolResult(
        content=f"Notification sent to: {', '.join(sent)}",
        preview=f"Sent ({len(sent)})",
    )


notify_tool = tool(
    display_name="Notify",
    description=NOTIFY_DESCRIPTION,
    input_model=NotifyInput,
    policy=ToolPolicy(
        action=ToolAction.WRITE,
        scope=ToolScope.EXTERNAL,
        requires_approval=True,
        permissions=frozenset({"notifiers"}),
    ),
    approval=approve_notify,
    execute=notify,
)
