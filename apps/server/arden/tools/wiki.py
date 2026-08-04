"""Bounded agent access to the managed wiki."""

import asyncio
import json
from datetime import UTC, date
from hashlib import sha256

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from arden.agent.types.tools import ToolSourceRef
from arden.constants import DAILY_NOTES_AUTOMATION_ID, OFFLOAD_THRESHOLD
from arden.revisions.errors import RevisionConflictError
from arden.revisions.models import ResourceState, ResourceVersion
from arden.services.session import SessionService
from arden.tools.core import ToolResult, tool
from arden.tools.core.context import ResourceObservation, ToolExecution
from arden.tools.core.formatting import format_lines_with_pagination
from arden.tools.core.types import ApprovalInfo, ToolAction, ToolPolicy, ToolScope
from arden.wiki.constants import (
    AUTOMATIONS_PATH_PREFIX,
    README_FILENAME,
    WIKI_HEALTH_PATH,
    WIKI_HEALTH_RESOURCE_ID,
    WIKI_POST_COMMIT_SERVICE,
    wiki_page_observation_id,
)
from arden.wiki.models import GeneratedPageTarget, LinkReference, WikiPageRecord, WikiSnapshot
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
_MAX_LINK_PATH_CHARS = 4_096
_CONTENT_TRUNCATION = "\n[truncated at 40000 characters]"
_WIKI_PERMISSION = frozenset({WIKI_SERVICE})
_WIKI_WRITE_PERMISSIONS = frozenset({WIKI_SERVICE, WIKI_POST_COMMIT_SERVICE})
_WIKI_MOVE_PERMISSIONS = frozenset({WIKI_SERVICE, WIKI_POST_COMMIT_SERVICE, "session"})


class WikiReadPageInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=4096)
    offset: int = Field(default=1, ge=1)
    limit: int = Field(default=500, ge=1, le=_MAX_PAGE_LINES)


class WikiListPagesInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    directory: str = Field(
        default="",
        max_length=4096,
        description="Exact wiki directory, such as 'topics'. Empty means the wiki root.",
    )
    offset: int = Field(default=0, ge=0, description="Zero-based entry offset from a prior list result.")
    limit: int = Field(default=100, ge=1, le=_MAX_LIST_ENTRIES)


class WikiListChangesInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    since: AwareDatetime = Field(description="Inclusive ISO timestamp, usually today's local midnight.")
    offset: int = Field(default=0, ge=0, le=10_000)
    limit: int = Field(default=100, ge=1, le=_MAX_LIST_ENTRIES)


class WikiLinksInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=4096)
    limit: int = Field(default=100, ge=1, le=_MAX_LINKS)


class WikiCreatePageInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(
        min_length=4,
        max_length=4096,
        description=(
            "Relative POSIX .md path. Scheduled automations normally create only under automations/; "
            "the predefined Daily Notes automation writes dated pages under daily/."
        ),
    )
    title: str = Field(min_length=1, max_length=4096)
    aliases: list[str] = Field(default_factory=list, max_length=100)
    body: str = Field(default="", max_length=_MAX_CONTENT_CHARS)


class WikiEditPageInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=4096)
    body: str = Field(max_length=_MAX_CONTENT_CHARS)


class WikiPatchPageInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=4096)
    old_text: str = Field(
        min_length=1,
        max_length=_MAX_CONTENT_CHARS,
        description="Exact existing body text to replace. It must match exactly once.",
    )
    new_text: str = Field(default="", max_length=_MAX_CONTENT_CHARS, description="Literal replacement text.")


class WikiArchivePageInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=4096)
    reason: str = Field(default="archive wiki page", min_length=1, max_length=500)


class WikiMovePageInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=4096)
    new_path: str = Field(
        min_length=4,
        max_length=4096,
        description="New relative POSIX .md path. The page title, identity, aliases, body, and metadata stay unchanged.",
    )


class WikiPublishGeneratedInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=4096)
    generated: str = Field(max_length=_MAX_CONTENT_CHARS)


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


def _page_data(record: WikiPageRecord) -> dict[str, object]:
    return {
        "path": record.resource.path,
        "title": record.page.title,
        "aliases": list(record.page.aliases),
        "lifecycle": record.page.lifecycle,
    }


def _archived_page_data(wiki: WikiService, resource: ResourceVersion) -> dict[str, object]:
    page = parse_page(wiki.repository.read_version(resource), expected_page_id=resource.resource_id)
    return {
        "path": resource.path,
        "title": page.title,
        "aliases": list(page.aliases),
        "lifecycle": "archived",
    }


def _page_ref(record: WikiPageRecord) -> ToolSourceRef:
    return ToolSourceRef(
        provider="memory",
        kind="wiki_page",
        ref=record.resource.path,
        title=record.page.title,
    )


def _observe_page(execution: ToolExecution, record: WikiPageRecord, head: str | None, *, content_read: bool) -> None:
    execution.ctx.run.observe_resource(
        wiki_page_observation_id(record.page.page_id),
        version=record.resource.version_id,
        container_version=head,
        content_read=content_read,
    )


def _require_page_observation(
    execution: ToolExecution,
    page_id: str,
    *,
    content_read: bool,
) -> ResourceObservation | ToolResult:
    observation = execution.ctx.run.resource_observation(wiki_page_observation_id(page_id))
    if (
        observation is not None
        and observation.version is not None
        and observation.container_version is not None
        and (observation.content_read or not content_read)
    ):
        return observation
    return ToolResult.failure(
        code="fresh_read_required",
        message="Read this wiki page before changing it.",
        preview="Read required",
        recovery_action="Call wiki_read_page for this exact path, then retry the change.",
    )


def _active_page(snapshot: WikiSnapshot, path: str) -> WikiPageRecord | None:
    return next(
        (item for item in snapshot.pages if item.resource.path == path and item.page.lifecycle == "active"),
        None,
    )


def _active_page_not_found(path: str) -> ToolResult:
    return ToolResult.failure(
        code="not_found",
        message=f"No active wiki page exists at {path!r}.",
        preview="Wiki page not found",
        recovery_action="Call wiki_list_pages or wiki_read_page and retry with an exact active page path.",
    )


