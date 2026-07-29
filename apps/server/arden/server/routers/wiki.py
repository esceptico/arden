"""Admin API for durable wiki rename and maintenance decisions."""

import asyncio
import json
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from arden.logging import get_logger
from arden.revisions import (
    CorruptRepositoryError,
    RevisionConflictError,
    RevisionContentLimitError,
    UnsafePathError,
)
from arden.server.runtime import get_runtime
from arden.wiki.approval_store import PendingWikiRenameApprovalConflictError, WikiRenameApproval
from arden.wiki.approvals import (
    CorruptWikiRenameApprovalError,
    RenamePlanSerializationError,
    RenamePolicy,
    WikiRenameApprovalCoordinator,
    deserialize_rename_plan,
    rename_plan_fingerprint,
)
from arden.wiki.constants import WIKI_MAINTENANCE_FACT_DUPLICATE_EVIDENCE_PREFIX
from arden.wiki.maintenance.runner import (
    WikiMaintenanceError,
    parse_maintenance_proposal,
)
from arden.wiki.maintenance.store import (
    WikiMaintenanceReview,
    WikiMaintenanceReviewAction,
    WikiMaintenanceReviewConflictError,
    WikiMaintenanceStore,
)
from arden.wiki.service import WikiValidationError

router = APIRouter(prefix="/admin/wiki", tags=["wiki"])
_logger = get_logger(__name__)


class WikiRenameRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page_id: str = Field(min_length=1, max_length=512)
    new_path: str = Field(min_length=1, max_length=4096)
    new_title: str = Field(min_length=1, max_length=4096)


class WikiRenameRejectRequest(BaseModel):
    resolution: str | None = Field(default=None, max_length=4_000)


class WikiRenameApprovalResponse(BaseModel):
    approval_id: str
    old_path: str
    new_path: str
    old_title: str
    new_title: str
    link_count: int
    page_count: int
    generation: int
    status: str
    created_at: datetime
    resolved_at: datetime | None
    commit_id: str | None
    resolution: str | None
    replacement_approval_id: str | None


class WikiRenameResultResponse(BaseModel):
    status: str
    approval: WikiRenameApprovalResponse | None
    commit_id: str | None
    replacement_approval_id: str | None
    projection_pending: bool = False


class WikiRenameApprovalListResponse(BaseModel):
    approvals: list[WikiRenameApprovalResponse]


class WikiMaintenanceReviewDecisionRequest(BaseModel):
    generation: StrictInt = Field(ge=0)


class WikiMaintenanceManualResolveRequest(WikiMaintenanceReviewDecisionRequest):
    note: str = Field(min_length=1, max_length=4_000)


class WikiMaintenanceUpdatesProposalResponse(BaseModel):
    kind: Literal["maintenance_updates"]
    summary: str
    updates: list[dict[str, object]]


class WikiMaintenanceEvidenceProposalResponse(BaseModel):
    kind: Literal["manual_evidence_review"]
    section: str
    actualBytes: int
    actualBytesAtLeast: bool = False
    limitBytes: int


type WikiMaintenanceReviewProposalResponse = Annotated[
    WikiMaintenanceUpdatesProposalResponse | WikiMaintenanceEvidenceProposalResponse,
    Field(discriminator="kind"),
]


class WikiMaintenanceReviewResponse(BaseModel):
    review_id: str
    blocking_commit_id: str
    generation: int
    status: str
    summary: str
    proposal: WikiMaintenanceReviewProposalResponse | None
    created_at: datetime
    updated_at: datetime
    resolved_at: datetime | None
    decision_note: str | None


class WikiMaintenanceReviewListResponse(BaseModel):
    reviews: list[WikiMaintenanceReviewResponse]


class WikiMaintenanceReviewEvidenceChangeResponse(BaseModel):
    resourceId: str
    path: str
    action: str
    unifiedDiff: str
    displayLossy: bool


class WikiMaintenanceReviewEvidenceCursorResponse(BaseModel):
    changeIndex: int
    diffOffset: int


class WikiMaintenanceReviewEvidenceResponse(BaseModel):
    review_id: str
    generation: int
    actor: str
    origin: str
    reason: str
    occurred_at: datetime
    changeIndex: int
    changeCount: int
    diffOffset: int
    diffEndOffset: int
    moreInChange: bool
    previousCursor: WikiMaintenanceReviewEvidenceCursorResponse | None
    nextCursor: WikiMaintenanceReviewEvidenceCursorResponse | None
    change: WikiMaintenanceReviewEvidenceChangeResponse


