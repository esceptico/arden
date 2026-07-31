"""Canonical fact and managed-wiki HTTP surfaces."""

import asyncio
import unicodedata
from collections.abc import Mapping
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from arden.logging import get_logger
from arden.memory.facts.models import (
    FactConflictError,
    FactLedgerCorruptionError,
    FactValidationError,
)
from arden.memory.facts.plan_store import (
    FactPlanCorruptionError,
    FactPlanOwnershipError,
    FactPlanRequestConflictError,
)
from arden.memory.facts.service import (
    BATCH_FACT_LIMIT,
    FactPrincipal,
    FactScopeError,
    FactService,
)
from arden.revisions.errors import CorruptRepositoryError, RevisionConflictError, UnsafePathError
from arden.server.runtime import get_runtime
from arden.wiki.pages import page_excerpt
from arden.wiki.service import WikiService, WikiValidationError

wiki_router = APIRouter(prefix="/admin/wiki", tags=["wiki"])
facts_router = APIRouter(prefix="/admin/facts", tags=["facts"])
_logger = get_logger(__name__)


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WikiCreateRequest(_Request):
    path: str = Field(min_length=1, max_length=4096)
    title: str = Field(min_length=1, max_length=4096)
    body: str = ""
    page_id: str | None = Field(default=None, min_length=1, max_length=512)
    aliases: list[str] = Field(default_factory=list, max_length=100)
    metadata: dict[str, Any] = Field(default_factory=dict)
    expected_head: str | None


class WikiUpdateRequest(_Request):
    content: str
    expected_version: str = Field(min_length=1, max_length=128)
    expected_head: str = Field(min_length=1, max_length=128)


class WikiVersionRequest(_Request):
    expected_version: str = Field(min_length=1, max_length=128)
    expected_head: str = Field(min_length=1, max_length=128)


class WikiMaintenanceRestoreRequest(_Request):
    expected_version: str = Field(min_length=1, max_length=128)
    expected_head: str = Field(min_length=1, max_length=128)


class PinnedFactRequest(_Request):
    request_id: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=20_000)


class FactBatchReadRequest(_Request):
    fact_ids: list[str] = Field(min_length=1, max_length=BATCH_FACT_LIMIT)


def _json_value(value: object) -> object:
    """Recursively turn immutable domain values into ordinary JSON values."""

    if isinstance(value, Enum):
        return value.value
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.decode("utf-8")
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    if is_dataclass(value):
        return _json_value(asdict(value))
    raise TypeError(f"cannot serialize {type(value).__name__}")


def _wiki_service(request: Request) -> WikiService:
    service = get_runtime(request).wiki_service
    if service is None:
        raise HTTPException(status_code=503, detail="wiki service not ready")
    return service


def _fact_service(request: Request) -> FactService:
    service = get_runtime(request).fact_service
    if service is None:
        raise HTTPException(status_code=503, detail="fact service not ready")
    return service


def _page_timestamps(
    service: WikiService,
    head: str | None,
    page_ids: set[str],
) -> dict[str, tuple[datetime | None, datetime | None]]:
    """Derive page creation/update times from canonical commit history."""

    result: dict[str, list[datetime | None]] = {page_id: [None, None] for page_id in page_ids}
    if head is None or not page_ids:
        return dict.fromkeys(page_ids, (None, None))
    for commit in service.repository.history(start=head):
        for change in commit.changes:
            version = change.after or change.before
            if version is None or version.resource_id not in result:
                continue
            times = result[version.resource_id]
            if times[1] is None:
                times[1] = commit.timestamp
            if change.action == "create":
                times[0] = commit.timestamp
        if all(created is not None and updated is not None for created, updated in result.values()):
            break
    return {page_id: (times[0], times[1]) for page_id, times in result.items()}


