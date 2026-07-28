"""Memory router — directory-first memory artifacts plus records/labels for the
/admin/memory desktop contract.

The substrate is records + labels. There is no graph or canonical derivation DAG
in the read/write contract; item detail returns empty edge arrays for one release
for compatibility.
"""

import re
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from arden.memory.artifacts import MACHINE_PAGE_DIRS, ArtifactMemoryStore, MemoryArtifact
from arden.memory.frontmatter import strip_frontmatter
from arden.memory.journal import JournalConflictError
from arden.memory.ledger import LedgerEntry
from arden.memory.models import Record, SourceRef
from arden.memory.page_edit_service import (
    PageEditService,
    PreviewExpiredError,
    PreviewNotFoundError,
    ReconciliationPendingError,
    StalePageRevisionError,
)
from arden.memory.page_events import PageEditDecision
from arden.memory.pages import Page
from arden.memory.reconciler import RecordOperation
from arden.memory.scopes import MemoryScope, scope_for_write
from arden.server.deps import require_knowledge_runtime
from arden.server.runtime import Runtime, get_runtime
from arden.server.runtime.knowledge import KnowledgeRuntime

router = APIRouter(prefix="/admin/memory", tags=["memory"])

_MANAGED_WIKI_READONLY_REASON = "Managed wiki page — use Rename; editing is not available here yet."


def _record_store(knowledge: KnowledgeRuntime = Depends(require_knowledge_runtime)):
    if not knowledge._record_store:
        raise HTTPException(status_code=503, detail="memory not ready")
    return knowledge._record_store


def _artifact_store(knowledge: KnowledgeRuntime = Depends(require_knowledge_runtime)) -> ArtifactMemoryStore:
    artifacts = knowledge.artifact_store
    if artifacts is None:
        raise HTTPException(status_code=503, detail="memory artifacts not ready")
    return artifacts


def _page_edit_service(knowledge: KnowledgeRuntime = Depends(require_knowledge_runtime)) -> PageEditService:
    if knowledge.page_edit_service is None:
        raise HTTPException(status_code=503, detail="memory page edits not ready")
    return knowledge.page_edit_service


def _wiki_service(request: Request):
    """The managed wiki is optional while the runtime is starting and in tests."""

    return getattr(getattr(request.app.state, "runtime", None), "wiki_service", None)


def _link_index_projection(knowledge: KnowledgeRuntime = Depends(require_knowledge_runtime)):
    return getattr(knowledge, "_link_index", None)


# --- JSON adapters (record -> item) ------------------------------------------

# The lens.ts color ramp / badges key on these exact strings — emit nothing else.
_PROVENANCE_USER = "user_authored"
_PROVENANCE_RECORDED = "recorded"
_PROVENANCE_EXTERNAL = "external"
_USER_SOURCE_KINDS = {"desktop_pin", "user", "user_authored"}
_INTEGRATION_SOURCE_KINDS = {"file", "web", "email", "gmail", "calendar", "slack", "mcp", "integration"}


def _provenance(r: Record) -> str:
    if r.pinned:
        return _PROVENANCE_USER
    if r.source_ref is not None and r.source_ref.kind in _USER_SOURCE_KINDS:
        return _PROVENANCE_USER
    if r.source_ref is not None and r.source_ref.kind in _INTEGRATION_SOURCE_KINDS:
        return _PROVENANCE_EXTERNAL
    return _PROVENANCE_RECORDED


def record_to_item_json(r: Record, labels: list[str]) -> dict:
    """A Record rendered as the UI's `MemoryItem` record item shape. Records
    have no claims-era fields, so map deterministically: text->content,
    kind->canonical_subject, one flat user/null scope, no edges. `labels` come
    from a labels_for batch hydrate — list endpoints must never fetch them per
    record (N+1)."""
    source_refs = []
    if r.source_ref is not None:
        source_refs.append(
            {
                "kind": r.source_ref.kind,
                "ref": r.source_ref.ref,
                "captured_at": r.source_ref.captured_at,
            }
        )
    status = "superseded" if r.superseded_by else "active"
    return {
        "id": r.id,
        "content": r.text,
        "kind": r.kind,
        "canonical_subject": r.kind,
        "labels": labels,
        "scope": {"kind": r.scope_kind or "global", "key": r.scope_key},
        "provenance": _provenance(r),
        "pinned": r.pinned,
        "status": status,
        "standing": "active",
        "depth": 0,
        "valid_from": r.created_at,
        "invalid_at": None,
        "source_refs": source_refs,
        "corroboration": 1 + len(source_refs),
        "last_relevant_at": r.last_confirmed_at,
        "feedback": "confirmed" if r.pinned else "none",
        "created_at": r.created_at,
        "updated_at": r.last_confirmed_at,
    }