_EVIDENCE_DIFF_PAGE_CHARS = 16_384
_EVIDENCE_COMMIT_BYTES = 256 * 1024
_EVIDENCE_SOURCE_BYTES = 1024 * 1024


def _coordinator(request: Request) -> WikiRenameApprovalCoordinator:
    coordinator = get_runtime(request).wiki_rename_coordinator
    if coordinator is None:
        raise HTTPException(status_code=503, detail="wiki rename service not ready")
    return coordinator


def _maintenance_store(request: Request) -> WikiMaintenanceStore:
    store = get_runtime(request).wiki_maintenance_store
    if store is None:
        raise HTTPException(status_code=503, detail="wiki maintenance service not ready")
    return store


def _wiki_repository(request: Request):
    service = get_runtime(request).wiki_service
    if service is None:
        raise HTTPException(status_code=503, detail="wiki history service not ready")
    return service.repository


def _approval_response(approval: WikiRenameApproval) -> WikiRenameApprovalResponse:
    """Expose reviewed metadata only; the persisted execution plan stays private."""

    try:
        plan = deserialize_rename_plan(approval.plan_json)
        fingerprint = rename_plan_fingerprint(approval.plan_json)
    except RenamePlanSerializationError as exc:
        raise HTTPException(status_code=503, detail="wiki rename approval is corrupt") from exc
    if (
        fingerprint != approval.request_fingerprint
        or plan.old_path != approval.old_path
        or plan.new_path != approval.new_path
        or plan.link_count != approval.link_count
        or plan.page_count != approval.page_count
    ):
        raise HTTPException(status_code=503, detail="wiki rename approval is corrupt")
    return WikiRenameApprovalResponse(
        approval_id=approval.approval_id,
        old_path=approval.old_path,
        new_path=approval.new_path,
        old_title=plan.old_title,
        new_title=plan.new_title,
        link_count=approval.link_count,
        page_count=approval.page_count,
        generation=approval.generation,
        status=approval.status,
        created_at=approval.created_at,
        resolved_at=approval.resolved_at,
        commit_id=approval.commit_id,
        resolution=approval.resolution,
        replacement_approval_id=approval.replacement_approval_id,
    )


def _result_response(result, *, projection_pending: bool = False) -> WikiRenameResultResponse:
    return WikiRenameResultResponse(
        status=result.status,
        approval=_approval_response(result.approval) if result.approval else None,
        commit_id=result.commit_id,
        replacement_approval_id=result.replacement_approval_id,
        projection_pending=projection_pending,
    )


async def _project_wiki_after_commit(request: Request, commit_id: str | None) -> bool:
    if commit_id is None:
        return False
    try:
        await get_runtime(request).project_wiki_state()
    except Exception:
        _logger.exception("Wiki projection failed after a committed rename", commit_id=commit_id)
        return True
    return False


async def _request_maintenance_after_decision(request: Request, *, resume_fact_maintenance: bool) -> None:
    runtime = get_runtime(request)
    await runtime.request_wiki_maintenance()
    if resume_fact_maintenance:
        await runtime.request_fact_maintenance()


async def _notify_maintenance_review_change(request: Request, revision: str) -> None:
    await get_runtime(request).notify_wiki_maintenance_reviews_changed(revision)