def _page_json(
    record,
    head: str | None,
    *,
    content: bool,
    timestamps: Mapping[str, tuple[datetime | None, datetime | None]] | None = None,
) -> dict[str, object]:
    created_at, updated_at = (None, None) if timestamps is None else timestamps.get(record.page.page_id, (None, None))
    result: dict[str, object] = {
        "page_id": record.page.page_id,
        "path": record.resource.path,
        "resource_state": record.resource.state.value,
        "title": record.page.title,
        # The list response omits content, so rows have nothing to preview
        # without this; it is the same first prose block the hover card shows.
        "excerpt": page_excerpt(record.page.body),
        "aliases": list(record.page.aliases),
        "lifecycle": record.page.lifecycle,
        "redirect_to": record.page.redirect_to,
        "metadata": _json_value(record.page.metadata),
        "version": record.resource.version_id,
        "repository_head": head,
        "created_at": None if created_at is None else created_at.isoformat(),
        "updated_at": None if updated_at is None else updated_at.isoformat(),
    }
    if content:
        result["content"] = record.content.decode("utf-8")
    return result


def _page_snapshot(service: WikiService, page_id: str):
    snapshot = service.snapshot()
    record = next((item for item in snapshot.pages if item.page.page_id == page_id), None)
    if record is None:
        raise KeyError(f"unknown wiki page: {page_id}")
    return snapshot.head, record


def _wiki_conflict(service: WikiService, page_id: str, exc: RevisionConflictError) -> HTTPException:
    detail: dict[str, object] = {"error": "page_revision_conflict", "message": str(exc)}
    try:
        head = service.repository.head
        resource = service.repository.get(page_id, at=head)
        detail.update(
            current_content=service.repository.read_version(resource).decode("utf-8"),
            current_revision=resource.version_id,
            current_head=head,
        )
    except (KeyError, UnicodeDecodeError, CorruptRepositoryError):
        pass
    return HTTPException(status_code=409, detail=detail)


def _wiki_error(service: WikiService, page_id: str, exc: Exception) -> HTTPException:
    if isinstance(exc, RevisionConflictError):
        return _wiki_conflict(service, page_id, exc)
    if isinstance(exc, KeyError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, CorruptRepositoryError):
        return HTTPException(status_code=503, detail="wiki repository is corrupt")
    if isinstance(exc, (WikiValidationError, UnsafePathError, UnicodeError, TypeError, ValueError)):
        return HTTPException(status_code=422, detail=str(exc))
    raise exc


async def _project_committed_wiki_change(request: Request, revision: str | None) -> bool:
    if revision is None:
        return False
    try:
        return await get_runtime(request).project_wiki_change_after_commit()
    except Exception:
        _logger.exception("Wiki projection enqueue failed after a committed user change", revision=revision)
        return True


@wiki_router.get("/pages")
def list_wiki_pages(
    request: Request,
    include_redirects: bool = Query(default=False),
) -> dict[str, object]:
    service = _wiki_service(request)
    try:
        snapshot = service.snapshot()
        timestamps = _page_timestamps(service, snapshot.head, {record.page.page_id for record in snapshot.pages})
        pages = [
            _page_json(record, snapshot.head, content=False, timestamps=timestamps)
            for record in snapshot.pages
            if include_redirects or record.page.lifecycle == "active"
        ]
        return {"repository_head": snapshot.head, "pages": pages}
    except Exception as exc:
        raise _wiki_error(service, "", exc) from exc


@wiki_router.get("/pages/{page_id}")
def read_wiki_page(page_id: str, request: Request) -> dict[str, object]:
    service = _wiki_service(request)
    try:
        head, record = _page_snapshot(service, page_id)
        return _page_json(
            record,
            head,
            content=True,
            timestamps=_page_timestamps(service, head, {page_id}),
        )
    except Exception as exc:
        raise _wiki_error(service, page_id, exc) from exc


@wiki_router.post("/pages", status_code=201)
async def create_wiki_page(body: WikiCreateRequest, request: Request) -> dict[str, object]:
    service = _wiki_service(request)
    try:
        actual_head = service.repository.head
        if actual_head != body.expected_head:
            raise RevisionConflictError(f"current head changed: expected {body.expected_head!r}, found {actual_head!r}")
        created = service.create_page(
            path=body.path,
            title=body.title,
            body=body.body.encode("utf-8"),
            page_id=body.page_id,
            aliases=tuple(body.aliases),
            metadata=body.metadata,
            expected_head=body.expected_head,
            actor="user:desktop",
            origin="desktop",
            reason="create wiki page",
        )
        head, record = _page_snapshot(service, created.page.page_id)
        projection_pending = await _project_committed_wiki_change(request, head)
        result = _page_json(
            record,
            head,
            content=True,
            timestamps=_page_timestamps(service, head, {record.page.page_id}),
        )
        if projection_pending:
            result["projection_pending"] = True
        return result
    except Exception as exc:
        raise _wiki_error(service, body.page_id or "", exc) from exc