async def hydrated_items_json(store, records: list[Record]) -> list[dict]:
    """Render many records as MemoryItems with ONE labels_for batch query."""
    labels = await store.labels_for([r.id for r in records])
    return [record_to_item_json(r, labels[r.id]) for r in records]


# --- 1: scopes ---------------------------------------------------------------


@router.get("/scopes")
async def list_scopes() -> dict:
    """The UI currently shows one simple memory surface.

    Scopes are still stored and enforced by API/tool read paths; they are not a
    user-facing hierarchy or graph browser. Returning [] keeps the old scope-chip
    row hidden while the lean memory UI settles.
    """
    return {"scopes": []}


# --- 2: artifact memory surface ---------------------------------------------


def artifact_summary_to_json(a) -> dict:
    return {
        "path": a.path,
        "title": a.title,
        "kind": a.kind,
        "type": a.type,
        "directory": a.directory,
        "scope": {"kind": a.scope_kind, "key": a.scope_key},
        "snippet": a.snippet,
        "summary": a.summary,
        "revision": a.revision,
        "record_count": a.record_count,
        "generated": a.generated,
        "editable": a.editable,
        "readonly_reason": a.readonly_reason,
        "updated_at": a.updated_at,
        "created_at": a.created_at,
        "labels": list(a.labels),
        "source": a.source,
    }


def artifact_detail_to_json(a) -> dict:
    timeline = tuple(getattr(a, "timeline", ()) or ())
    active_ids = {entry.id for entry in Page(lines=list(timeline)).active_entries()}
    return {
        **artifact_summary_to_json(a),
        "content": a.content,
        "editable_content": a.raw_content if a.editable else None,
        "timeline": [
            {
                "id": entry.id,
                "text": entry.text,
                "kind": entry.kind,
                "date": (entry.occurred_at or entry.meta.recorded_at)[:10],
                "src": entry.meta.sources[0].kind if entry.meta.sources else "unknown",
                "pinned": entry.pinned,
                "superseded": entry.id not in active_ids,
            }
            for entry in timeline
            if isinstance(entry, LedgerEntry) and entry.meta.operation == "record"
        ],
        "frontmatter": _json_safe(getattr(a, "frontmatter", {}) or {}),
    }


def _json_safe(fm: dict) -> dict:
    """Frontmatter values can carry YAML wrapper types (e.g. QuotedStr); coerce to
    plain JSON-friendly scalars/lists so the client renders them as Obsidian properties."""

    def coerce(v):
        if isinstance(v, (list, tuple)):
            return [coerce(x) for x in v]
        if isinstance(v, (str, int, float, bool)) or v is None:
            return v
        return str(v)

    return {str(k): coerce(v) for k, v in fm.items()}


def _wiki_frontmatter(record) -> dict:
    page = record.page
    frontmatter = {
        "page_id": page.page_id,
        "title": page.title,
        "aliases": list(page.aliases),
        "lifecycle": page.lifecycle,
    }
    if page.redirect_to is not None:
        frontmatter["redirect_to"] = page.redirect_to
    frontmatter.update(page.metadata)
    return frontmatter


