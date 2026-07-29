"""Approved agent access to producer-owned wiki provisioning."""

from typing import Literal, Protocol, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator

from arden.revisions.errors import RevisionConflictError
from arden.services.wiki_producer_models import (
    WikiProducerPartialProvisionError,
    WikiProducerProvision,
    WikiProducerProvisionConflictError,
    WikiProducerRequest,
)
from arden.tools.core import ToolResult, tool
from arden.tools.core.context import ToolExecution
from arden.tools.core.types import ApprovalInfo, ToolAction, ToolPolicy, ToolScope
from arden.wiki.constants import PUBLISH_WIKI_GENERATED_TOOL_NAME, READ_WIKI_PAGE_TOOL_NAME

_PERMISSIONS = frozenset({"automation", "wiki"})


class WikiProducerProvisioning(Protocol):
    async def provision(self, request: WikiProducerRequest) -> WikiProducerProvision: ...


class ProvisionWikiProducerInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page_id: str = Field(min_length=1, max_length=512)
    path: str = Field(min_length=1, max_length=4096)
    title: str = Field(min_length=1, max_length=4096)
    aliases: list[str] = Field(default_factory=list, max_length=100)
    automation_name: str = Field(min_length=1, max_length=220)
    prompt: str = Field(min_length=1)
    model: str | None = None
    trigger_type: Literal["time", "event", "message"]
    at: str | None = None
    days: str | None = None
    every: str | None = None
    start: str | None = None
    end: str | None = None
    event_type: str | None = None
    lead_minutes: int | str | None = None
    channels: list[str] | None = Field(default=None, max_length=100)
    from_user: str | None = None
    contains: list[str] | None = Field(default=None, max_length=100)
    source_tool_scope: list[str] = Field(default_factory=list, max_length=200)
    expected_head: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @field_validator("source_tool_scope")
    @classmethod
    def validate_source_tool_scope(cls, values: list[str]) -> list[str]:
        normalized = sorted({value.strip() for value in values})
        if not all(normalized):
            raise ValueError("source_tool_scope entries must be nonempty")
        wildcard = [value for value in normalized if value == "*" or value.endswith("*")]
        if wildcard:
            raise ValueError("producer source_tool_scope requires exact tool names")
        return normalized


def _request(args: ProvisionWikiProducerInput) -> WikiProducerRequest:
    return WikiProducerRequest(
        page_id=args.page_id,
        path=args.path,
        title=args.title,
        aliases=tuple(args.aliases),
        automation_name=args.automation_name,
        prompt=args.prompt,
        model=args.model,
        trigger_type=args.trigger_type,
        at=args.at,
        days=args.days,
        every=args.every,
        start=args.start,
        end=args.end,
        event_type=args.event_type,
        lead_minutes=args.lead_minutes,
        channels=tuple(args.channels) if args.channels is not None else None,
        from_user=args.from_user,
        contains=tuple(args.contains) if args.contains is not None else None,
        source_tool_scope=tuple(args.source_tool_scope),
        expected_head=args.expected_head,
    )


def _tool_scope(args: ProvisionWikiProducerInput) -> list[str]:
    return sorted({*args.source_tool_scope, READ_WIKI_PAGE_TOOL_NAME, PUBLISH_WIKI_GENERATED_TOOL_NAME})


def _display(value: object | None) -> str:
    return "none" if value is None or value == "" else str(value)


def _trigger_preview(args: ProvisionWikiProducerInput) -> str:
    if args.trigger_type == "time":
        return (
            f"time · at={_display(args.at)} · days={_display(args.days)} · every={_display(args.every)} · "
            f"start={_display(args.start)} · end={_display(args.end)}"
        )
    if args.trigger_type == "event":
        return f"event · event_type={_display(args.event_type)} · lead_minutes={_display(args.lead_minutes)}"
    return (
        f"message · channels={', '.join(args.channels or []) or 'none'} · "
        f"from_user={args.from_user or 'none'} · contains={', '.join(args.contains or []) or 'none'}"
    )