@wiki_router.put("/pages/{page_id}")
async def update_wiki_page(page_id: str, body: WikiUpdateRequest, request: Request) -> dict[str, object]:
    runtime = get_runtime(request)
    service = _wiki_service(request)
    try:
        try:
            runtime.require_wiki_user_edit_queue()
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        _record, commit_id = await asyncio.to_thread(
            service.update_page_with_commit,
            page_id,
            content=body.content.encode("utf-8"),
            expected_version=body.expected_version,
            expected_head=body.expected_head,
        )
        if commit_id is not None:
            try:
                await runtime.enqueue_wiki_user_edit(commit_id)
                curation_pending = False
            except Exception:
                curation_pending = True
                _logger.exception(
                    "Wiki edit committed but curator enqueue failed; startup reconciliation will retry",
                    commit_id=commit_id,
                )
            projection_pending = await _project_committed_wiki_change(request, commit_id)
        else:
            curation_pending = False
            projection_pending = False
        head, record = _page_snapshot(service, page_id)
        result = _page_json(
            record,
            head,
            content=True,
            timestamps=_page_timestamps(service, head, {page_id}),
        )
        if curation_pending:
            result["curation_pending"] = True
        if projection_pending:
            result["projection_pending"] = True
        return result
    except Exception as exc:
        raise _wiki_error(service, page_id, exc) from exc


@wiki_router.post("/pages/{page_id}/archive")
async def archive_wiki_page(page_id: str, body: WikiVersionRequest, request: Request) -> dict[str, object]:
    service = _wiki_service(request)
    try:
        service.archive_page(
            page_id,
            expected_version=body.expected_version,
            base_head=body.expected_head,
            actor="user:desktop",
            origin="desktop",
            reason="archive wiki page",
        )
        head = service.repository.head
        projection_pending = await _project_committed_wiki_change(request, head)
        resource = service.repository.get(page_id, at=head)
        result = {
            "page_id": page_id,
            "path": resource.path,
            "resource_state": resource.state.value,
            "version": resource.version_id,
            "repository_head": head,
        }
        if projection_pending:
            result["projection_pending"] = True
        return result
    except Exception as exc:
        raise _wiki_error(service, page_id, exc) from exc


@wiki_router.post("/pages/{page_id}/restore")
async def restore_wiki_page(page_id: str, body: WikiVersionRequest, request: Request) -> dict[str, object]:
    service = _wiki_service(request)
    try:
        service.restore_page(
            page_id,
            expected_version=body.expected_version,
            base_head=body.expected_head,
            actor="user:desktop",
            origin="desktop",
            reason="restore wiki page",
        )
        head, record = _page_snapshot(service, page_id)
        projection_pending = await _project_committed_wiki_change(request, head)
        result = _page_json(
            record,
            head,
            content=True,
            timestamps=_page_timestamps(service, head, {page_id}),
        )
        if projection_pending:
            result["projection_pending"] = True
        return result
    except Exception as exc:
        raise _wiki_error(service, page_id, exc) from exc


@wiki_router.get("/pages/{page_id}/history")
def wiki_page_history(
    page_id: str,
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, object]:
    service = _wiki_service(request)
    try:
        head = service.repository.head
        service.repository.get(page_id, at=head)
        commits = service.repository.history(resource_id=page_id, start=head, limit=limit)
        return {"page_id": page_id, "repository_head": head, "commits": _json_value(commits)}
    except Exception as exc:
        raise _wiki_error(service, page_id, exc) from exc