def _wiki_artifact(record) -> MemoryArtifact:
    body = record.page.body.decode("utf-8")
    frontmatter = _wiki_frontmatter(record)
    metadata = record.page.metadata
    declared_kind = metadata.get("kind")
    kind = declared_kind.strip() if isinstance(declared_kind, str) and declared_kind.strip() else "topic"
    labels = metadata.get("labels")
    label_values = tuple(str(label) for label in labels) if isinstance(labels, (list, tuple)) else ()
    snippet = next(
        (line.strip() for line in body.splitlines() if line.strip() and not line.lstrip().startswith("#")),
        None,
    )
    summary = metadata.get("summary")
    parent = Path(record.resource.path).parent
    return MemoryArtifact(
        path=record.resource.path,
        title=record.page.title,
        kind=kind,
        scope_kind="global",
        scope_key=None,
        content=body,
        summary=summary if isinstance(summary, str) and summary.strip() else snippet,
        revision=record.resource.version_id,
        record_count=None,
        updated_at=None,
        created_at=None,
        type="file",
        directory="" if parent == Path(".") else parent.as_posix(),
        generated=False,
        editable=False,
        readonly_reason=_MANAGED_WIKI_READONLY_REASON,
        snippet=snippet,
        labels=label_values,
        source="wiki",
        frontmatter=frontmatter,
    )


def _managed_wiki_artifacts(wiki_service, *, include_redirects: bool = False) -> list[MemoryArtifact]:
    return [_wiki_artifact(record) for record in wiki_service.list_pages(include_redirects=include_redirects)]


def _filter_wiki_artifacts(
    artifacts: list[MemoryArtifact], *, kind: str | None = None, q: str | None = None
) -> list[MemoryArtifact]:
    query = (q or "").strip().lower()
    filtered: list[MemoryArtifact] = []
    for artifact in artifacts:
        if kind and artifact.kind != kind:
            continue
        if query:
            haystack = " ".join(
                (
                    artifact.path,
                    artifact.title,
                    artifact.kind,
                    artifact.directory,
                    artifact.content,
                    str(artifact.frontmatter),
                )
            ).lower()
            if query not in haystack:
                continue
        filtered.append(artifact)
    return filtered


def _managed_wiki_artifact(wiki_service, path: str) -> MemoryArtifact | None:
    record = _managed_wiki_record(wiki_service, path)
    return _wiki_artifact(record) if record is not None else None


def _managed_wiki_record(wiki_service, path: str):
    return next(
        (record for record in wiki_service.list_pages(include_redirects=True) if record.resource.path == path),
        None,
    )


def _managed_wiki_directories(wiki_artifacts: list[MemoryArtifact]) -> set[str]:
    directories: set[str] = set()
    for artifact in wiki_artifacts:
        parent = Path(artifact.path).parent
        while parent != Path("."):
            directories.add(parent.as_posix() + "/")
            parent = parent.parent
    return directories


@router.get("/artifacts")
def list_artifacts(
    kind: str | None = Query(default=None),
    q: str | None = Query(default=None, max_length=200),
    artifacts: ArtifactMemoryStore = Depends(_artifact_store),
    wiki_service=Depends(_wiki_service),
) -> dict:
    visible = {artifact.path: artifact for artifact in artifacts.list_artifacts(kind=kind, q=q)}
    all_managed = _managed_wiki_artifacts(wiki_service, include_redirects=True) if wiki_service is not None else []
    active_managed = [artifact for artifact in all_managed if artifact.frontmatter["lifecycle"] == "active"]
    managed = _filter_wiki_artifacts(active_managed, kind=kind, q=q)
    # Managed pages own their logical paths. Their physical wiki subtree remains
    # invisible to ArtifactMemoryStore and FilePageStore.
    for artifact in all_managed:
        visible.pop(artifact.path, None)
    visible.update({artifact.path: artifact for artifact in managed})
    return {
        "artifacts": [artifact_summary_to_json(a) for a in visible.values()],
        "directories": sorted(set(artifacts.list_directories()) | _managed_wiki_directories(active_managed)),
    }


@router.post("/artifacts/rebuild")
async def rebuild_artifacts(
    artifacts: ArtifactMemoryStore = Depends(_artifact_store),
    wiki_service=Depends(_wiki_service),
) -> dict:
    # Memory is file-canonical: the markdown pages ARE the source of truth, there
    # is no projection to re-derive. Exporting here would clobber the pages, so
    # this is a no-op that just returns the current pages.
    visible = {artifact.path: artifact for artifact in artifacts.list_artifacts()}
    all_managed = _managed_wiki_artifacts(wiki_service, include_redirects=True) if wiki_service is not None else []
    managed = [artifact for artifact in all_managed if artifact.frontmatter["lifecycle"] == "active"]
    for artifact in all_managed:
        visible.pop(artifact.path, None)
    visible.update({artifact.path: artifact for artifact in managed})
    return {
        "artifacts": [artifact_summary_to_json(a) for a in visible.values()],
        "detail": "no-op: memory is file-canonical",
    }