async def approve_provision_wiki_producer(
    _execution: ToolExecution,
    args: ProvisionWikiProducerInput,
) -> ApprovalInfo:
    preview = "\n".join(
        (
            f"Page: {args.page_id} ({args.path})",
            f"Title: {args.title}",
            f"Aliases: {', '.join(args.aliases) or 'none'}",
            f"Automation: wiki-producer:{args.page_id} — {args.automation_name}",
            f"Trigger: {_trigger_preview(args)}",
            f"Model: {args.model or 'default'}",
            "Auto-approve: true",
            f"Exact tools: {', '.join(_tool_scope(args))}",
            "",
            "Prompt:",
            args.prompt,
        )
    )
    return ApprovalInfo(
        description=f"Provision wiki producer for {args.path}",
        preview=preview,
        diff=f"Expected wiki head: {args.expected_head}",
    )


async def provision_wiki_producer(execution: ToolExecution, args: ProvisionWikiProducerInput) -> ToolResult:
    if execution.ctx.run.automation_id is not None:
        return ToolResult.failure(
            code="interactive_required",
            message="Producer provisioning requires one user-approved interactive tool call.",
            preview="Interactive approval required",
        )
    if "wiki_producer" not in execution.ctx.services:
        return ToolResult.failure(
            code="not_configured",
            message="Wiki producer provisioning requires both wiki and automation services.",
            preview="Producer provisioning unavailable",
            recovery_action="Enable canonical memory and automations before retrying.",
        )
    provisioner = cast("WikiProducerProvisioning", execution.ctx.services["wiki_producer"])
    try:
        result = await provisioner.provision(_request(args))
    except RevisionConflictError as exc:
        return ToolResult.failure(
            code="revision_conflict",
            message=str(exc),
            preview="Wiki page changed",
            retryable=True,
            recovery_action="Read the current wiki head and retry the same producer contract.",
        )
    except WikiProducerPartialProvisionError as exc:
        return ToolResult.failure(
            code="partial_provision",
            message=str(exc),
            preview="Producer page created; automation pending",
            retryable=True,
            recovery_action="Retry the exact same producer contract with the current wiki head.",
            data={"page_id": exc.page_id, "automation_id": exc.automation_id},
        )
    except WikiProducerProvisionConflictError as exc:
        return ToolResult.failure(
            code="producer_conflict",
            message=str(exc),
            preview="Conflicting producer contract",
        )
    except ValueError as exc:
        return ToolResult.failure(code="invalid_arguments", message=str(exc), preview="Invalid producer contract")

    return ToolResult(
        content=f"Provisioned wiki producer {result.automation_id} for {result.path}.",
        preview="Wiki producer ready",
        data={
            "page": {
                "page_id": result.page_id,
                "path": result.path,
                "title": result.title,
                "aliases": list(result.aliases),
                "version": result.page_version,
                "head": result.head,
            },
            "automation": {
                "task_id": result.automation_id,
                "name": result.automation_name,
                "model": result.model,
                "auto_approve": result.auto_approve,
                "tool_scope": list(result.tool_scope),
            },
            "channel_id": result.channel_id,
            "recovery": {
                "page_created": result.page_created,
                "channel_created": result.channel_created,
                "automation_created": result.automation_created,
            },
        },
    )


provision_wiki_producer_tool = tool(
    display_name="ProvisionWikiProducer",
    display_description="Create a producer-owned wiki page and its automation.",
    description=(
        "Create one new feeds/ or insights/ page and its owning scheduled automation as one recoverable operation. "
        "Use this before a producer's first run; existing producer publication remains update-only."
    ),
    input_model=ProvisionWikiProducerInput,
    policy=ToolPolicy(
        action=ToolAction.WRITE,
        scope=ToolScope.INTERNAL,
        requires_approval=True,
        permissions=_PERMISSIONS,
        idempotent=True,
    ),
    approval=approve_provision_wiki_producer,
    execute=provision_wiki_producer,
)