@wiki_router.post("/pages/{page_id}/history/{commit_id}/restore")
async def restore_wiki_maintenance_change(
    page_id: str,
    commit_id: str,
    body: WikiMaintenanceRestoreRequest,
    request: Request,
) -> dict[str, object]:
    service = _wiki_service(request)
    try:
        _record, restored_commit_id = await asyncio.to_thread(
            service.restore_maintenance_change,
            page_id,
            commit_id,
            expected_version=body.expected_version,
            expected_head=body.expected_head,
        )
        projection_pending = await _project_committed_wiki_change(request, restored_commit_id)
        head, record = _page_snapshot(service, page_id)
        result = _page_json(
            record,
            head,
            content=True,
            timestamps=_page_timestamps(service, head, {page_id}),
        )
        if projection_pending:
            result["projection_pending"] = True
        return result
    except Exception as exc:
        raise _wiki_error(service, page_id, exc) from exc


@wiki_router.get("/pages/{page_id}/diff")
def wiki_page_diff(
    page_id: str,
    request: Request,
    base: str | None = Query(default=None, max_length=128),
    target: str | None = Query(default=None, max_length=128),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=16_384, ge=1, le=65_536),
    tail: bool = Query(default=False),
) -> dict[str, object]:
    service = _wiki_service(request)
    try:
        target_revision = service.repository.head if target is None else target
        page = service.repository.diff_page(
            base,
            target_revision,
            page_id,
            offset=offset,
            limit=limit,
            tail=tail,
        )
        return {"base": base, "target": target_revision, "diff": _json_value(page)}
    except Exception as exc:
        raise _wiki_error(service, page_id, exc) from exc


@wiki_router.get("/pages/{page_id}/links")
def wiki_page_links(page_id: str, request: Request) -> dict[str, object]:
    service = _wiki_service(request)
    try:
        report = service.link_report(page_id)
        return {
            "page_id": page_id,
            "repository_head": report.head,
            "outgoing": _json_value(report.outgoing),
            "backlinks": _json_value(report.backlinks),
        }
    except Exception as exc:
        raise _wiki_error(service, page_id, exc) from exc


async def _fact_principal(service: FactService) -> FactPrincipal:
    return _fact_principal_for_scopes(await service.known_scopes())


def _fact_principal_for_scopes(scopes: frozenset[tuple[str, str | None]]) -> FactPrincipal:
    return FactPrincipal(
        owner_id="admin:facts",
        readable_scopes=scopes,
        writable_scopes=frozenset(),
    )


def _pinned_principal() -> FactPrincipal:
    scope = ("user", None)
    return FactPrincipal(
        owner_id="user:desktop",
        readable_scopes=frozenset({scope}),
        writable_scopes=frozenset({scope}),
    )


def _normalized_fact_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


async def _existing_pinned_fact(service: FactService, principal: FactPrincipal, text: str):
    normalized = _normalized_fact_text(text)
    matches = await service.search(principal, text, limit=100)
    return next(
        (
            fact
            for fact in matches
            if fact.status == "active"
            and fact.scope == {"kind": "user", "key": None}
            and fact.normalized_text == normalized
        ),
        None,
    )


def _fact_after(created_at: datetime | None, fact_id: str | None):
    if created_at is None and fact_id is None:
        return None
    if created_at is None or fact_id is None:
        raise HTTPException(status_code=422, detail="after_created_at and after_fact_id must be supplied together")
    if created_at.tzinfo is None:
        raise HTTPException(status_code=422, detail="after_created_at must include a timezone")
    return created_at.astimezone(UTC), fact_id


def _fact_error(exc: Exception) -> HTTPException:
    if isinstance(exc, HTTPException):
        return exc
    if isinstance(exc, KeyError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, (FactScopeError, FactPlanOwnershipError)):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, (FactConflictError, FactPlanRequestConflictError)):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, FactValidationError):
        return HTTPException(status_code=422, detail=str(exc))
    if isinstance(exc, (FactLedgerCorruptionError, FactPlanCorruptionError)):
        return HTTPException(status_code=503, detail="fact ledger is corrupt")
    if isinstance(exc, (TypeError, ValueError)):
        return HTTPException(status_code=422, detail=str(exc))
    raise exc


async def _fact_page(
    service: FactService,
    *,
    query: str | None,
    subject: str | None,
    include_inactive: bool,
    limit: int,
    after_created_at: datetime | None,
    after_fact_id: str | None,
) -> dict[str, object]:
    page = await service.search_page(
        await _fact_principal(service),
        query,
        subject=subject,
        include_inactive=include_inactive,
        limit=limit,
        after=_fact_after(after_created_at, after_fact_id),
    )
    next_after = None
    if page.has_more and page.items:
        last = page.items[-1]
        next_after = {"created_at": last.created_at.isoformat(), "fact_id": last.fact_id}
    return {
        "facts": _json_value(page.items),
        "total": page.total,
        "has_more": page.has_more,
        "next_after": next_after,
    }


