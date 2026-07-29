"""Bounded agent access to the managed wiki."""

import asyncio
import json
from hashlib import sha256
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from arden.agent.types.tools import ToolSourceRef
from arden.revisions.errors import RevisionConflictError
from arden.revisions.models import ResourceState, ResourceVersion
from arden.tools.core import ToolResult, tool
from arden.tools.core.context import ToolExecution
from arden.tools.core.formatting import format_lines_with_pagination
from arden.tools.core.types import ApprovalInfo, ToolAction, ToolPolicy, ToolScope
from arden.wiki.constants import (
    AUTOMATIONS_PATH_PREFIX,
    WIKI_HEALTH_PATH,
    WIKI_HEALTH_RESOURCE_ID,
    WIKI_POST_COMMIT_SERVICE,
)
from arden.wiki.models import GeneratedPageTarget, LinkReference, WikiPageRecord
from arden.wiki.pages import PageValidationError, extract_generated_region, parse_page
from arden.wiki.pages import create_page as build_page
from arden.wiki.service import (
    GeneratedRegionConflictError,
    WikiAmbiguityError,
    WikiService,
    WikiValidationError,
)

WIKI_SERVICE = "wiki"
_MAX_PAGE_LINES = 4_000
_MAX_CONTENT_CHARS = 40_000
_MAX_LIST_ENTRIES = 200
_MAX_LIST_DATA_BYTES = 40_000
_MAX_LINKS = 500
_MAX_LINK_DATA_BYTES = 40_000
_MAX_LINK_FIELD_CHARS = 2_000
_MAX_LINK_ID_CHARS = 512
_MAX_LINK_CANDIDATES = 20
_CONTENT_TRUNCATION = "\n[truncated at 40000 characters]"
_WIKI_PERMISSION = frozenset({WIKI_SERVICE})
_WIKI_WRITE_PERMISSIONS = frozenset({WIKI_SERVICE, WIKI_POST_COMMIT_SERVICE})


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


class ListWikiPagesInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    directory: str = Field(
        default="",
        max_length=4096,
        description="Exact wiki directory, such as 'topics'. Empty means the wiki root.",
    )
    limit: int = Field(default=100, ge=1, le=_MAX_LIST_ENTRIES)


class WikiLinksInput(WikiPageSelector):
    limit: int = Field(default=100, ge=1, le=_MAX_LINKS)


class CreateWikiPageInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page_id: str = Field(
        min_length=1,
        max_length=512,
        description="Stable page identity. Use a concise durable slug; it cannot be changed later.",
    )
    path: str = Field(
        min_length=4,
        max_length=4096,
        description="Relative POSIX .md path. Scheduled automations may create only under automations/.",
    )
    title: str = Field(min_length=1, max_length=4096)
    aliases: list[str] = Field(default_factory=list, max_length=100)
    body: str = Field(default="", max_length=_MAX_CONTENT_CHARS)
    expected_head: str | None = Field(
        description="Exact repository head returned by list_wiki_pages; use null only when the wiki is empty.",
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    )


class EditWikiPageInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page_id: str = Field(min_length=1, max_length=512)
    body: str = Field(max_length=_MAX_CONTENT_CHARS)
    expected_version: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    expected_head: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")


class ArchiveWikiPageInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page_id: str = Field(min_length=1, max_length=512)
    expected_version: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    expected_head: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    reason: str = Field(default="archive wiki page", min_length=1, max_length=500)


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


def _archived_page_data(wiki: WikiService, resource: ResourceVersion, head: str | None) -> dict[str, object]:
    page = parse_page(wiki.repository.read_version(resource), expected_page_id=resource.resource_id)
    return {
        "page_id": page.page_id,
        "path": resource.path,
        "title": page.title,
        "aliases": list(page.aliases),
        "lifecycle": "archived",
        "version": resource.version_id,
        "head": head,
    }


def _page_ref(record: WikiPageRecord) -> ToolSourceRef:
    return ToolSourceRef(
        provider="memory",
        kind="wiki_page",
        ref=record.page.page_id,
        title=record.page.title,
    )


def _write_identity(execution: ToolExecution) -> tuple[str, str]:
    automation_id = execution.ctx.run.automation_id
    if automation_id is not None:
        return f"Automation {automation_id}", f"wiki.automation.{automation_id}"
    return "Agent", "wiki.agent"