def _unchanged_page(record: WikiPageRecord) -> ToolResult:
    return ToolResult(
        content=f"{record.resource.path} already has the requested body.",
        preview="Wiki page unchanged",
        source_refs=(_page_ref(record),),
        data={
            "page": _page_data(record),
            "changed": False,
            "projection_pending": False,
        },
    )


def _patch_body(record: WikiPageRecord, old_text: str, new_text: str) -> str | ToolResult:
    body = record.page.body.decode("utf-8", errors="strict")
    matches = body.count(old_text)
    if matches == 0:
        return ToolResult.failure(
            code="not_found",
            message="The exact old_text was not found in the current wiki page body.",
            preview="Patch text not found",
            recovery_action="Call wiki_read_page, copy the exact current text with more context, and retry.",
        )
    if matches > 1:
        return ToolResult.failure(
            code="invalid_ref",
            message=f"old_text matched {matches} places in the current wiki page body.",
            preview="Patch text is ambiguous",
            recovery_action="Include more surrounding text so old_text matches exactly once.",
        )
    patched = body.replace(old_text, new_text, 1)
    if len(patched) > _MAX_CONTENT_CHARS:
        return ToolResult.failure(
            code="invalid_input",
            message=f"The patched wiki page body exceeds {_MAX_CONTENT_CHARS} characters.",
            preview="Wiki patch too large",
            recovery_action="Use a smaller replacement and retry.",
        )
    return patched


def _write_identity(execution: ToolExecution) -> tuple[str, str]:
    automation_id = execution.ctx.run.automation_id
    if automation_id is not None:
        return f"Automation {automation_id}", f"wiki.automation.{automation_id}"
    return "Agent", "wiki.agent"


def _is_daily_note_path(path: str) -> bool:
    prefix = "daily/"
    suffix = ".md"
    if not path.startswith(prefix) or not path.endswith(suffix):
        return False
    value = path[len(prefix) : -len(suffix)]
    try:
        return date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _automation_path_error(execution: ToolExecution, path: str) -> ToolResult | None:
    automation_id = execution.ctx.run.automation_id
    if automation_id is None:
        return None
    if automation_id == DAILY_NOTES_AUTOMATION_ID:
        if _is_daily_note_path(path):
            return None
        return ToolResult.failure(
            code="automation_path_denied",
            message="Daily Notes may write only dated daily/YYYY-MM-DD.md pages.",
            preview="Wiki path outside daily notes",
            recovery_action="Use today's dated daily/YYYY-MM-DD.md path and retry.",
        )
    if path.startswith(AUTOMATIONS_PATH_PREFIX):
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
        message="Automation-owned generated regions must be changed with wiki_publish_generated.",
        preview="Use generated-region publishing",
        recovery_action="Read the page, then call wiki_publish_generated again.",
    )


def _revision_conflict() -> ToolResult:
    return ToolResult.failure(
        code="revision_conflict",
        message="The wiki changed after it was read.",
        preview="Wiki changed",
        retryable=True,
        recovery_action="Read the relevant page or directory again, then retry.",
    )


def _invalid_page(_error: ValueError) -> ToolResult:
    return ToolResult.failure(
        code="invalid_page",
        message="The wiki page path, metadata, or Markdown body is invalid.",
        preview="Invalid wiki page",
        recovery_action="Correct the page path, title, aliases, or Markdown body and retry.",
    )


def _directory(value: str) -> str | None:
    if not value:
        return ""
    if value.startswith("/") or "\\" in value:
        return None
    directory = value.removesuffix("/")
    if not directory or any(part in {"", ".", ".."} for part in directory.split("/")):
        return None
    return directory


def _activated_directory_readme_paths(snapshot: WikiSnapshot, page_path: str) -> tuple[str, ...]:
    directory, _separator, _filename = page_path.rpartition("/")
    normalized = _directory(directory)
    if not normalized:
        return ()
    parts = normalized.split("/")
    candidates = tuple("/".join((*parts[:index], README_FILENAME)) for index in range(1, len(parts) + 1))
    active_paths = {record.resource.path for record in snapshot.pages if record.page.lifecycle == "active"}
    return tuple(path for path in candidates if path not in active_paths)