@facts_router.get("")
async def list_facts(
    request: Request,
    subject: str | None = Query(default=None, min_length=1, max_length=500),
    include_inactive: bool = Query(default=False),
    limit: int = Query(default=100, ge=1, le=100),
    after_created_at: datetime | None = Query(default=None),
    after_fact_id: str | None = Query(default=None, min_length=1, max_length=500),
) -> dict[str, object]:
    service = _fact_service(request)
    try:
        return await _fact_page(
            service,
            query=None,
            subject=subject,
            include_inactive=include_inactive,
            limit=limit,
            after_created_at=after_created_at,
            after_fact_id=after_fact_id,
        )
    except Exception as exc:
        raise _fact_error(exc) from exc


@facts_router.post("", status_code=201)
async def pin_fact(body: PinnedFactRequest, request: Request) -> dict[str, object]:
    """Persist an explicit desktop pin through the canonical fact plan boundary."""

    service = _fact_service(request)
    principal = _pinned_principal()
    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=422, detail="text must not be blank")
    try:
        existing = await _existing_pinned_fact(service, principal, text)
        if existing is not None:
            return {"written": False, "fact_id": existing.fact_id}
        now = datetime.now(UTC).isoformat()
        preview = await service.plan(
            principal,
            [
                {
                    "op": "create",
                    "text": text,
                    "kind": "source",
                    "labels": ["pinned"],
                    "subjects": ["Pinned agent result"],
                    "scope": {"kind": "user", "key": None},
                    "lifecycle": "durable",
                    "certainty": "confirmed",
                    "evidence_class": "direct",
                    "sources": [
                        {
                            "kind": "desktop_action",
                            "ref": body.request_id,
                            "captured_at": now,
                            "scope_kind": "user",
                            "scope_key": None,
                            "role": "user",
                        }
                    ],
                }
            ],
            request_key=f"desktop-pin:{body.request_id}",
            actor="user:desktop",
            origin="desktop.pin",
            reason="pin completed agent result",
        )
        committed = await service.commit(principal, preview.plan_id)
        fact = committed.facts[0]
        return {"written": True, "fact_id": fact.fact_id}
    except FactConflictError:
        existing = await _existing_pinned_fact(service, principal, text)
        if existing is not None:
            return {"written": False, "fact_id": existing.fact_id}
        raise
    except Exception as exc:
        raise _fact_error(exc) from exc


@facts_router.get("/search")
async def search_facts(
    request: Request,
    query: str = Query(min_length=1, max_length=20_000),
    subject: str | None = Query(default=None, min_length=1, max_length=500),
    include_inactive: bool = Query(default=False),
    limit: int = Query(default=100, ge=1, le=100),
    after_created_at: datetime | None = Query(default=None),
    after_fact_id: str | None = Query(default=None, min_length=1, max_length=500),
) -> dict[str, object]:
    service = _fact_service(request)
    try:
        return await _fact_page(
            service,
            query=query,
            subject=subject,
            include_inactive=include_inactive,
            limit=limit,
            after_created_at=after_created_at,
            after_fact_id=after_fact_id,
        )
    except Exception as exc:
        raise _fact_error(exc) from exc


@facts_router.post("/batch")
async def read_facts_batch(body: FactBatchReadRequest, request: Request) -> dict[str, object]:
    service = _fact_service(request)
    try:
        facts = await service.get_many_with_snapshot_principal(tuple(body.fact_ids), _fact_principal_for_scopes)
        return {"facts": _json_value(facts)}
    except Exception as exc:
        raise _fact_error(exc) from exc


@facts_router.get("/{fact_id}")
async def read_fact(fact_id: str, request: Request) -> dict[str, object]:
    service = _fact_service(request)
    try:
        fact = await service.get(await _fact_principal(service), fact_id)
        return {"fact": _json_value(fact)}
    except Exception as exc:
        raise _fact_error(exc) from exc
