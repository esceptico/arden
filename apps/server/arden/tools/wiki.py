"""Bounded agent access to the managed wiki."""

import asyncio
import json
from hashlib import sha256
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from arden.agent.types.tools import ToolSourceRef
from arden.revisions.errors import RevisionConflictError
from arden.tools.core import ToolResult, tool
from arden.tools.core.context import ToolExecution
from arden.tools.core.formatting import format_lines_with_pagination
from arden.tools.core.types import ApprovalInfo, ToolAction, ToolPolicy, ToolScope
from arden.wiki.models import GeneratedPageTarget, LinkReference, WikiPageRecord
from arden.wiki.pages import extract_generated_region
from arden.wiki.service import (
    GeneratedRegionConflictError,
    WikiAmbiguityError,
    WikiService,
    WikiValidationError,
)

WIKI_SERVICE = "wiki"
_MAX_PAGE_LINES = 4_000
_MAX_CONTENT_CHARS = 40_000
_MAX_LINKS = 500
_MAX_LINK_DATA_BYTES = 40_000
_MAX_LINK_FIELD_CHARS = 2_000
_MAX_LINK_ID_CHARS = 512
_MAX_LINK_CANDIDATES = 20
_CONTENT_TRUNCATION = "\n[truncated at 40000 characters]"
_WIKI_PERMISSION = frozenset({WIKI_SERVICE})