def _automation_path_error(execution: ToolExecution, path: str) -> ToolResult | None:
    if execution.ctx.run.automation_id is None or path.startswith(AUTOMATIONS_PATH_PREFIX):
        return None
    return ToolResult.failure(
        code="automation_path_denied",
        message=f"Scheduled automations may write only under {AUTOMATIONS_PATH_PREFIX}.",
        preview="Wiki path outside automation workspace",
        recovery_action=f"Use a page path under {AUTOMATIONS_PATH_PREFIX} and retry.",
    )


def _producer_page_error(execution: ToolExecution, record: WikiPageRecord) -> ToolResult | None:
    if execution.ctx.run.automation_id is None or "producer_automation_id" not in record.page.metadata:
        return None
    return ToolResult.failure(
        code="producer_page_requires_generated_publish",
        message="Automation-owned generated regions must be changed with publish_wiki_generated.",
        preview="Use generated-region publishing",
        recovery_action="Read the page, then call publish_wiki_generated with its current version and head.",
    )


def _revision_conflict(error: RevisionConflictError) -> ToolResult:
    return ToolResult.failure(
        code="revision_conflict",
        message=str(error),
        preview="Wiki changed",
        retryable=True,
        recovery_action="Read the page or directory again and retry with its current version and repository head.",
    )


def _invalid_page(error: ValueError) -> ToolResult:
    return ToolResult.failure(code="invalid_page", message=str(error), preview="Invalid wiki page")


def _directory(value: str) -> str | None:
    if not value:
        return ""
    if value.startswith("/") or "\\" in value:
        return None
    directory = value.removesuffix("/")
    if not directory or any(part in {"", ".", ".."} for part in directory.split("/")):
        return None
    return directory


def _listing_page_data(record: WikiPageRecord) -> dict[str, object]:
    return {
        "kind": "page",
        "page_id": record.page.page_id,
        "path": record.resource.path,
        "title": record.page.title,
        "lifecycle": record.page.lifecycle,
        "version": record.resource.version_id,
    }


def _listing_entries(
    pages: tuple[WikiPageRecord, ...],
    directory: str,
) -> list[dict[str, object]]:
    prefix = f"{directory}/" if directory else ""
    directories: set[str] = set()
    entries: list[dict[str, object]] = []
    for record in pages:
        path = record.resource.path
        if not path.startswith(prefix):
            continue
        relative = path[len(prefix) :]
        if not relative:
            continue
        child, separator, _remainder = relative.partition("/")
        if separator:
            directories.add(f"{prefix}{child}/")
            continue
        entries.append(_listing_page_data(record))
    return [
        *({"kind": "directory", "path": path} for path in sorted(directories)),
        *sorted(entries, key=lambda entry: str(entry["path"])),
    ]