def _maintenance_proposal_response(proposal_json: str | None) -> WikiMaintenanceReviewProposalResponse | None:
    """Expose only executable review fields; fingerprints and CAS versions stay server-side."""

    if proposal_json is None:
        return None
    try:
        proposal = json.loads(proposal_json)
        if not isinstance(proposal, dict):
            raise ValueError("proposal is not an object")
        kind = proposal.get("kind")
        if kind == "maintenance_updates":
            summary = proposal.get("summary")
            updates = proposal.get("updates")
            if not isinstance(summary, str) or not isinstance(updates, list):
                raise ValueError("maintenance updates proposal is malformed")
            sanitized_updates: list[dict[str, object]] = []
            for update in updates:
                if not isinstance(update, dict):
                    raise ValueError("maintenance update is malformed")
                page_id = update.get("page_id")
                title = update.get("title")
                aliases = update.get("aliases")
                body = update.get("body")
                if (
                    not isinstance(page_id, str)
                    or not isinstance(title, str)
                    or not isinstance(aliases, list)
                    or not all(isinstance(alias, str) for alias in aliases)
                    or not isinstance(body, str)
                ):
                    raise ValueError("maintenance update is malformed")
                sanitized_updates.append({"pageId": page_id, "title": title, "aliases": aliases, "body": body})
            return WikiMaintenanceUpdatesProposalResponse(
                kind=kind,
                summary=summary,
                updates=sanitized_updates,
            )
        if kind == "manual_evidence_review":
            section = proposal.get("section")
            actual_bytes = proposal.get("actual_bytes")
            actual_bytes_at_least = proposal.get("actual_bytes_at_least", False)
            limit_bytes = proposal.get("limit_bytes")
            if (
                not isinstance(section, str)
                or isinstance(actual_bytes, bool)
                or not isinstance(actual_bytes, int)
                or not isinstance(actual_bytes_at_least, bool)
                or isinstance(limit_bytes, bool)
                or not isinstance(limit_bytes, int)
            ):
                raise ValueError("manual evidence proposal is malformed")
            return WikiMaintenanceEvidenceProposalResponse(
                kind=kind,
                section=section,
                actualBytes=actual_bytes,
                actualBytesAtLeast=actual_bytes_at_least,
                limitBytes=limit_bytes,
            )
        raise ValueError("maintenance proposal has an unknown kind")
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=503, detail="wiki maintenance review is corrupt") from exc


def _maintenance_review_response(review: WikiMaintenanceReview) -> WikiMaintenanceReviewResponse:
    return WikiMaintenanceReviewResponse(
        review_id=review.review_id,
        blocking_commit_id=review.blocking_commit_id,
        generation=review.generation,
        status=review.status.value,
        summary=review.summary,
        proposal=_maintenance_proposal_response(review.proposal_json),
        created_at=review.created_at,
        updated_at=review.updated_at,
        resolved_at=review.resolved_at,
        decision_note=review.decision_note,
    )


async def _resolve_maintenance_review(
    request: Request,
    review_id: str,
    *,
    generation: int,
    action: WikiMaintenanceReviewAction,
    decision_note: str | None = None,
) -> WikiMaintenanceReviewResponse:
    store = _maintenance_store(request)
    try:
        existing = await store.get_review(review_id)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if existing is None:
        raise HTTPException(status_code=404, detail="wiki maintenance review not found")
    if action is WikiMaintenanceReviewAction.ACCEPT:
        proposal = _maintenance_proposal_response(existing.proposal_json)
        if proposal is None or (proposal.kind == "maintenance_updates" and not proposal.updates):
            raise HTTPException(status_code=422, detail="wiki maintenance review has no executable proposal")
        if proposal.kind == "maintenance_updates":
            try:
                proposal_json = existing.proposal_json
                if proposal_json is None:
                    raise WikiMaintenanceError("maintenance review has no executable proposal")
                executable = parse_maintenance_proposal(proposal_json)
                expected_reason = f"wiki maintenance {existing.blocking_commit_id} {executable.replay_fingerprint}"
                if executable.reason != expected_reason:
                    raise WikiMaintenanceError("maintenance proposal reason does not match its review")
            except WikiMaintenanceError as exc:
                raise HTTPException(status_code=503, detail="wiki maintenance review is corrupt") from exc
    try:
        review = await store.resolve(
            review_id,
            expected_generation=generation,
            action=action,
            decision_note=decision_note,
        )
    except WikiMaintenanceReviewConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await _request_maintenance_after_decision(
        request,
        resume_fact_maintenance=existing.evidence_key.startswith(WIKI_MAINTENANCE_FACT_DUPLICATE_EVIDENCE_PREFIX),
    )
    await _notify_maintenance_review_change(request, review.blocking_commit_id)
    return _maintenance_review_response(review)


async def _maintenance_review_evidence(
    request: Request,
    review_id: str,
    *,
    generation: int,
    change_index: int,
    diff_offset: int,
) -> WikiMaintenanceReviewEvidenceResponse:
    store = _maintenance_store(request)
    try:
        review = await store.get_review(review_id)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="wiki maintenance review is corrupt") from exc
    if review is None or review.status.value != "needs_review":
        raise HTTPException(status_code=404, detail="pending wiki maintenance review not found")
    if review.generation != generation:
        raise HTTPException(status_code=409, detail="wiki maintenance review generation changed")

    repository = _wiki_repository(request)
    try:
        return await asyncio.to_thread(
            _maintenance_evidence_page,
            repository,
            review,
            change_index=change_index,
            diff_offset=diff_offset,
        )
    except IndexError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="wiki revision evidence not found") from exc
    except RevisionContentLimitError as exc:
        raise HTTPException(
            status_code=413,
            detail=(
                "wiki revision evidence exceeds the in-app source limit "
                f"({exc.actual_bytes} bytes; limit {exc.limit_bytes})"
            ),
        ) from exc
    except (CorruptRepositoryError, UnsafePathError, TypeError, ValueError, OSError) as exc:
        raise HTTPException(status_code=503, detail="wiki revision evidence is unavailable") from exc