class InitBody(BaseModel):
    confirm: bool = Field(default=False)
    max_llm_calls: int = Field(default=400, ge=1, le=100_000)
    wipe: bool = Field(default=False)


@router.post("/init")
async def init_memory(
    body: InitBody,
    runtime: Runtime = Depends(get_runtime),
) -> dict:
    """/init: reset the curator + consolidate watermarks and (re)derive memory from
    ALL chat transcripts via the BULK curator gate, then consolidate. Additive by
    default (keeps existing records, can only enrich); pass wipe=true for a
    destructive reset (wipe-except-pinned first). Requires confirm=true."""
    if not body.confirm:
        raise HTTPException(status_code=400, detail="init requires confirm=true")
    if not runtime.knowledge.memory_ready:
        raise HTTPException(status_code=503, detail="memory not ready")
    from arden.memory.init import run_memory_init

    return await run_memory_init(
        runtime.knowledge,
        max_llm_calls=body.max_llm_calls,
        wipe=body.wipe,
    )


@router.post("/prune")
async def prune_records(store=Depends(_record_store)) -> dict:
    """Manually trigger the LINT pass: hard-delete superseded tombstones, drop the
    labels they orphan, and reconcile the vector index. Runs automatically each
    consolidate sweep too; this is the on-demand lever."""
    return await store.prune()


class NotebookCreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=1000)
    kind: Literal["note", "folder"]


@router.post("/notebook/create")
async def create_notebook_entry(
    body: NotebookCreateBody,
    request: Request,
    artifacts: ArtifactMemoryStore = Depends(_artifact_store),
    service: PageEditService = Depends(_page_edit_service),
) -> dict:
    _reject_machine_page(body.path)
    safe = Path(body.path)
    if safe.is_absolute() or ".." in safe.parts:
        raise HTTPException(status_code=422, detail="invalid memory page path")
    if body.kind == "note":
        if safe.suffix != ".md":
            raise HTTPException(status_code=422, detail="memory notes must end with .md")
        if (wiki_service := _wiki_service(request)) is not None and _managed_wiki_artifact(
            wiki_service, safe.as_posix()
        ):
            raise HTTPException(status_code=409, detail="managed wiki page already exists at this path")
        try:
            await service.create_page(path=safe.as_posix(), actor="user:desktop")
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except (FileExistsError, JournalConflictError) as exc:
            raise HTTPException(status_code=409, detail="memory page already exists") from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=422, detail="invalid memory page path") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"artifact": artifact_summary_to_json(artifacts.read_artifact(safe.as_posix()))}
    if safe.suffix == ".md":
        raise HTTPException(status_code=422, detail="memory folders must not end with .md")
    try:
        created = artifacts.create_directory(safe.as_posix())
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail="memory folder already exists") from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=422, detail="invalid memory folder path") from exc
    return {"path": created}


class PageEditPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    base_revision: str
    content: str
    actor: str = "user:desktop"


class PageEditDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    choice: Literal["note_only", "forget_memory"]
    target_ids: tuple[str, ...] = ()


class PageEditApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preview_id: str
    decisions: dict[str, Literal["note_only", "forget_memory"] | PageEditDecisionRequest]
    save_pending: bool = False


class PageEditRetryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str
    decisions: dict[str, Literal["note_only", "forget_memory"] | PageEditDecisionRequest]


def _page_edit_decisions(
    raw_decisions: dict[str, Literal["note_only", "forget_memory"] | PageEditDecisionRequest],
) -> dict[str, PageEditDecision]:
    decisions: dict[str, PageEditDecision] = {}
    for question_id, raw in raw_decisions.items():
        value = PageEditDecisionRequest(choice=raw) if isinstance(raw, str) else raw
        decisions[question_id] = PageEditDecision(
            choice="Note only" if value.choice == "note_only" else "Forget memory",
            target_ids=value.target_ids,
        )
    return decisions