def _bounded_listing(entries: list[dict[str, object]], limit: int) -> list[dict[str, object]]:
    visible: list[dict[str, object]] = []
    used = 0
    for entry in entries[:limit]:
        size = len(json.dumps(entry, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) + 1
        if used + size > _MAX_LIST_DATA_BYTES:
            break
        visible.append(entry)
        used += size
    return visible


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


def _bounded_content(
    content: str,
    *,
    offset: int,
    limit: int,
    max_chars: int = _MAX_CONTENT_CHARS,
) -> tuple[str, bool]:
    rendered = format_lines_with_pagination(content, offset, limit)
    if len(rendered) <= max_chars:
        return rendered, False
    return rendered[: max_chars - len(_CONTENT_TRUNCATION)] + _CONTENT_TRUNCATION, True


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


async def list_wiki_pages(execution: ToolExecution, args: ListWikiPagesInput) -> ToolResult:
    wiki = _wiki(execution)
    if isinstance(wiki, ToolResult):
        return wiki
    directory = _directory(args.directory)
    if directory is None:
        return ToolResult.failure(
            code="invalid_directory",
            message="Wiki directory must be a relative POSIX path without '.' or '..'.",
            preview="Invalid wiki directory",
        )
    try:
        snapshot = await asyncio.to_thread(wiki.snapshot)
    except WikiAmbiguityError as exc:
        return ToolResult.failure(code="ambiguous_ref", message=str(exc), preview="Ambiguous wiki reference")
    except WikiValidationError as exc:
        return ToolResult.failure(code="invalid_ref", message=str(exc), preview="Invalid wiki reference")
    pages = tuple(record for record in snapshot.pages if record.page.lifecycle == "active")
    entries = _listing_entries(pages, directory)
    if directory and not entries:
        return ToolResult.failure(
            code="not_found",
            message=f"No active wiki directory exists at {directory!r}.",
            preview="Wiki directory not found",
        )
    visible = _bounded_listing(entries, args.limit)
    lines = []
    for entry in visible:
        if entry["kind"] == "directory":
            lines.append(f"[directory] {entry['path']}")
        else:
            lines.append(f"[page] {entry['path']} — {entry['title']} ({entry['page_id']})")
    if not lines:
        lines.append("No wiki entries.")
    has_more = len(visible) < len(entries)
    if has_more:
        lines.append("More entries exist; list a narrower directory.")
    label = "/" if not directory else f"/{directory}/"
    return ToolResult(
        content=f"Wiki {label}\n" + "\n".join(lines),
        preview=f"{len(visible)} wiki entries" + (" (capped)" if has_more else ""),
        data={
            "head": snapshot.head,
            "directory": directory,
            "entries": visible,
            "total": len(entries),
            "has_more": has_more,
        },
    )


async def read_wiki_page(execution: ToolExecution, args: ReadWikiPageInput) -> ToolResult:
    wiki = _wiki(execution)
    if isinstance(wiki, ToolResult):
        return wiki
    resolved = await asyncio.to_thread(_resolve, wiki, args)
    if isinstance(resolved, ToolResult):
        return resolved
    record, head = resolved
    page = _page_data(record, head)
    prefix = (
        f"Wiki page metadata: {json.dumps(page, ensure_ascii=False, separators=(',', ':'))}\n\nWiki page content:\n"
    )
    content, truncated = _bounded_content(
        record.content.decode("utf-8"),
        offset=args.offset,
        limit=args.limit,
        max_chars=_MAX_CONTENT_CHARS - len(prefix),
    )
    return ToolResult(
        content=prefix + content,
        preview=record.page.title,
        source_refs=(_page_ref(record),),
        data={
            "page": page,
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


async def create_wiki_page(execution: ToolExecution, args: CreateWikiPageInput) -> ToolResult:
    wiki = _wiki(execution)
    if isinstance(wiki, ToolResult):
        return wiki
    if error := _automation_path_error(execution, args.path):
        return error
    if args.page_id == WIKI_HEALTH_RESOURCE_ID or args.path.casefold() == WIKI_HEALTH_PATH:
        return _invalid_page(WikiValidationError("health page is backend-managed"))
    body = args.body.encode("utf-8")
    try:
        desired = build_page(
            page_id=args.page_id,
            title=args.title,
            aliases=tuple(args.aliases),
            body=body,
        ).to_bytes()
        snapshot = await asyncio.to_thread(wiki.snapshot)
        current = next((record for record in snapshot.pages if record.page.page_id == args.page_id), None)
        if current is not None and current.resource.path == args.path and current.content == desired:
            return ToolResult(
                content=f"{args.path} already has the requested content.",
                preview="Wiki page unchanged",
                source_refs=(_page_ref(current),),
                data={
                    "page": _page_data(current, snapshot.head),
                    "commit_id": None,
                    "changed": False,
                    "projection_pending": False,
                },
            )
        if snapshot.head != args.expected_head:
            raise RevisionConflictError(
                f"current head changed: expected {args.expected_head!r}, found {snapshot.head!r}"
            )
        actor, origin = _write_identity(execution)
        created = await asyncio.to_thread(
            wiki.create_page,
            page_id=args.page_id,
            path=args.path,
            title=args.title,
            aliases=tuple(args.aliases),
            body=body,
            expected_head=snapshot.head,
            actor=actor,
            origin=origin,
            reason=f"create wiki page {args.path}",
        )
    except RevisionConflictError as error:
        return _revision_conflict(error)
    except WikiAmbiguityError as error:
        return ToolResult.failure(code="name_conflict", message=str(error), preview="Wiki name already exists")
    except (PageValidationError, WikiValidationError) as error:
        return _invalid_page(error)

    commit_id = wiki.repository.head
    projection_pending = await execution.ctx.services[WIKI_POST_COMMIT_SERVICE]()
    head = wiki.repository.head
    return ToolResult(
        content=f"Created {created.resource.path}.",
        preview="Wiki page created",
        source_refs=(_page_ref(created),),
        data={
            "page": _page_data(created, head),
            "commit_id": commit_id,
            "changed": True,
            "projection_pending": projection_pending,
        },
    )


async def edit_wiki_page(execution: ToolExecution, args: EditWikiPageInput) -> ToolResult:
    wiki = _wiki(execution)
    if isinstance(wiki, ToolResult):
        return wiki
    try:
        snapshot = await asyncio.to_thread(wiki.snapshot)
        record = next(
            (item for item in snapshot.pages if item.page.page_id == args.page_id and item.page.lifecycle == "active"),
            None,
        )
        if record is None:
            return ToolResult.failure(
                code="not_found",
                message=f"No active wiki page has page_id {args.page_id!r}.",
                preview="Wiki page not found",
            )
        if error := _automation_path_error(execution, record.resource.path):
            return error
        if error := _producer_page_error(execution, record):
            return error
        replacement = record.page.with_body(args.body.encode("utf-8")).to_bytes()
        if replacement == record.content:
            return ToolResult(
                content=f"{record.resource.path} already has the requested body.",
                preview="Wiki page unchanged",
                source_refs=(_page_ref(record),),
                data={
                    "page": _page_data(record, snapshot.head),
                    "commit_id": None,
                    "changed": False,
                    "projection_pending": False,
                },
            )
        actor, origin = _write_identity(execution)
        updated, commit_id = await asyncio.to_thread(
            wiki.update_page_with_commit,
            args.page_id,
            content=replacement,
            expected_version=args.expected_version,
            expected_head=args.expected_head,
            actor=actor,
            origin=origin,
            reason=f"edit wiki page {record.resource.path}",
        )
    except RevisionConflictError as error:
        return _revision_conflict(error)
    except (PageValidationError, WikiValidationError) as error:
        return _invalid_page(error)

    projection_pending = await execution.ctx.services[WIKI_POST_COMMIT_SERVICE]()
    return ToolResult(
        content=f"Updated {updated.resource.path}.",
        preview="Wiki page updated",
        source_refs=(_page_ref(updated),),
        data={
            "page": _page_data(updated, wiki.repository.head),
            "commit_id": commit_id,
            "changed": True,
            "projection_pending": projection_pending,
        },
    )


async def archive_wiki_page(execution: ToolExecution, args: ArchiveWikiPageInput) -> ToolResult:
    wiki = _wiki(execution)
    if isinstance(wiki, ToolResult):
        return wiki
    try:
        snapshot = await asyncio.to_thread(wiki.snapshot)
        record = next((item for item in snapshot.pages if item.page.page_id == args.page_id), None)
        if record is None:
            try:
                resource = await asyncio.to_thread(wiki.repository.get, args.page_id, at=snapshot.head)
            except KeyError:
                return ToolResult.failure(
                    code="not_found",
                    message=f"No wiki page has page_id {args.page_id!r}.",
                    preview="Wiki page not found",
                )
            if resource.state is not ResourceState.ARCHIVED:
                return ToolResult.failure(
                    code="not_found",
                    message=f"No active wiki page has page_id {args.page_id!r}.",
                    preview="Wiki page not found",
                )
            if error := _automation_path_error(execution, resource.path):
                return error
            return ToolResult(
                content=f"{resource.path} is already archived.",
                preview="Wiki page unchanged",
                data={
                    "page": _archived_page_data(wiki, resource, snapshot.head),
                    "commit_id": None,
                    "changed": False,
                    "projection_pending": False,
                },
            )
        if error := _automation_path_error(execution, record.resource.path):
            return error
        if error := _producer_page_error(execution, record):
            return error
        if record.page.page_id == WIKI_HEALTH_RESOURCE_ID:
            return _invalid_page(WikiValidationError("health page is backend-managed"))
        actor, origin = _write_identity(execution)
        await asyncio.to_thread(
            wiki.archive_page,
            args.page_id,
            expected_version=args.expected_version,
            base_head=args.expected_head,
            actor=actor,
            origin=origin,
            reason=args.reason,
        )
    except RevisionConflictError as error:
        return _revision_conflict(error)
    except WikiValidationError as error:
        return _invalid_page(error)

    resource = await asyncio.to_thread(wiki.repository.get, args.page_id, at=wiki.repository.head)
    projection_pending = await execution.ctx.services[WIKI_POST_COMMIT_SERVICE]()
    return ToolResult(
        content=f"Archived {resource.path}.",
        preview="Wiki page archived",
        data={
            "page": _archived_page_data(wiki, resource, wiki.repository.head),
            "commit_id": wiki.repository.head,
            "changed": True,
            "projection_pending": projection_pending,
        },
    )


async def approve_create_wiki_page(
    _execution: ToolExecution,
    args: CreateWikiPageInput,
) -> ApprovalInfo:
    return ApprovalInfo(
        description=f"Create wiki page {args.path}",
        preview=args.body[:1_500],
        diff=f"Title: {args.title}\nPage ID: {args.page_id}\nExpected repository head: {args.expected_head}",
    )


async def approve_edit_wiki_page(
    _execution: ToolExecution,
    args: EditWikiPageInput,
) -> ApprovalInfo:
    return ApprovalInfo(
        description=f"Replace the body of wiki page {args.page_id}",
        preview=args.body[:1_500],
        diff=f"Expected page version: {args.expected_version}\nExpected repository head: {args.expected_head}",
    )


async def approve_archive_wiki_page(
    _execution: ToolExecution,
    args: ArchiveWikiPageInput,
) -> ApprovalInfo:
    return ApprovalInfo(
        description=f"Archive wiki page {args.page_id}",
        preview=args.reason,
        diff=f"Expected page version: {args.expected_version}\nExpected repository head: {args.expected_head}",
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
        if not record.resource.path.startswith((AUTOMATIONS_PATH_PREFIX, "insights/")) or "fact_citations" in (
            record.page.metadata
        ):
            return ToolResult.failure(
                code="not_producer_page",
                message="Only registered automation or insight producer pages can be updated with this tool.",
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
                data={
                    "page": _page_data(record, snapshot.head),
                    "commit_id": None,
                    "changed": False,
                    "projection_pending": False,
                },
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
    projection_pending = await execution.ctx.services[WIKI_POST_COMMIT_SERVICE]() if commit is not None else False
    current_head = wiki.repository.head
    return ToolResult(
        content=f"Updated the generated region in {updated.resource.path}.",
        preview="Wiki page updated",
        source_refs=(_page_ref(updated),),
        data={
            "page": _page_data(updated, current_head),
            "commit_id": None if commit is None else commit.commit_id,
            "changed": commit is not None,
            "projection_pending": projection_pending,
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


list_wiki_pages_tool = tool(
    display_name="ListWikiPages",
    display_description="List one managed wiki directory.",
    description="List direct child directories and active pages in one managed wiki directory.",
    input_model=ListWikiPagesInput,
    policy=ToolPolicy(
        action=ToolAction.READ,
        scope=ToolScope.INTERNAL,
        permissions=_WIKI_PERMISSION,
        max_result_chars=_MAX_LIST_DATA_BYTES,
    ),
    execute=list_wiki_pages,
)

create_wiki_page_tool = tool(
    display_name="CreateWikiPage",
    display_description="Create one managed wiki page.",
    description=(
        "Create one common managed wiki page from a stable page ID, path, title, aliases, and Markdown body. "
        "List the wiki root first and provide its exact repository head. "
        f"Scheduled automations can create pages only under {AUTOMATIONS_PATH_PREFIX}."
    ),
    input_model=CreateWikiPageInput,
    policy=ToolPolicy(
        action=ToolAction.WRITE,
        scope=ToolScope.INTERNAL,
        requires_approval=True,
        permissions=_WIKI_WRITE_PERMISSIONS,
        max_result_chars=4_000,
        destructive=False,
        idempotent=True,
    ),
    approval=approve_create_wiki_page,
    execute=create_wiki_page,
)

edit_wiki_page_tool = tool(
    display_name="EditWikiPage",
    display_description="Replace one managed wiki page body.",
    description=(
        "Replace only the Markdown body of one active managed wiki page while preserving its identity and metadata. "
        "Read the page first and provide its exact page ID, version, and repository head. "
        f"Scheduled automations can edit pages only under {AUTOMATIONS_PATH_PREFIX}."
    ),
    input_model=EditWikiPageInput,
    policy=ToolPolicy(
        action=ToolAction.WRITE,
        scope=ToolScope.INTERNAL,
        requires_approval=True,
        permissions=_WIKI_WRITE_PERMISSIONS,
        max_result_chars=4_000,
        destructive=False,
        idempotent=True,
    ),
    approval=approve_edit_wiki_page,
    execute=edit_wiki_page,
)

archive_wiki_page_tool = tool(
    display_name="ArchiveWikiPage",
    display_description="Archive one managed wiki page.",
    description=(
        "Recoverably archive one active managed wiki page; this never hard-deletes its history. "
        "Read the page first and provide its exact page ID, version, and repository head. "
        f"Scheduled automations can archive pages only under {AUTOMATIONS_PATH_PREFIX}."
    ),
    input_model=ArchiveWikiPageInput,
    policy=ToolPolicy(
        action=ToolAction.WRITE,
        scope=ToolScope.INTERNAL,
        requires_approval=True,
        permissions=_WIKI_WRITE_PERMISSIONS,
        max_result_chars=4_000,
        destructive=True,
        idempotent=True,
    ),
    approval=approve_archive_wiki_page,
    execute=archive_wiki_page,
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
        permissions=_WIKI_WRITE_PERMISSIONS,
        max_result_chars=4_000,
        destructive=False,
        idempotent=False,
    ),
    approval=approve_publish_wiki_generated,
    execute=publish_wiki_generated,
)