def _maintenance_evidence_page(
    repository,
    review: WikiMaintenanceReview,
    *,
    change_index: int,
    diff_offset: int,
) -> WikiMaintenanceReviewEvidenceResponse:
    commit_bytes = repository.commit_size(review.blocking_commit_id)
    if commit_bytes > _EVIDENCE_COMMIT_BYTES:
        raise RevisionContentLimitError(actual_bytes=commit_bytes, limit_bytes=_EVIDENCE_COMMIT_BYTES)
    commit = repository.inspect_commit(review.blocking_commit_id)
    changes = []
    seen_resources: set[str] = set()
    for change in commit.changes:
        version = change.after or change.before
        if version is None or version.resource_id in seen_resources:
            raise CorruptRepositoryError("wiki revision has invalid resource changes")
        seen_resources.add(version.resource_id)
        if (change.before is not None and change.before.path.endswith(".md")) or (
            change.after is not None and change.after.path.endswith(".md")
        ):
            changes.append(change)
    if not changes:
        raise CorruptRepositoryError("wiki revision has no Markdown changes")
    if change_index >= len(changes):
        raise IndexError("change_index is outside the reviewed Markdown changes")

    selected = changes[change_index]
    selected_version = selected.after or selected.before
    assert selected_version is not None
    diff = repository.diff_versions_page(
        selected.before,
        selected.after,
        offset=diff_offset,
        limit=_EVIDENCE_DIFF_PAGE_CHARS,
        source_byte_limit=_EVIDENCE_SOURCE_BYTES,
    )
    if diff.resource_id != selected_version.resource_id:
        raise CorruptRepositoryError("wiki revision diff does not match its resource change")
    diff_version = diff.after or diff.before
    if diff_version is None:
        raise CorruptRepositoryError("wiki revision diff has no resource")

    previous_cursor = None
    if diff.offset > 0:
        previous_cursor = WikiMaintenanceReviewEvidenceCursorResponse(
            changeIndex=change_index,
            diffOffset=max(0, diff.offset - _EVIDENCE_DIFF_PAGE_CHARS),
        )
    elif change_index > 0:
        previous = changes[change_index - 1]
        previous_tail = repository.diff_versions_page(
            previous.before,
            previous.after,
            limit=_EVIDENCE_DIFF_PAGE_CHARS,
            tail=True,
            source_byte_limit=_EVIDENCE_SOURCE_BYTES,
        )
        previous_cursor = WikiMaintenanceReviewEvidenceCursorResponse(
            changeIndex=change_index - 1,
            diffOffset=previous_tail.offset,
        )

    next_cursor = None
    if diff.has_more:
        next_cursor = WikiMaintenanceReviewEvidenceCursorResponse(
            changeIndex=change_index,
            diffOffset=diff.end_offset,
        )
    elif change_index + 1 < len(changes):
        next_cursor = WikiMaintenanceReviewEvidenceCursorResponse(
            changeIndex=change_index + 1,
            diffOffset=0,
        )
    display_diff = diff.unified_diff.encode("utf-8", errors="surrogateescape").decode(
        "utf-8",
        errors="replace",
    )

    return WikiMaintenanceReviewEvidenceResponse(
        review_id=review.review_id,
        generation=review.generation,
        actor=commit.actor,
        origin=commit.origin,
        reason=commit.reason,
        occurred_at=commit.timestamp,
        changeIndex=change_index,
        changeCount=len(changes),
        diffOffset=diff.offset,
        diffEndOffset=diff.end_offset,
        moreInChange=diff.has_more,
        previousCursor=previous_cursor,
        nextCursor=next_cursor,
        change=WikiMaintenanceReviewEvidenceChangeResponse(
            resourceId=selected_version.resource_id,
            path=diff_version.path,
            action=selected.action,
            unifiedDiff=display_diff,
            displayLossy=display_diff != diff.unified_diff,
        ),
    )