def _listing_page_data(record: WikiPageRecord) -> dict[str, object]:
    return {
        "kind": "page",
        "path": record.resource.path,
        "title": record.page.title,
        "lifecycle": record.page.lifecycle,
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


def _resolve(wiki: WikiService, path: str) -> tuple[WikiPageRecord, str | None] | ToolResult:
    try:
        snapshot = wiki.snapshot()
    except WikiAmbiguityError:
        return ToolResult.failure(
            code="ambiguous_ref",
            message="The wiki contains an ambiguous page name.",
            preview="Ambiguous wiki reference",
            recovery_action="Call wiki_list_pages and retry with an exact page path.",
        )
    except WikiValidationError:
        return ToolResult.failure(
            code="invalid_ref",
            message="The wiki cannot resolve this page path.",
            preview="Invalid wiki reference",
            recovery_action="Call wiki_list_pages and retry with one exact returned page path.",
        )

    pages = tuple(record for record in snapshot.pages if record.page.lifecycle == "active")
    matches = tuple(record for record in pages if record.resource.path == path)

    if not matches:
        return ToolResult.failure(
            code="not_found",
            message=f"No active wiki page exists at {path!r}.",
            preview="Wiki page not found",
            recovery_action="Call wiki_list_pages and retry with an exact active page path.",
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


def _link_data(reference: LinkReference, paths: dict[str, str]) -> tuple[dict[str, object], bool]:
    source_path, source_truncated = _bounded_text(paths.get(reference.source_page_id), _MAX_LINK_PATH_CHARS)
    target_path, target_truncated = _bounded_text(paths.get(reference.target_page_id), _MAX_LINK_PATH_CHARS)
    page, page_truncated = _bounded_text(reference.node.page, _MAX_LINK_FIELD_CHARS)
    fragment, fragment_truncated = _bounded_text(reference.node.fragment, _MAX_LINK_FIELD_CHARS)
    alias, alias_truncated = _bounded_text(reference.node.alias, _MAX_LINK_FIELD_CHARS)
    truncated = any(
        (
            source_truncated,
            target_truncated,
            page_truncated,
            fragment_truncated,
            alias_truncated,
        )
    )
    return {
        "source_path": source_path,
        "target_path": target_path,
        "status": reference.status.value,
        "page": page,
        "fragment": fragment,
        "alias": alias,
        "embed": reference.node.embed,
    }, truncated


def _bounded_links(
    references: tuple[LinkReference, ...],
    *,
    paths: dict[str, str],
    limit: int,
    budget: int,
) -> tuple[list[dict[str, object]], int, bool]:
    result: list[dict[str, object]] = []
    used = 0
    fields_truncated = False
    for reference in references[:limit]:
        item, truncated = _link_data(reference, paths)
        size = len(json.dumps(item, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) + 1
        if used + size > budget:
            break
        result.append(item)
        used += size
        fields_truncated = fields_truncated or truncated
    return result, used, fields_truncated


async def wiki_list_pages(execution: ToolExecution, args: WikiListPagesInput) -> ToolResult:
    wiki = _wiki(execution)
    if isinstance(wiki, ToolResult):
        return wiki
    directory = _directory(args.directory)
    if directory is None:
        return ToolResult.failure(
            code="invalid_directory",
            message="Wiki directory must be a relative POSIX path without '.' or '..'.",
            preview="Invalid wiki directory",
            recovery_action="Retry with a relative POSIX directory such as 'topics', or empty for the root.",
        )
    try:
        snapshot = await asyncio.to_thread(wiki.snapshot)
    except WikiAmbiguityError:
        return ToolResult.failure(
            code="ambiguous_ref",
            message="The wiki contains an ambiguous page name.",
            preview="Ambiguous wiki reference",
            recovery_action="List the wiki root, then retry with one exact child directory.",
        )
    except WikiValidationError:
        return ToolResult.failure(
            code="invalid_ref",
            message="The wiki cannot list this directory.",
            preview="Invalid wiki reference",
            recovery_action="List the wiki root, then retry with one exact child directory.",
        )
    pages = tuple(record for record in snapshot.pages if record.page.lifecycle == "active")
    entries = _listing_entries(pages, directory)
    if directory and not entries:
        return ToolResult.failure(
            code="not_found",
            message=f"No active wiki directory exists at {directory!r}.",
            preview="Wiki directory not found",
            recovery_action="List its parent directory, or create the first page under this directory.",
        )
    visible = _bounded_listing(entries[args.offset :], args.limit)
    lines = []
    for entry in visible:
        if entry["kind"] == "directory":
            lines.append(f"[directory] {entry['path']}")
        else:
            lines.append(f"[page] {entry['path']} — {entry['title']}")
    if not lines:
        lines.append("No wiki entries.")
    next_offset = args.offset + len(visible)
    has_more = next_offset < len(entries)
    if has_more:
        if visible:
            lines.append(f"More entries exist; retry with offset={next_offset}.")
        else:
            lines.append(
                "More entries exist, but the result budget cannot fit the next entry. List a narrower directory."
            )
    label = "/" if not directory else f"/{directory}/"
    lines.append("Cite pages to the user as markdown links with these exact paths: [Title](path.md).")
    return ToolResult(
        content=f"Wiki {label}\n" + "\n".join(lines),
        preview=f"{len(visible)} wiki entries" + (" (capped)" if has_more else ""),
        data={
            "directory": directory,
            "offset": args.offset,
            "entries": visible,
            "total": len(entries),
            "has_more": has_more,
            "next_offset": next_offset if has_more and visible else None,
        },
    )


async def wiki_list_changes(execution: ToolExecution, args: WikiListChangesInput) -> ToolResult:
    wiki = _wiki(execution)
    if isinstance(wiki, ToolResult):
        return wiki
    since = args.since.astimezone(UTC)
    commits = await asyncio.to_thread(wiki.repository.history, limit=args.offset + args.limit + 1)
    recent = [commit for commit in commits if commit.timestamp >= since]
    visible = recent[args.offset : args.offset + args.limit]
    has_more = len(recent) > args.offset + len(visible)
    entries: list[dict[str, object]] = []
    lines: list[str] = []
    for commit in visible:
        changes = []
        for change in commit.changes:
            before_path = change.before.path if change.before is not None else None
            after_path = change.after.path if change.after is not None else None
            changes.append(
                {
                    "action": change.action,
                    "path": after_path or before_path,
                    "before_path": before_path,
                    "after_path": after_path,
                }
            )
        entries.append(
            {
                "timestamp": commit.timestamp.isoformat(),
                "actor": commit.actor,
                "origin": commit.origin,
                "reason": commit.reason,
                "changes": changes,
            }
        )
        paths = ", ".join(str(change["path"]) for change in changes)
        lines.append(f"- {commit.timestamp.isoformat()} - {paths} - {commit.reason}")
    if not lines:
        lines.append("No wiki changes in this time window.")
    next_offset = args.offset + len(visible)
    if has_more:
        lines.append(f"More changes exist; retry with offset={next_offset}.")
    return ToolResult(
        content="Wiki changes\n" + "\n".join(lines),
        preview=f"{len(visible)} wiki changes" + (" (capped)" if has_more else ""),
        data={
            "since": since.isoformat(),
            "offset": args.offset,
            "entries": entries,
            "has_more": has_more,
            "next_offset": next_offset if has_more else None,
        },
    )


async def wiki_read_page(execution: ToolExecution, args: WikiReadPageInput) -> ToolResult:
    wiki = _wiki(execution)
    if isinstance(wiki, ToolResult):
        return wiki
    resolved = await asyncio.to_thread(_resolve, wiki, args.path)
    if isinstance(resolved, ToolResult):
        return resolved
    record, head = resolved
    body = record.page.body.decode("utf-8")
    content, truncated = _bounded_content(
        body,
        offset=args.offset,
        limit=args.limit,
        max_chars=_MAX_CONTENT_CHARS,
    )
    result = ToolResult(
        content=content,
        preview=record.page.title,
        source_refs=(_page_ref(record),),
        data={
            "page": _page_data(record),
            "offset": args.offset,
            "limit": args.limit,
            "content_truncated": truncated,
        },
    )
    _observe_page(
        execution,
        record,
        head,
        content_read=(
            args.offset == 1
            and args.limit >= len(body.split("\n"))
            and not truncated
            and len(result.serialized_payload().encode("utf-8")) <= OFFLOAD_THRESHOLD
        ),
    )
    return result


async def wiki_links(execution: ToolExecution, args: WikiLinksInput) -> ToolResult:
    wiki = _wiki(execution)
    if isinstance(wiki, ToolResult):
        return wiki
    resolved = await asyncio.to_thread(_resolve, wiki, args.path)
    if isinstance(resolved, ToolResult):
        return resolved
    record, head = resolved
    report = await asyncio.to_thread(wiki.link_report, record.page.page_id, at=head)
    page = _page_data(report.page)
    static_size = len(json.dumps(page, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) + 1_000
    if static_size >= _MAX_LINK_DATA_BYTES:
        return ToolResult.failure(
            code="result_too_large",
            message="The wiki page identity exceeds the link-result budget.",
            preview="Wiki page identity too large",
            recovery_action="Read the page directly; report malformed page metadata if this persists.",
        )
    remaining = _MAX_LINK_DATA_BYTES - static_size
    paths = {item.page.page_id: item.resource.path for item in report.pages}
    outgoing_budget = remaining // 2 if report.backlinks else remaining
    outgoing, outgoing_size, outgoing_fields_truncated = _bounded_links(
        report.outgoing,
        paths=paths,
        limit=args.limit,
        budget=outgoing_budget,
    )
    backlinks, _backlinks_size, backlink_fields_truncated = _bounded_links(
        report.backlinks,
        paths=paths,
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
                recovery_action="Read the page directly and inspect a narrower set of links.",
            )
        data["links_truncated"] = True
    return ToolResult(
        content=f"{len(report.outgoing)} outgoing links; {len(report.backlinks)} backlinks.",
        preview=report.page.page.title,
        source_refs=(_page_ref(report.page),),
        data=data,
    )


async def wiki_create_page(execution: ToolExecution, args: WikiCreatePageInput) -> ToolResult:
    wiki = _wiki(execution)
    if isinstance(wiki, ToolResult):
        return wiki
    if error := _automation_path_error(execution, args.path):
        return error
    if args.path.casefold() == WIKI_HEALTH_PATH:
        return _invalid_page(WikiValidationError("health page is backend-managed"))
    body = args.body.encode("utf-8")
    try:
        snapshot = await asyncio.to_thread(wiki.snapshot)
        current = next((record for record in snapshot.pages if record.resource.path == args.path), None)
        if current is not None:
            desired = build_page(
                page_id=current.page.page_id,
                title=args.title,
                aliases=tuple(args.aliases),
                body=body,
            ).to_bytes()
            if current.content == desired:
                _observe_page(execution, current, snapshot.head, content_read=True)
                return ToolResult(
                    content=f"{args.path} already has the requested content.",
                    preview="Wiki page unchanged",
                    source_refs=(_page_ref(current),),
                    data={
                        "page": _page_data(current),
                        "changed": False,
                        "projection_pending": False,
                    },
                )
            return ToolResult.failure(
                code="name_conflict",
                message=f"A wiki page already exists at {args.path!r} with different content.",
                preview="Wiki path already exists",
                recovery_action="Read the existing page, choose another path, or edit that page instead.",
            )
        activated_readme_paths = _activated_directory_readme_paths(snapshot, args.path)
        actor, origin = _write_identity(execution)
        created = await asyncio.to_thread(
            wiki.create_page,
            path=args.path,
            title=args.title,
            aliases=tuple(args.aliases),
            body=body,
            expected_head=snapshot.head,
            actor=actor,
            origin=origin,
            reason=f"create wiki page {args.path}",
        )
    except RevisionConflictError:
        return _revision_conflict()
    except WikiAmbiguityError:
        return ToolResult.failure(
            code="name_conflict",
            message="The requested path, title, or alias is already in use.",
            preview="Wiki name already exists",
            recovery_action="List the target directory, choose a unique path/title/alias, and retry.",
        )
    except (PageValidationError, WikiValidationError) as error:
        return _invalid_page(error)

    projection_pending = await execution.ctx.services[WIKI_POST_COMMIT_SERVICE]()
    head = wiki.repository.head
    _observe_page(execution, created, head, content_read=True)
    message = f"Created {created.resource.path}."
    if activated_readme_paths:
        readmes = ", ".join(activated_readme_paths)
        message += (
            f" Created or restored directory contracts: {readmes}. Read them now; replace any bootstrap text with "
            "the exact purpose, producers, consumers, boundaries, and retention before finishing."
        )
    return ToolResult(
        content=message,
        preview="Wiki page created",
        source_refs=(_page_ref(created),),
        data={
            "page": _page_data(created),
            "changed": True,
            "projection_pending": projection_pending,
        },
    )


async def preflight_wiki_edit_page(execution: ToolExecution, args: WikiEditPageInput) -> ToolResult | None:
    wiki = _wiki(execution)
    if isinstance(wiki, ToolResult):
        return wiki
    snapshot = await asyncio.to_thread(wiki.snapshot)
    record = _active_page(snapshot, args.path)
    if record is None:
        return _active_page_not_found(args.path)
    if error := _automation_path_error(execution, record.resource.path):
        return error
    if error := _producer_page_error(execution, record):
        return error
    if record.page.page_id == WIKI_HEALTH_RESOURCE_ID:
        return _invalid_page(WikiValidationError("health page is backend-managed"))
    replacement = record.page.with_body(args.body.encode("utf-8")).to_bytes()
    if replacement == record.content:
        return None
    observation = _require_page_observation(execution, record.page.page_id, content_read=True)
    if isinstance(observation, ToolResult):
        return observation
    if record.resource.version_id != observation.version:
        return _revision_conflict()
    return None


async def wiki_edit_page(execution: ToolExecution, args: WikiEditPageInput) -> ToolResult:
    wiki = _wiki(execution)
    if isinstance(wiki, ToolResult):
        return wiki
    try:
        snapshot = await asyncio.to_thread(wiki.snapshot)
        record = _active_page(snapshot, args.path)
        if record is None:
            return _active_page_not_found(args.path)
        if error := _automation_path_error(execution, record.resource.path):
            return error
        if error := _producer_page_error(execution, record):
            return error
        replacement = record.page.with_body(args.body.encode("utf-8")).to_bytes()
        if replacement == record.content:
            _observe_page(execution, record, snapshot.head, content_read=True)
            return _unchanged_page(record)
        observation = _require_page_observation(execution, record.page.page_id, content_read=True)
        if isinstance(observation, ToolResult):
            return observation
        actor, origin = _write_identity(execution)
        updated, _commit_id = await asyncio.to_thread(
            wiki.update_page_with_commit,
            record.page.page_id,
            content=replacement,
            expected_version=observation.version,
            actor=actor,
            origin=origin,
            reason=f"edit wiki page {record.resource.path}",
        )
    except RevisionConflictError:
        return _revision_conflict()
    except (PageValidationError, WikiValidationError) as error:
        return _invalid_page(error)

    projection_pending = await execution.ctx.services[WIKI_POST_COMMIT_SERVICE]()
    _observe_page(execution, updated, wiki.repository.head, content_read=True)
    return ToolResult(
        content=f"Updated {updated.resource.path}.",
        preview="Wiki page updated",
        source_refs=(_page_ref(updated),),
        data={
            "page": _page_data(updated),
            "changed": True,
            "projection_pending": projection_pending,
        },
    )


async def preflight_wiki_patch_page(execution: ToolExecution, args: WikiPatchPageInput) -> ToolResult | None:
    wiki = _wiki(execution)
    if isinstance(wiki, ToolResult):
        return wiki
    snapshot = await asyncio.to_thread(wiki.snapshot)
    record = _active_page(snapshot, args.path)
    if record is None:
        return _active_page_not_found(args.path)
    if error := _automation_path_error(execution, record.resource.path):
        return error
    if error := _producer_page_error(execution, record):
        return error
    if record.page.page_id == WIKI_HEALTH_RESOURCE_ID:
        return _invalid_page(WikiValidationError("health page is backend-managed"))
    patched = _patch_body(record, args.old_text, args.new_text)
    if isinstance(patched, ToolResult):
        return patched
    if patched.encode("utf-8") == record.page.body:
        return None
    return None


async def wiki_patch_page(execution: ToolExecution, args: WikiPatchPageInput) -> ToolResult:
    wiki = _wiki(execution)
    if isinstance(wiki, ToolResult):
        return wiki
    try:
        snapshot = await asyncio.to_thread(wiki.snapshot)
        record = _active_page(snapshot, args.path)
        if record is None:
            return _active_page_not_found(args.path)
        if error := _automation_path_error(execution, record.resource.path):
            return error
        if error := _producer_page_error(execution, record):
            return error
        if record.page.page_id == WIKI_HEALTH_RESOURCE_ID:
            return _invalid_page(WikiValidationError("health page is backend-managed"))
        patched = _patch_body(record, args.old_text, args.new_text)
        if isinstance(patched, ToolResult):
            return patched
        if patched.encode("utf-8") == record.page.body:
            return _unchanged_page(record)
        replacement = record.page.with_body(patched.encode("utf-8")).to_bytes()
        actor, origin = _write_identity(execution)
        updated, _commit_id = await asyncio.to_thread(
            wiki.update_page_with_commit,
            record.page.page_id,
            content=replacement,
            expected_version=record.resource.version_id,
            actor=actor,
            origin=origin,
            reason=f"patch wiki page {record.resource.path}",
        )
    except RevisionConflictError:
        return _revision_conflict()
    except (PageValidationError, WikiValidationError, UnicodeError) as error:
        return _invalid_page(error)

    projection_pending = await execution.ctx.services[WIKI_POST_COMMIT_SERVICE]()
    prior = execution.ctx.run.resource_observation(wiki_page_observation_id(record.page.page_id))
    _observe_page(
        execution,
        updated,
        wiki.repository.head,
        content_read=False if prior is None else prior.content_read,
    )
    return ToolResult(
        content=f"Patched {updated.resource.path}.",
        preview="Wiki page patched",
        source_refs=(_page_ref(updated),),
        data={
            "page": _page_data(updated),
            "changed": True,
            "projection_pending": projection_pending,
        },
    )


async def wiki_archive_page(execution: ToolExecution, args: WikiArchivePageInput) -> ToolResult:
    wiki = _wiki(execution)
    if isinstance(wiki, ToolResult):
        return wiki
    try:
        snapshot = await asyncio.to_thread(wiki.snapshot)
        record = next((item for item in snapshot.pages if item.resource.path == args.path), None)
        if record is None:
            resource = await asyncio.to_thread(
                wiki.repository.find_by_path,
                args.path,
                at=snapshot.head,
                include_archived=True,
            )
            if resource is None:
                return ToolResult.failure(
                    code="not_found",
                    message=f"No wiki page exists at {args.path!r}.",
                    preview="Wiki page not found",
                    recovery_action="Call wiki_list_pages and retry with an exact page path.",
                )
            if resource.state is not ResourceState.ARCHIVED:
                return ToolResult.failure(
                    code="not_found",
                    message=f"No active wiki page exists at {args.path!r}.",
                    preview="Wiki page not found",
                    recovery_action="Call wiki_list_pages and retry with an exact active page path.",
                )
            if error := _automation_path_error(execution, resource.path):
                return error
            return ToolResult(
                content=f"{resource.path} is already archived.",
                preview="Wiki page unchanged",
                data={
                    "page": _archived_page_data(wiki, resource),
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
        observation = _require_page_observation(execution, record.page.page_id, content_read=False)
        if isinstance(observation, ToolResult):
            return observation
        if record.resource.version_id != observation.version:
            return _revision_conflict()
        actor, origin = _write_identity(execution)
        await asyncio.to_thread(
            wiki.archive_page,
            record.page.page_id,
            expected_version=observation.version,
            base_head=snapshot.head,
            actor=actor,
            origin=origin,
            reason=args.reason,
        )
    except RevisionConflictError:
        return _revision_conflict()
    except WikiValidationError as error:
        return _invalid_page(error)

    resource = await asyncio.to_thread(wiki.repository.get, record.page.page_id, at=wiki.repository.head)
    projection_pending = await execution.ctx.services[WIKI_POST_COMMIT_SERVICE]()
    execution.ctx.run.observe_resource(
        wiki_page_observation_id(record.page.page_id),
        version=resource.version_id,
        container_version=wiki.repository.head,
        content_read=False,
    )
    return ToolResult(
        content=f"Archived {resource.path}.",
        preview="Wiki page archived",
        data={
            "page": _archived_page_data(wiki, resource),
            "changed": True,
            "projection_pending": projection_pending,
        },
    )


async def wiki_move_page(execution: ToolExecution, args: WikiMovePageInput) -> ToolResult:
    wiki = _wiki(execution)
    if isinstance(wiki, ToolResult):
        return wiki
    try:
        snapshot = await asyncio.to_thread(wiki.snapshot)
        record = next(
            (item for item in snapshot.pages if item.resource.path == args.path and item.page.lifecycle == "active"),
            None,
        )
        if record is None:
            destination = next(
                (
                    item
                    for item in snapshot.pages
                    if item.resource.path == args.new_path and item.page.lifecycle == "active"
                ),
                None,
            )
            if destination is not None:
                history = await asyncio.to_thread(
                    wiki.repository.history,
                    resource_id=destination.page.page_id,
                )
                already_moved = any(
                    change.action == "move"
                    and change.before is not None
                    and change.after is not None
                    and change.before.path == args.path
                    and change.after.path == args.new_path
                    for commit in history
                    for change in commit.changes
                )
                if already_moved:
                    if error := _automation_path_error(execution, args.path):
                        return error
                    if error := _automation_path_error(execution, args.new_path):
                        return error
                    _observe_page(execution, destination, snapshot.head, content_read=False)
                    return ToolResult(
                        content=f"{args.path} was already moved to {args.new_path}.",
                        preview="Wiki page unchanged",
                        source_refs=(_page_ref(destination),),
                        data={
                            "page": _page_data(destination),
                            "changed": False,
                            "projection_pending": False,
                        },
                    )
            return ToolResult.failure(
                code="not_found",
                message=f"No active wiki page exists at {args.path!r}.",
                preview="Wiki page not found",
                recovery_action="Call wiki_list_pages or wiki_read_page and retry with an exact active page path.",
            )
        if record.resource.path == args.new_path:
            _observe_page(execution, record, snapshot.head, content_read=False)
            return ToolResult(
                content=f"{record.resource.path} is already at the requested path.",
                preview="Wiki page unchanged",
                source_refs=(_page_ref(record),),
                data={"page": _page_data(record), "changed": False, "projection_pending": False},
            )
        if error := _automation_path_error(execution, record.resource.path):
            return error
        if error := _automation_path_error(execution, args.new_path):
            return error
        if error := _producer_page_error(execution, record):
            return error
        if record.page.page_id == WIKI_HEALTH_RESOURCE_ID:
            return _invalid_page(WikiValidationError("health page is backend-managed"))
        sessions: SessionService = execution.ctx.services["session"]
        area = await sessions.find_area_by_page_id(record.page.page_id)
        if area is not None:
            return ToolResult.failure(
                code="area_page_bound",
                message=f"Wiki page {record.resource.path} is bound to Area {area['name']!r}.",
                preview="Area page must move with its Area",
                recovery_action="Rename the Area instead; it moves the bound page and keeps the custodian synchronized.",
            )
        observation = _require_page_observation(execution, record.page.page_id, content_read=False)
        if isinstance(observation, ToolResult):
            return observation
        if record.resource.version_id != observation.version:
            return _revision_conflict()
        activated_readme_paths = _activated_directory_readme_paths(snapshot, args.new_path)
        actor, origin = _write_identity(execution)
        moved, commit_id = await asyncio.to_thread(
            wiki.move_page,
            record.page.page_id,
            new_path=args.new_path,
            expected_version=observation.version,
            expected_head=snapshot.head,
            actor=actor,
            origin=origin,
            reason=f"move wiki page {record.resource.path} to {args.new_path}",
        )
    except RevisionConflictError:
        return _revision_conflict()
    except WikiAmbiguityError:
        return ToolResult.failure(
            code="name_conflict",
            message="The destination path or another page name is already in use.",
            preview="Wiki path already exists",
            recovery_action="List the destination directory and retry with an unused path.",
        )
    except (PageValidationError, WikiValidationError) as error:
        return _invalid_page(error)

    projection_pending = await execution.ctx.services[WIKI_POST_COMMIT_SERVICE]() if commit_id is not None else False
    _observe_page(execution, moved, wiki.repository.head, content_read=observation.content_read)
    message = (
        f"Moved {record.resource.path} to {moved.resource.path}."
        if commit_id is not None
        else f"{moved.resource.path} is already at the requested path."
    )
    if commit_id is not None and activated_readme_paths:
        readmes = ", ".join(activated_readme_paths)
        message += (
            f" Created or restored directory contracts: {readmes}. Read them now; replace any bootstrap text with "
            "the exact purpose, producers, consumers, boundaries, and retention before finishing."
        )
    return ToolResult(
        content=message,
        preview="Wiki page moved" if commit_id is not None else "Wiki page unchanged",
        source_refs=(_page_ref(moved),),
        data={
            "page": _page_data(moved),
            "changed": commit_id is not None,
            "projection_pending": projection_pending,
        },
    )


async def approve_wiki_create_page(
    _execution: ToolExecution,
    args: WikiCreatePageInput,
) -> ApprovalInfo:
    return ApprovalInfo(
        description=f"Create wiki page {args.path}",
        preview=args.body[:1_500],
        diff=f"Title: {args.title}\nPath: {args.path}",
    )


async def approve_wiki_edit_page(
    _execution: ToolExecution,
    args: WikiEditPageInput,
) -> ApprovalInfo:
    return ApprovalInfo(
        description=f"Replace the body of wiki page {args.path}",
        preview=args.body[:1_500],
        diff="Replace the current page body.",
    )


async def approve_wiki_patch_page(
    _execution: ToolExecution,
    args: WikiPatchPageInput,
) -> ApprovalInfo:
    diff = f"Replace exact text in {args.path}:\n- {args.old_text}\n+ {args.new_text}"
    return ApprovalInfo(
        description=f"Patch wiki page {args.path}",
        preview=args.new_text[:1_500],
        diff=diff[:12_000],
    )


async def approve_wiki_archive_page(
    _execution: ToolExecution,
    args: WikiArchivePageInput,
) -> ApprovalInfo:
    return ApprovalInfo(
        description=f"Archive wiki page {args.path}",
        preview=args.reason,
        diff="Archive the current page.",
    )


async def approve_wiki_move_page(
    _execution: ToolExecution,
    args: WikiMovePageInput,
) -> ApprovalInfo:
    return ApprovalInfo(
        description=f"Move wiki page {args.path} to {args.new_path}",
        preview="Wiki links to this page will be rewritten to the new path.",
        diff="Move the current page and update resolved path-style links.",
    )


async def wiki_publish_generated(execution: ToolExecution, args: WikiPublishGeneratedInput) -> ToolResult:
    wiki = _wiki(execution)
    if isinstance(wiki, ToolResult):
        return wiki
    generated = args.generated.encode("utf-8")
    if generated and not generated.endswith(b"\n"):
        generated += b"\n"
    try:
        snapshot = await asyncio.to_thread(wiki.snapshot)
        record = next(
            (page for page in snapshot.pages if page.resource.path == args.path and page.page.lifecycle == "active"),
            None,
        )
        if record is None:
            return ToolResult.failure(
                code="not_found",
                message=f"No active wiki page exists at {args.path!r}.",
                preview="Wiki page not found",
                recovery_action="Call wiki_list_pages or wiki_read_page and retry with an exact active page path.",
            )
        if not record.resource.path.startswith((AUTOMATIONS_PATH_PREFIX, "insights/")) or "fact_citations" in (
            record.page.metadata
        ):
            return ToolResult.failure(
                code="not_producer_page",
                message="Only registered automation or insight producer pages can be updated with this tool.",
                preview="Page is not producer-owned",
                recovery_action="Use wiki_edit_page for an ordinary page, or select a registered producer page.",
            )
        automation_id = execution.ctx.run.automation_id
        if automation_id is None:
            return ToolResult.failure(
                code="automation_required",
                message="Generated wiki publishing is available only to a scheduled automation run.",
                preview="Automation required",
                recovery_action="Use wiki_edit_page interactively, or run the owning scheduled automation.",
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
                recovery_action="Run the registered owning automation, or publish to a page owned by this automation.",
            )
        if extract_generated_region(record.content, expected_page_id=record.page.page_id) == generated:
            _observe_page(execution, record, snapshot.head, content_read=True)
            return ToolResult(
                content=f"{record.resource.path} already has that generated content.",
                preview="Wiki page unchanged",
                source_refs=(_page_ref(record),),
                data={
                    "page": _page_data(record),
                    "changed": False,
                    "projection_pending": False,
                },
            )
        observation = _require_page_observation(execution, record.page.page_id, content_read=True)
        if isinstance(observation, ToolResult):
            return observation
        if record.resource.version_id != observation.version:
            return _revision_conflict()
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
    except RevisionConflictError:
        return ToolResult.failure(
            code="revision_conflict",
            message="The wiki page changed after it was read.",
            preview="Wiki page changed",
            retryable=True,
            recovery_action="Read the page again, then retry.",
        )
    except GeneratedRegionConflictError as exc:
        return ToolResult.failure(
            code="generated_region_conflict",
            message=str(exc),
            preview="Generated region changed",
            recovery_action="Read the page again; do not overwrite user changes.",
        )
    except WikiValidationError as exc:
        return ToolResult.failure(
            code="invalid_page",
            message=str(exc),
            preview="Invalid wiki page",
            recovery_action="Read the page again and retry with valid generated Markdown.",
        )

    published_head = commit.commit_id if commit is not None else snapshot.head
    updated = await asyncio.to_thread(wiki.read_page, record.page.page_id, at=published_head)
    projection_pending = await execution.ctx.services[WIKI_POST_COMMIT_SERVICE]() if commit is not None else False
    current_head = wiki.repository.head
    _observe_page(execution, updated, current_head, content_read=True)
    return ToolResult(
        content=f"Updated the generated region in {updated.resource.path}.",
        preview="Wiki page updated",
        source_refs=(_page_ref(updated),),
        data={
            "page": _page_data(updated),
            "changed": commit is not None,
            "projection_pending": projection_pending,
        },
    )


async def approve_wiki_publish_generated(
    _execution: ToolExecution,
    args: WikiPublishGeneratedInput,
) -> ApprovalInfo:
    return ApprovalInfo(
        description=f"Update generated wiki content for {args.path}",
        preview=args.generated[:1_500],
        diff="Update the current generated content.",
    )


wiki_list_pages_tool = tool(
    display_name="ListWikiPages",
    display_description="List one managed wiki directory.",
    description="List direct child directories and active pages in one managed wiki directory.",
    input_model=WikiListPagesInput,
    policy=ToolPolicy(
        action=ToolAction.READ,
        scope=ToolScope.INTERNAL,
        permissions=_WIKI_PERMISSION,
        max_result_chars=_MAX_LIST_DATA_BYTES,
        deferred=True,
    ),
    execute=wiki_list_pages,
)

wiki_list_changes_tool = tool(
    display_name="ListWikiChanges",
    display_description="List managed wiki files changed since a timestamp.",
    description=(
        "List managed wiki commits since an inclusive timestamp, including changed paths, time, actor, and reason. "
        "Use this to understand recent wiki activity without inspecting internal revision identifiers."
    ),
    input_model=WikiListChangesInput,
    policy=ToolPolicy(
        action=ToolAction.READ,
        scope=ToolScope.INTERNAL,
        permissions=_WIKI_PERMISSION,
        max_result_chars=_MAX_LIST_DATA_BYTES,
        deferred=True,
    ),
    execute=wiki_list_changes,
)

wiki_create_page_tool = tool(
    display_name="CreateWikiPage",
    display_description="Create one managed wiki page.",
    description=(
        "Create one common managed wiki page from a path, title, aliases, and Markdown body. "
        "The path is the page's identity; the title is display-only. In the body, link other pages "
        "by path ([[topics/acme]] or [[topics/acme|Acme]]) — bare title links do not resolve. "
        "Missing ancestor directories receive semantic README.md contracts in the same commit. "
        "Read and specialize any reported bootstrap README before finishing. "
        f"Scheduled automations can create pages only under {AUTOMATIONS_PATH_PREFIX}."
    ),
    input_model=WikiCreatePageInput,
    policy=ToolPolicy(
        action=ToolAction.WRITE,
        scope=ToolScope.INTERNAL,
        requires_approval=True,
        permissions=_WIKI_WRITE_PERMISSIONS,
        max_result_chars=4_000,
        destructive=False,
        idempotent=True,
        deferred=True,
    ),
    approval=approve_wiki_create_page,
    execute=wiki_create_page,
)

wiki_edit_page_tool = tool(
    display_name="EditWikiPage",
    display_description="Replace one managed wiki page body.",
    description=(
        "FULL-BODY REPLACEMENT. Required prerequisite: call wiki_read_page for the exact path first. "
        "That read remains valid across later turns while the page version is unchanged; a conflict requires rereading. "
        "For a localized exact-text change, prefer wiki_patch_page instead. Replace only the Markdown body while "
        "preserving identity and metadata. Link other pages by path ([[topics/acme|Acme]]); bare title links do not resolve. "
        f"Scheduled automations can edit pages only under {AUTOMATIONS_PATH_PREFIX}."
    ),
    input_model=WikiEditPageInput,
    policy=ToolPolicy(
        action=ToolAction.WRITE,
        scope=ToolScope.INTERNAL,
        requires_approval=True,
        permissions=_WIKI_WRITE_PERMISSIONS,
        max_result_chars=4_000,
        destructive=False,
        idempotent=True,
        deferred=True,
    ),
    preflight=preflight_wiki_edit_page,
    approval=approve_wiki_edit_page,
    execute=wiki_edit_page,
)

wiki_patch_page_tool = tool(
    display_name="PatchWikiPage",
    display_description="Replace one exact text block in a managed wiki page.",
    description=(
        "Apply one localized exact-text replacement to an active managed wiki page body. A prior full read is not "
        "required, but old_text must match the current body exactly once. The runtime rechecks current content and "
        "uses compare-and-swap, preserving unrelated changes and rejecting conflicts. Use wiki_edit_page only for a "
        "full-body replacement after wiki_read_page. "
        f"Scheduled automations can patch pages only under {AUTOMATIONS_PATH_PREFIX}."
    ),
    input_model=WikiPatchPageInput,
    policy=ToolPolicy(
        action=ToolAction.WRITE,
        scope=ToolScope.INTERNAL,
        requires_approval=True,
        permissions=_WIKI_WRITE_PERMISSIONS,
        max_result_chars=4_000,
        destructive=False,
        idempotent=True,
        deferred=True,
    ),
    preflight=preflight_wiki_patch_page,
    approval=approve_wiki_patch_page,
    execute=wiki_patch_page,
)

wiki_archive_page_tool = tool(
    display_name="ArchiveWikiPage",
    display_description="Archive one managed wiki page.",
    description=(
        "Recoverably archive one active managed wiki page; this never hard-deletes its history. "
        "Read the page first. "
        f"Scheduled automations can archive pages only under {AUTOMATIONS_PATH_PREFIX}."
    ),
    input_model=WikiArchivePageInput,
    policy=ToolPolicy(
        action=ToolAction.WRITE,
        scope=ToolScope.INTERNAL,
        requires_approval=True,
        permissions=_WIKI_WRITE_PERMISSIONS,
        max_result_chars=4_000,
        destructive=True,
        idempotent=True,
        deferred=True,
    ),
    approval=approve_wiki_archive_page,
    execute=wiki_archive_page,
)

wiki_move_page_tool = tool(
    display_name="MoveWikiPage",
    display_description="Move one managed wiki page without renaming it.",
    description=(
        "Move one active managed wiki page to a new path without changing its identity, title, aliases, body, or metadata. "
        "Missing destination directory README.md contracts are created in the same commit. "
        "Read and specialize any reported bootstrap README before finishing. "
        "Wikilinks that resolve to the page are rewritten to the new path atomically. "
        "Read the page first."
    ),
    input_model=WikiMovePageInput,
    policy=ToolPolicy(
        action=ToolAction.WRITE,
        scope=ToolScope.INTERNAL,
        requires_approval=True,
        permissions=_WIKI_MOVE_PERMISSIONS,
        max_result_chars=4_000,
        destructive=False,
        idempotent=True,
        deferred=True,
    ),
    approval=approve_wiki_move_page,
    execute=wiki_move_page,
)

wiki_read_page_tool = tool(
    display_name="ReadWikiPage",
    display_description="Read one managed wiki page.",
    description="Read one active managed wiki page by its exact path.",
    input_model=WikiReadPageInput,
    policy=ToolPolicy(
        action=ToolAction.READ,
        scope=ToolScope.INTERNAL,
        permissions=_WIKI_PERMISSION,
        max_result_chars=_MAX_CONTENT_CHARS,
        deferred=True,
    ),
    execute=wiki_read_page,
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
        deferred=True,
    ),
    execute=wiki_links,
)

wiki_publish_generated_tool = tool(
    display_name="PublishWikiGenerated",
    display_description="Update one page's generated region.",
    description=("Update only the generated region of one existing managed wiki page. Read the page first."),
    input_model=WikiPublishGeneratedInput,
    policy=ToolPolicy(
        action=ToolAction.WRITE,
        scope=ToolScope.INTERNAL,
        requires_approval=True,
        permissions=_WIKI_WRITE_PERMISSIONS,
        max_result_chars=4_000,
        destructive=False,
        idempotent=False,
        deferred=True,
    ),
    approval=approve_wiki_publish_generated,
    execute=wiki_publish_generated,
)