class WikiPageSelector(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page_id: str | None = Field(default=None, min_length=1, max_length=512)
    path: str | None = Field(default=None, min_length=1, max_length=4096)
    title: str | None = Field(default=None, min_length=1, max_length=4096)
    alias: str | None = Field(default=None, min_length=1, max_length=4096)

    @model_validator(mode="after")
    def _require_one_exact_selector(self) -> Self:
        if sum(value is not None for value in (self.page_id, self.path, self.title, self.alias)) != 1:
            raise ValueError("provide exactly one of page_id, path, title, or alias")
        return self


class ReadWikiPageInput(WikiPageSelector):
    offset: int = Field(default=1, ge=1)
    limit: int = Field(default=500, ge=1, le=_MAX_PAGE_LINES)


class WikiLinksInput(WikiPageSelector):
    limit: int = Field(default=100, ge=1, le=_MAX_LINKS)


class PublishWikiGeneratedInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page_id: str = Field(min_length=1, max_length=512)
    generated: str = Field(max_length=_MAX_CONTENT_CHARS)
    expected_version: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    expected_head: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")


def _wiki(execution: ToolExecution) -> WikiService | ToolResult:
    wiki = execution.ctx.services.get(WIKI_SERVICE)
    if not isinstance(wiki, WikiService):
        return ToolResult.failure(
            code="not_configured",
            message="The managed wiki is unavailable.",
            preview="Wiki unavailable",
            recovery_action="Enable canonical memory before retrying.",
        )
    return wiki


def _selector(selector: WikiPageSelector) -> tuple[str, str]:
    if selector.page_id is not None:
        return "page_id", selector.page_id
    if selector.path is not None:
        return "path", selector.path
    if selector.title is not None:
        return "title", selector.title
    if selector.alias is not None:
        return "alias", selector.alias
    raise AssertionError("WikiPageSelector requires one selector")


def _page_data(record: WikiPageRecord, head: str | None) -> dict[str, object]:
    return {
        "page_id": record.page.page_id,
        "path": record.resource.path,
        "title": record.page.title,
        "aliases": list(record.page.aliases),
        "lifecycle": record.page.lifecycle,
        "version": record.resource.version_id,
        "head": head,
    }


def _page_ref(record: WikiPageRecord) -> ToolSourceRef:
    return ToolSourceRef(
        provider="memory",
        kind="wiki_page",
        ref=record.page.page_id,
        title=record.page.title,
    )


def _resolve(wiki: WikiService, selector: WikiPageSelector) -> tuple[WikiPageRecord, str | None] | ToolResult:
    field, value = _selector(selector)
    try:
        snapshot = wiki.snapshot()
    except WikiAmbiguityError as exc:
        return ToolResult.failure(code="ambiguous_ref", message=str(exc), preview="Ambiguous wiki reference")
    except WikiValidationError as exc:
        return ToolResult.failure(code="invalid_ref", message=str(exc), preview="Invalid wiki reference")

    pages = tuple(record for record in snapshot.pages if record.page.lifecycle == "active")
    if field == "page_id":
        matches = tuple(record for record in pages if record.page.page_id == value)
    elif field == "path":
        matches = tuple(record for record in pages if record.resource.path == value)
    elif field == "title":
        matches = tuple(record for record in pages if record.page.title == value)
    else:
        matches = tuple(record for record in pages if value in record.page.aliases)

    if not matches:
        return ToolResult.failure(
            code="not_found",
            message=f"No active wiki page has {field} {value!r}.",
            preview="Wiki page not found",
        )
    if len(matches) != 1:
        return ToolResult.failure(
            code="ambiguous_ref",
            message=f"Wiki {field} {value!r} matches multiple active pages.",
            preview="Ambiguous wiki reference",
            data={"candidates": [_page_data(record, snapshot.head) for record in matches]},
        )
    return matches[0], snapshot.head


def _bounded_content(content: str, *, offset: int, limit: int) -> tuple[str, bool]:
    rendered = format_lines_with_pagination(content, offset, limit)
    if len(rendered) <= _MAX_CONTENT_CHARS:
        return rendered, False
    return rendered[: _MAX_CONTENT_CHARS - len(_CONTENT_TRUNCATION)] + _CONTENT_TRUNCATION, True


def _bounded_text(value: str | None, limit: int) -> tuple[str | None, bool]:
    if value is None or len(value) <= limit:
        return value, False
    return value[:limit] + "…", True


def _link_data(reference: LinkReference) -> tuple[dict[str, object], bool]:
    source_page_id, source_truncated = _bounded_text(reference.source_page_id, _MAX_LINK_ID_CHARS)
    target_page_id, target_truncated = _bounded_text(reference.target_page_id, _MAX_LINK_ID_CHARS)
    page, page_truncated = _bounded_text(reference.node.page, _MAX_LINK_FIELD_CHARS)
    fragment, fragment_truncated = _bounded_text(reference.node.fragment, _MAX_LINK_FIELD_CHARS)
    alias, alias_truncated = _bounded_text(reference.node.alias, _MAX_LINK_FIELD_CHARS)
    candidates = []
    candidate_truncated = len(reference.candidates) > _MAX_LINK_CANDIDATES
    for candidate in reference.candidates[:_MAX_LINK_CANDIDATES]:
        bounded, truncated = _bounded_text(candidate, _MAX_LINK_ID_CHARS)
        candidates.append(bounded)
        candidate_truncated = candidate_truncated or truncated
    truncated = any(
        (
            source_truncated,
            target_truncated,
            page_truncated,
            fragment_truncated,
            alias_truncated,
            candidate_truncated,
        )
    )
    return {
        "source_page_id": source_page_id,
        "target_page_id": target_page_id,
        "status": reference.status.value,
        "candidates": candidates,
        "page": page,
        "fragment": fragment,
        "alias": alias,
        "embed": reference.node.embed,
    }, truncated


def _bounded_links(
    references: tuple[LinkReference, ...],
    *,
    limit: int,
    budget: int,
) -> tuple[list[dict[str, object]], int, bool]:
    result: list[dict[str, object]] = []
    used = 0
    fields_truncated = False
    for reference in references[:limit]:
        item, truncated = _link_data(reference)
        size = len(json.dumps(item, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) + 1
        if used + size > budget:
            break
        result.append(item)
        used += size
        fields_truncated = fields_truncated or truncated
    return result, used, fields_truncated


async def read_wiki_page(execution: ToolExecution, args: ReadWikiPageInput) -> ToolResult:
    wiki = _wiki(execution)
    if isinstance(wiki, ToolResult):
        return wiki
    resolved = await asyncio.to_thread(_resolve, wiki, args)
    if isinstance(resolved, ToolResult):
        return resolved
    record, head = resolved
    content, truncated = _bounded_content(record.content.decode("utf-8"), offset=args.offset, limit=args.limit)
    return ToolResult(
        content=content,
        preview=record.page.title,
        source_refs=(_page_ref(record),),
        data={
            "page": _page_data(record, head),
            "offset": args.offset,
            "limit": args.limit,
            "content_truncated": truncated,
        },
    )


async def wiki_links(execution: ToolExecution, args: WikiLinksInput) -> ToolResult:
    wiki = _wiki(execution)
    if isinstance(wiki, ToolResult):
        return wiki
    resolved = await asyncio.to_thread(_resolve, wiki, args)
    if isinstance(resolved, ToolResult):
        return resolved
    record, head = resolved
    report = await asyncio.to_thread(wiki.link_report, record.page.page_id, at=head)
    page = _page_data(report.page, report.head)
    static_size = len(json.dumps(page, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) + 1_000
    if static_size >= _MAX_LINK_DATA_BYTES:
        return ToolResult.failure(
            code="result_too_large",
            message="The wiki page identity exceeds the link-result budget.",
            preview="Wiki page identity too large",
        )
    remaining = _MAX_LINK_DATA_BYTES - static_size
    outgoing_budget = remaining // 2 if report.backlinks else remaining
    outgoing, outgoing_size, outgoing_fields_truncated = _bounded_links(
        report.outgoing,
        limit=args.limit,
        budget=outgoing_budget,
    )
    backlinks, _backlinks_size, backlink_fields_truncated = _bounded_links(
        report.backlinks,
        limit=args.limit,
        budget=remaining - outgoing_size,
    )
    links_truncated = len(outgoing) != len(report.outgoing) or len(backlinks) != len(report.backlinks)
    data = {
        "page": page,
        "outgoing": outgoing,
        "backlinks": backlinks,
        "outgoing_total": len(report.outgoing),
        "backlinks_total": len(report.backlinks),
        "links_truncated": links_truncated,
        "fields_truncated": outgoing_fields_truncated or backlink_fields_truncated,
    }
    while len(json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > _MAX_LINK_DATA_BYTES:
        if backlinks:
            backlinks.pop()
        elif outgoing:
            outgoing.pop()
        else:
            return ToolResult.failure(
                code="result_too_large",
                message="The wiki link metadata exceeds the result budget.",
                preview="Wiki links too large",
            )
        data["links_truncated"] = True
    return ToolResult(
        content=f"{len(report.outgoing)} outgoing links; {len(report.backlinks)} backlinks.",
        preview=report.page.page.title,
        source_refs=(_page_ref(report.page),),
        data=data,
    )


async def publish_wiki_generated(execution: ToolExecution, args: PublishWikiGeneratedInput) -> ToolResult:
    wiki = _wiki(execution)
    if isinstance(wiki, ToolResult):
        return wiki
    generated = args.generated.encode("utf-8")
    if generated and not generated.endswith(b"\n"):
        generated += b"\n"
    try:
        snapshot = await asyncio.to_thread(wiki.snapshot)
        if snapshot.head != args.expected_head:
            raise RevisionConflictError(
                f"current head changed: expected {args.expected_head!r}, found {snapshot.head!r}"
            )
        record = next(
            (page for page in snapshot.pages if page.page.page_id == args.page_id and page.page.lifecycle == "active"),
            None,
        )
        if record is None:
            return ToolResult.failure(
                code="not_found",
                message=f"No active wiki page has page_id {args.page_id!r}.",
                preview="Wiki page not found",
            )
        if record.resource.version_id != args.expected_version:
            raise RevisionConflictError(f"resource {args.page_id} changed: expected {args.expected_version}")
        if (
            record.resource.path.endswith("/README.md")
            or not record.resource.path.startswith(("feeds/", "insights/"))
            or "fact_citations" in record.page.metadata
        ):
            return ToolResult.failure(
                code="not_producer_page",
                message="Only registered feed or insight producer pages can be updated with this tool.",
                preview="Page is not producer-owned",
            )
        automation_id = execution.ctx.run.automation_id
        if automation_id is None:
            return ToolResult.failure(
                code="automation_required",
                message="Generated wiki publishing is available only to a scheduled automation run.",
                preview="Automation required",
            )
        producer_automation_id = record.page.metadata.get("producer_automation_id")
        if producer_automation_id != automation_id:
            return ToolResult.failure(
                code="producer_mismatch",
                message=(
                    f"Page {record.resource.path} is owned by automation "
                    f"{producer_automation_id!r}, not {automation_id!r}."
                ),
                preview="Different producer owns this page",
            )
        if extract_generated_region(record.content, expected_page_id=record.page.page_id) == generated:
            return ToolResult(
                content=f"{record.resource.path} already has that generated content.",
                preview="Wiki page unchanged",
                source_refs=(_page_ref(record),),
                data={"page": _page_data(record, snapshot.head), "commit_id": None, "changed": False},
            )
        metadata = {
            key: value
            for key, value in record.page.metadata.items()
            if key not in {"generated_from_revision", "fact_citations"}
        }
        actor = f"Automation {automation_id}"
        origin = f"wiki.automation.{automation_id}"
        source_revision = sha256(b"wiki.producer.v1\0" + automation_id.encode("utf-8") + b"\0" + generated).hexdigest()
        commit = await asyncio.to_thread(
            wiki.publish_generated,
            (
                GeneratedPageTarget(
                    record.page.page_id,
                    record.resource.path,
                    record.page.title,
                    record.page.aliases,
                    generated,
                    metadata,
                ),
            ),
            source_revision=source_revision,
            base_head=snapshot.head,
            actor=actor,
            origin=origin,
            reason=f"update generated wiki page {record.resource.path}",
        )
    except RevisionConflictError as exc:
        return ToolResult.failure(
            code="revision_conflict",
            message=str(exc),
            preview="Wiki page changed",
            retryable=True,
            recovery_action="Read the page again and retry with its current version and repository head.",
        )
    except GeneratedRegionConflictError as exc:
        return ToolResult.failure(
            code="generated_region_conflict",
            message=str(exc),
            preview="Generated region changed",
            recovery_action="Read the page again; do not overwrite user changes.",
        )
    except WikiValidationError as exc:
        return ToolResult.failure(code="invalid_page", message=str(exc), preview="Invalid wiki page")

    published_head = commit.commit_id if commit is not None else snapshot.head
    updated = await asyncio.to_thread(wiki.read_page, args.page_id, at=published_head)
    return ToolResult(
        content=f"Updated the generated region in {updated.resource.path}.",
        preview="Wiki page updated",
        source_refs=(_page_ref(updated),),
        data={
            "page": _page_data(updated, published_head),
            "commit_id": None if commit is None else commit.commit_id,
            "changed": commit is not None,
        },
    )


async def approve_publish_wiki_generated(
    _execution: ToolExecution,
    args: PublishWikiGeneratedInput,
) -> ApprovalInfo:
    return ApprovalInfo(
        description=f"Update generated wiki content for {args.page_id}",
        preview=args.generated[:1_500],
        diff=(f"Expected page version: {args.expected_version}\nExpected repository head: {args.expected_head}"),
    )


read_wiki_page_tool = tool(
    display_name="ReadWikiPage",
    display_description="Read one managed wiki page.",
    description="Read one active managed wiki page by an exact page ID, path, title, or alias.",
    input_model=ReadWikiPageInput,
    policy=ToolPolicy(
        action=ToolAction.READ,
        scope=ToolScope.INTERNAL,
        permissions=_WIKI_PERMISSION,
        max_result_chars=_MAX_CONTENT_CHARS,
    ),
    execute=read_wiki_page,
)

wiki_links_tool = tool(
    display_name="WikiLinks",
    display_description="Read links for one managed wiki page.",
    description="Read exact outgoing links and backlinks for one active managed wiki page.",
    input_model=WikiLinksInput,
    policy=ToolPolicy(
        action=ToolAction.READ,
        scope=ToolScope.INTERNAL,
        permissions=_WIKI_PERMISSION,
        max_result_chars=_MAX_LINK_DATA_BYTES,
    ),
    execute=wiki_links,
)

publish_wiki_generated_tool = tool(
    display_name="PublishWikiGenerated",
    display_description="Update one page's generated region.",
    description=(
        "Update only the generated region of one existing managed wiki page. "
        "Read the page first and provide its exact version and repository head."
    ),
    input_model=PublishWikiGeneratedInput,
    policy=ToolPolicy(
        action=ToolAction.WRITE,
        scope=ToolScope.INTERNAL,
        requires_approval=True,
        permissions=_WIKI_PERMISSION,
        max_result_chars=4_000,
        destructive=False,
        idempotent=False,
    ),
    approval=approve_publish_wiki_generated,
    execute=publish_wiki_generated,
)