@router.post("/rename-approvals", response_model=WikiRenameResultResponse)
async def request_rename(request: Request, body: WikiRenameRequest):
    coordinator = _coordinator(request)
    try:
        current = coordinator.service.read_page(body.page_id)
        # A title-only edit belongs to the page-edit path. A rename must move
        # the file so it can create its old-path redirect safely.
        if body.new_path == current.resource.path:
            raise WikiValidationError("title edits must not use the rename endpoint")
        await request.app.state.area_pages.validate_rename_request(
            body.page_id,
            body.new_path,
            body.new_title,
        )
        result = await coordinator.request_rename(
            request_key=f"wiki-rename:{body.page_id}",
            page_id=body.page_id,
            new_path=body.new_path,
            new_title=body.new_title,
            expected_version=current.resource.version_id,
            base_head=coordinator.service.repository.head,
            policy=RenamePolicy.ASK,
            apply_plan=request.app.state.area_pages.apply_rename_plan,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="wiki page not found") from exc
    except (RevisionConflictError, PendingWikiRenameApprovalConflictError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (WikiValidationError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    projection_pending = await _project_wiki_after_commit(request, result.commit_id)
    return _result_response(result, projection_pending=projection_pending)


@router.get("/rename-approvals", response_model=WikiRenameApprovalListResponse)
async def list_rename_approvals(request: Request):
    coordinator = _coordinator(request)
    return {"approvals": [_approval_response(approval) for approval in await coordinator.list_pending()]}


@router.post("/rename-approvals/{approval_id}/accept", response_model=WikiRenameResultResponse)
async def accept_rename(request: Request, approval_id: str):
    try:
        result = await _coordinator(request).accept(
            approval_id,
            apply_plan=request.app.state.area_pages.apply_rename_plan,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="wiki rename approval not found") from exc
    except RevisionConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except CorruptWikiRenameApprovalError as exc:
        raise HTTPException(status_code=503, detail="wiki rename approval is corrupt") from exc
    except (WikiValidationError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    projection_pending = await _project_wiki_after_commit(request, result.commit_id)
    return _result_response(result, projection_pending=projection_pending)


@router.post("/rename-approvals/{approval_id}/reject", response_model=WikiRenameResultResponse)
async def reject_rename(request: Request, approval_id: str, body: WikiRenameRejectRequest):
    try:
        result = await _coordinator(request).reject(approval_id, resolution=body.resolution)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="wiki rename approval not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _result_response(result)


@router.get("/maintenance-reviews", response_model=WikiMaintenanceReviewListResponse)
async def list_maintenance_reviews(request: Request):
    store = _maintenance_store(request)
    return {"reviews": [_maintenance_review_response(review) for review in await store.list_pending()]}


@router.get(
    "/maintenance-reviews/{review_id}/evidence",
    response_model=WikiMaintenanceReviewEvidenceResponse,
)
async def get_maintenance_review_evidence(
    request: Request,
    review_id: str,
    generation: int = Query(..., ge=0),
    change_index: int = Query(0, ge=0),
    diff_offset: int = Query(0, ge=0),
):
    return await _maintenance_review_evidence(
        request,
        review_id,
        generation=generation,
        change_index=change_index,
        diff_offset=diff_offset,
    )


@router.post("/maintenance-reviews/{review_id}/accept", response_model=WikiMaintenanceReviewResponse)
async def accept_maintenance_review(
    request: Request,
    review_id: str,
    body: WikiMaintenanceReviewDecisionRequest,
):
    return await _resolve_maintenance_review(
        request,
        review_id,
        generation=body.generation,
        action=WikiMaintenanceReviewAction.ACCEPT,
    )


@router.post("/maintenance-reviews/{review_id}/reject", response_model=WikiMaintenanceReviewResponse)
async def reject_maintenance_review(
    request: Request,
    review_id: str,
    body: WikiMaintenanceReviewDecisionRequest,
):
    return await _resolve_maintenance_review(
        request,
        review_id,
        generation=body.generation,
        action=WikiMaintenanceReviewAction.REJECT,
    )


@router.post("/maintenance-reviews/{review_id}/resolve-manually", response_model=WikiMaintenanceReviewResponse)
async def resolve_maintenance_review_manually(
    request: Request,
    review_id: str,
    body: WikiMaintenanceManualResolveRequest,
):
    return await _resolve_maintenance_review(
        request,
        review_id,
        generation=body.generation,
        action=WikiMaintenanceReviewAction.RESOLVE_MANUAL,
        decision_note=body.note,
    )