def _reject_machine_page(path: str) -> None:
    parts = Path(path).parts
    if parts and parts[0] in MACHINE_PAGE_DIRS:
        raise HTTPException(status_code=403, detail="machine-only memory page")


def _require_editable_page(path: str, artifacts: ArtifactMemoryStore, wiki_service=None) -> None:
    _reject_machine_page(path)
    if wiki_service is not None and _managed_wiki_artifact(wiki_service, path) is not None:
        raise HTTPException(status_code=403, detail=_MANAGED_WIKI_READONLY_REASON)
    try:
        artifact = artifacts.read_artifact(path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="memory page not found") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if not artifact.editable:
        raise HTTPException(status_code=403, detail=artifact.readonly_reason or "machine-only memory page")


def _revision_conflict(exc: StalePageRevisionError) -> HTTPException:
    try:
        current_content = exc.current_content.decode("utf-8")
    except UnicodeDecodeError as decode_error:
        raise HTTPException(status_code=422, detail="memory pages must be UTF-8") from decode_error
    return HTTPException(
        status_code=409,
        detail={
            "error": "page_revision_conflict",
            "current_content": current_content,
            "current_revision": exc.current_revision,
            "base_revision": exc.base_revision,
            "candidate_revision": exc.candidate_revision,
        },
    )


@router.post("/page-edits/preview")
async def preview_page_edit(
    body: PageEditPreviewRequest,
    service: PageEditService = Depends(_page_edit_service),
    artifacts: ArtifactMemoryStore = Depends(_artifact_store),
    wiki_service=Depends(_wiki_service),
) -> dict:
    _require_editable_page(body.path, artifacts, wiki_service)
    try:
        preview = await service.preview(
            path=body.path,
            base_revision=body.base_revision,
            content=body.content.encode("utf-8"),
            actor=body.actor,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="memory page not found") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except StalePageRevisionError as exc:
        raise _revision_conflict(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"preview": preview.model_dump(mode="json")}


@router.put("/page-edits/apply")
async def apply_page_edit(
    body: PageEditApplyRequest,
    service: PageEditService = Depends(_page_edit_service),
    wiki_service=Depends(_wiki_service),
) -> dict:
    try:
        if (
            wiki_service is not None
            and _managed_wiki_artifact(wiki_service, service.preview_path(body.preview_id)) is not None
        ):
            raise HTTPException(status_code=403, detail=_MANAGED_WIKI_READONLY_REASON)
        decisions = _page_edit_decisions(body.decisions)
        event = await service.apply(
            body.preview_id,
            decisions=decisions,
            save_as_pending=body.save_pending,
        )
    except StalePageRevisionError as exc:
        raise _revision_conflict(exc) from exc
    except ReconciliationPendingError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except PreviewExpiredError as exc:
        raise HTTPException(status_code=422, detail="page edit preview expired") from exc
    except PreviewNotFoundError as exc:
        raise HTTPException(status_code=404, detail="page edit preview not found") from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="memory page not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"event": event.model_dump(mode="json"), "revision": event.result_revision}


@router.put("/page-edits/retry")
async def retry_page_edit(
    body: PageEditRetryRequest,
    service: PageEditService = Depends(_page_edit_service),
    wiki_service=Depends(_wiki_service),
) -> dict:
    try:
        pending = next((event for event in service.history() if event.id == body.event_id), None)
        if (
            pending is not None
            and wiki_service is not None
            and _managed_wiki_artifact(wiki_service, pending.path) is not None
        ):
            raise HTTPException(status_code=403, detail=_MANAGED_WIKI_READONLY_REASON)
        event = await service.retry(
            body.event_id,
            decisions=_page_edit_decisions(body.decisions),
        )
    except ReconciliationPendingError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"event": event.model_dump(mode="json"), "revision": event.result_revision}


