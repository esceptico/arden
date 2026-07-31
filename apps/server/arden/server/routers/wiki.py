"""Admin API for durable wiki rename decisions."""

from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from arden.logging import get_logger
from arden.revisions import RevisionConflictError
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
from arden.wiki.exceptions import WikiRenameApplyAmbiguousError
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


def _coordinator(request: Request) -> WikiRenameApprovalCoordinator:
    coordinator = get_runtime(request).wiki_rename_coordinator
    if coordinator is None:
        raise HTTPException(status_code=503, detail="wiki rename service not ready")
    return coordinator


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
        return await get_runtime(request).project_wiki_change_after_commit()
    except Exception:
        _logger.exception("Wiki projection enqueue failed after a committed rename", commit_id=commit_id)
        return True


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
    except WikiRenameApplyAmbiguousError as exc:
        raise HTTPException(status_code=503, detail="wiki rename is still applying; retry accept") from exc
    except BaseExceptionGroup as exc:
        raise HTTPException(status_code=503, detail="wiki rename is still applying; retry accept") from exc
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