@router.get("/page-edits/history")
def page_edit_history(
    path: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    before_sequence: int | None = Query(default=None, ge=1),
    service: PageEditService = Depends(_page_edit_service),
    wiki_service=Depends(_wiki_service),
) -> dict:
    if path is not None:
        _reject_machine_page(path)
        if wiki_service is not None and _managed_wiki_record(wiki_service, path) is not None:
            raise HTTPException(status_code=409, detail="managed wiki history is not legacy page-edit history")
    all_events = tuple(sorted(service.history(path=path), key=lambda event: event.sequence, reverse=True))
    events = tuple(event for event in all_events if before_sequence is None or event.sequence < before_sequence)
    page = events[:limit]
    return {
        "events": [event.model_dump(mode="json") for event in page],
        "total": len(all_events),
        "limit": limit,
        "next_before_sequence": page[-1].sequence if len(events) > len(page) else None,
    }


_WIKILINK_MD_RE = re.compile(r"\[\[([^\]#|]+)(?:#[^\]|]+)?(?:\|([^\]]+))?\]\]")
_COMMENT_MD_RE = re.compile(r"<!--.*?-->", flags=re.DOTALL)
_INLINE_LINK_MD_RE = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_MARKUP_CHARS_RE = re.compile(r"[`*_#>|]+")
_MENTION_CONTEXT_WORDS = 15
_MAX_UNLINKED_SOURCES = 4


def _strip_page_markup(content: str) -> str:
    body = strip_frontmatter(content)
    body = _COMMENT_MD_RE.sub(" ", body)
    body = _WIKILINK_MD_RE.sub(lambda m: m.group(2) or m.group(1), body)
    body = _INLINE_LINK_MD_RE.sub(r"\1", body)
    body = _MARKUP_CHARS_RE.sub(" ", body)
    return " ".join(body.split())


def _mention_excerpt(text: str, start: int, end: int) -> str:
    before = text[:start].split()
    after = text[end:].split()
    words = [*before[-(_MENTION_CONTEXT_WORDS - 1) :], text[start:end], *after[:_MENTION_CONTEXT_WORDS]]
    prefix = "…" if len(before) > _MENTION_CONTEXT_WORDS - 1 else ""
    suffix = "…" if len(after) > _MENTION_CONTEXT_WORDS else ""
    return f"{prefix}{' '.join(words)}{suffix}"


def _unlinked_mentions(path: str, title: str, snapshot, artifacts: ArtifactMemoryStore) -> list[dict]:
    pattern = re.compile(rf"(?<!\w){re.escape(title)}(?!\w)", flags=re.IGNORECASE)
    linked_sources = {link.source_path for link in snapshot.links if link.resolved_path == path}
    mentions: list[dict] = []
    for source in snapshot.pages:
        if source == path or source in linked_sources or Path(source).parts[0] in MACHINE_PAGE_DIRS:
            continue
        try:
            text = _strip_page_markup(artifacts.read_resource_bytes(source).decode("utf-8"))
        except (FileNotFoundError, UnicodeDecodeError):
            continue
        match = pattern.search(text)
        if match is None:
            continue
        mentions.append({"source_path": source, "context": _mention_excerpt(text, match.start(), match.end())})
        if len(mentions) == _MAX_UNLINKED_SOURCES:
            break
    return mentions


def _wiki_link_json(reference, *, source, paths_by_id: dict[str, str]) -> dict:
    node = reference.node
    target = f"{node.page or ''}{f'#{node.fragment}' if node.fragment is not None else ''}"
    display = (node.alias or target).strip()
    alias = node.alias.strip() if node.alias is not None else None
    candidate_ids = reference.candidates or (
        (reference.target_page_id,) if reference.target_page_id is not None else ()
    )
    candidates = tuple(
        sorted(
            (paths_by_id[page_id] for page_id in candidate_ids if page_id in paths_by_id),
            key=str.casefold,
        )
    )
    resolved_path = paths_by_id.get(reference.target_page_id) if reference.target_page_id is not None else None
    source_body = source.page.body.decode("utf-8")
    prefix = source_body.encode("utf-8")[: node.start].decode("utf-8")
    line = prefix.count("\n") + 1
    column = len(prefix.rsplit("\n", 1)[-1]) + 1
    return {
        "source_path": source.resource.path,
        "target": target,
        "display": display,
        "alias": alias,
        "heading": None,
        "context": node.raw,
        "line": line,
        "column": column,
        "status": str(reference.status),
        "resolved_path": resolved_path,
        "candidates": candidates,
        "source_revision": source.resource.version_id,
    }


def _wiki_page_links(report, *, limit: int, offset: int) -> dict:
    records_by_id = {page.page.page_id: page for page in report.pages}
    paths_by_id = {page_id: page.resource.path for page_id, page in records_by_id.items()}

    def render(reference) -> dict:
        source = records_by_id[reference.source_page_id]
        return _wiki_link_json(reference, source=source, paths_by_id=paths_by_id)

    return {
        "path": report.page.resource.path,
        "revision": report.head or report.page.resource.version_id,
        "stale": False,
        "outgoing": [render(link) for link in report.outgoing[offset : offset + limit]],
        "backlinks": [render(link) for link in report.backlinks[offset : offset + limit]],
        "unlinked": [],
        "total_outgoing": len(report.outgoing),
        "total_backlinks": len(report.backlinks),
        "limit": limit,
        "offset": offset,
    }


@router.get("/links")
def page_links(
    path: str = Query(..., min_length=1, max_length=1000),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    projection=Depends(_link_index_projection),
    artifacts: ArtifactMemoryStore = Depends(_artifact_store),
    wiki_service=Depends(_wiki_service),
) -> dict:
    safe = Path(path)
    if safe.is_absolute() or ".." in safe.parts:
        raise HTTPException(status_code=422, detail="invalid memory page path")
    if wiki_service is not None:
        try:
            report = wiki_service.link_report_for_path(path)
        except KeyError:
            pass
        else:
            return _wiki_page_links(report, limit=limit, offset=offset)
    _reject_machine_page(path)
    if projection is None:
        raise HTTPException(status_code=503, detail="memory link index not ready")
    snapshot = projection.index.snapshot
    if not snapshot.contains(path):
        raise HTTPException(status_code=404, detail="memory page not found")
    outgoing = snapshot.outgoing(path)
    backlinks = snapshot.backlinks(path)
    try:
        title = artifacts.read_artifact(path).title
    except FileNotFoundError:
        title = safe.stem
    return {
        "path": path,
        "revision": snapshot.revision,
        "stale": projection.stale,
        "outgoing": [link.to_dict() for link in outgoing[offset : offset + limit]],
        "backlinks": [link.to_dict() for link in backlinks[offset : offset + limit]],
        "unlinked": _unlinked_mentions(path, title, snapshot, artifacts),
        "total_outgoing": len(outgoing),
        "total_backlinks": len(backlinks),
        "limit": limit,
        "offset": offset,
    }


@router.get("/artifacts/{path:path}")
def read_artifact(
    path: str,
    artifacts: ArtifactMemoryStore = Depends(_artifact_store),
    wiki_service=Depends(_wiki_service),
) -> dict:
    if wiki_service is not None and (artifact := _managed_wiki_artifact(wiki_service, path)) is not None:
        return {"artifact": artifact_detail_to_json(artifact)}
    try:
        artifact = artifacts.read_artifact(path)
        return {"artifact": artifact_detail_to_json(artifact)}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="memory artifact not found") from exc


# --- 3: records (retrieval substrate / compatibility) ------------------------


class RecordBody(BaseModel):
    text: str = Field(min_length=1, max_length=20_000)
    kind_tag: str = Field(default="fact", pattern="^(directive|fact|source)$")
    scope_kind: str | None = Field(default=None, max_length=64)
    scope_key: str | None = Field(default=None, max_length=500)


class PinBody(BaseModel):
    pinned: bool


class ForgetBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm: bool = False


@router.post("/record")
async def create_record(
    body: RecordBody,
    store=Depends(_record_store),
    artifacts: ArtifactMemoryStore = Depends(_artifact_store),
) -> dict:
    """Quick-capture write (the desktop pin-to-memory affordance): a single atomic
    record into the flat pool. Pinning is a follow-up call so the record survives
    consolidation decay."""
    explicit = MemoryScope(body.scope_kind, body.scope_key) if body.scope_kind or body.scope_key else None
    scope = scope_for_write(kind=body.kind_tag, explicit_scope=explicit)
    record = await store.add(body.text, kind=body.kind_tag, scope_kind=scope.kind, scope_key=scope.key)
    artifacts.append_event(f"Remembered: {body.text}")  # changelog audit (separate from canonical pages)
    # store.add already persists the page (canonical). Do NOT export_from_records —
    # that would re-derive the old projection over the canonical pages, clobbering them.
    return {"record": record_to_item_json(record, [])}


@router.post("/record/{record_id}/pin")
async def pin_record(
    record_id: str,
    body: PinBody,
    store=Depends(_record_store),
    artifacts: ArtifactMemoryStore = Depends(_artifact_store),
) -> dict:
    if not await store.set_pinned(record_id, body.pinned):
        raise HTTPException(status_code=404, detail="record not found")
    artifacts.append_event(f"{'pinned' if body.pinned else 'unpinned'} memory record")
    return {"ok": True, "pinned": body.pinned}


@router.post("/record/{record_id}/forget")
async def forget_record(record_id: str, body: ForgetBody, store=Depends(_record_store)) -> dict:
    """Append an exact, user-authored RETRACT. Never infer a target from text."""
    if not body.confirm:
        raise HTTPException(status_code=400, detail="forget requires confirm=true")
    record = await store.get(record_id)
    if record is None:
        if hasattr(store, "history") and store.history(record_id):
            raise HTTPException(status_code=409, detail="record is no longer active")
        raise HTTPException(status_code=404, detail="record not found")
    if record.superseded_by:
        raise HTTPException(status_code=409, detail="record is no longer active")
    if not hasattr(store, "apply_operations"):
        raise HTTPException(status_code=503, detail="append-only record lifecycle is unavailable")
    source = SourceRef(
        kind="user_action",
        ref=f"memory:record:{record_id}:forget",
        role="retraction",
    )
    revision = store.apply_operations(
        (RecordOperation.retract(record_id),),
        source,
        batch_key=f"desktop-forget:{record_id}",
    )
    return {"ok": True, "operation": "RETRACT", "record_id": record_id, "revision": revision}


@router.get("/items")
async def list_items(
    status: str = Query(default="active"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    q: str | None = Query(default=None, max_length=200),
    store=Depends(_record_store),
    # Scope filters are visibility metadata.
    scope_kind: str | None = None,
    scope_key: str | None = None,
    kind: str | None = None,
) -> dict:
    include_superseded = status == "superseded"
    scopes = [(scope_kind, scope_key)] if scope_kind is not None else None
    if q:
        records = await store.search(
            q,
            include_superseded=include_superseded,
            limit=limit,
            scopes=scopes,
            kinds=[kind] if kind else None,
        )
    else:
        records = await store.list(
            include_superseded=include_superseded,
            limit=limit,
            offset=offset,
            scopes=scopes,
            kinds=[kind] if kind else None,
        )
    if status == "superseded":
        records = [r for r in records if r.superseded_by]
    return {"items": await hydrated_items_json(store, records), "limit": limit, "offset": offset}


@router.get("/items/{item_id}")
async def get_item(item_id: str, store=Depends(_record_store)) -> dict:
    record = await store.get(item_id)
    if record is None:
        raise HTTPException(status_code=404, detail="claim not found")
    labels = await store.labels_of(item_id)
    return {"item": record_to_item_json(record, labels), "parents": [], "children": []}


# --- 4: search ---------------------------------------------------------------


@router.get("/search")
async def search(
    q: str = Query(..., min_length=1),
    limit: int = Query(default=50, ge=1, le=200),
    include_inactive: bool = Query(default=False),
    mode: str = Query(default="fts"),
    store=Depends(_record_store),
    scope_kind: str | None = None,
    scope_key: str | None = None,
    kind: str | None = None,
) -> dict:
    scopes = [(scope_kind, scope_key)] if scope_kind is not None else None
    records = await store.search(
        q,
        limit=limit,
        include_superseded=include_inactive,
        scopes=scopes,
        kinds=[kind] if kind else None,
    )
    return {
        "mode": "hybrid" if store._search_index is not None else "fts",
        "items": await hydrated_items_json(store, records),
        "degraded": store._search_index is None,
    }
