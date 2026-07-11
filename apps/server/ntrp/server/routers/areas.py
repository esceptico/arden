"""Areas router — the single API surface for areas (the one container:
chats file into an area; a page + standing agent are optional capabilities).
Detail/asks/suggestions ride app.state (AreaService is a plain constructor
with injected callables); CRUD goes through the session service's store."""

from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ntrp.areas.work_store import AreaWorkConflict
from ntrp.server.schemas import (
    AreaResponse,
    CreateAreaOutcomeRequest,
    CreateAreaRequest,
    UpdateAreaOutcomeRequest,
    UpdateAreaRequest,
    UpdateAreaWorkItemRequest,
)

router = APIRouter(prefix="/areas", tags=["areas"])


class ResolveBody(BaseModel):
    state: Literal["dismissed", "done", "snoozed"]
    snoozed_until: str | None = None
    resolution: Literal["approved", "rejected", "dismissed", "acknowledged"] | None = None


class ReplyBody(BaseModel):
    message: str = Field(min_length=1, max_length=20_000)


class AutonomyBody(BaseModel):
    autonomy: Literal["observe", "act"] | None


def _svc(request: Request):
    return request.app.state.area_service


def _sessions(request: Request):
    return request.app.state.runtime.session_service


def _lifecycle(request: Request):
    return request.app.state.area_lifecycle


def _pages(request: Request):
    return request.app.state.area_pages


@router.get("", response_model=dict[str, list[AreaResponse]])
async def list_areas(request: Request):
    return {"areas": await _sessions(request).list_areas()}


@router.get("/overview")
async def areas_overview(request: Request):
    await request.app.state.hydrate_area_snapshot()
    svc = _svc(request)
    svc.refresh_mechanical()
    overview = svc.overview()
    # Suggestions are page-keyed; a page already attached anywhere must not
    # resurface as a suggestion.
    attached = {Path(s["page_path"]).stem for s in overview["areas"] if s.get("page_path")}
    overview["suggested"] = request.app.state.area_suggestions.list(exclude_keys=attached)
    return overview


@router.post("", response_model=AreaResponse)
async def create_area(request: Request, req: CreateAreaRequest):
    """Create-or-reuse by name (case-insensitive): promoting a suggested page
    for a container the user already has must attach, not duplicate."""
    try:
        area = await _lifecycle(request).create(
            name=req.name,
            default_cwd=req.default_cwd,
            instructions=req.instructions,
            knowledge_scope=req.knowledge_scope,
            page_path=req.page_path,
            autonomy=None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await request.app.state.emit_areas_changed([area["area_id"]])
    return area


@router.post("/suggestions/{key}/dismiss")
async def dismiss_suggestion(request: Request, key: str):
    request.app.state.area_suggestions.dismiss(key)
    return {"dismissed": key}


@router.get("/{area_id}")
async def area_detail(request: Request, area_id: str):
    await request.app.state.hydrate_area_snapshot()
    try:
        return _svc(request).detail(area_id)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


async def _require_active_area(request: Request, area_id: str) -> None:
    if not await _sessions(request).get_area(area_id):
        raise HTTPException(status_code=404, detail="Area not found")


async def _finish_work_edit(request: Request, area_id: str, description: str) -> None:
    await request.app.state.emit_areas_changed([area_id])
    await request.app.state.request_area_wake(area_id, description)


@router.post("/{area_id}/outcomes")
async def create_area_outcome(request: Request, area_id: str, body: CreateAreaOutcomeRequest):
    await _require_active_area(request, area_id)
    try:
        outcome = await request.app.state.runtime.stores.area_work.create_outcome(
            area_id, **body.model_dump(), source="user"
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await _finish_work_edit(request, area_id, f"user created outcome '{body.title}'")
    return outcome


@router.patch("/{area_id}/outcomes/{key}")
async def update_area_outcome(
    request: Request, area_id: str, key: str, body: UpdateAreaOutcomeRequest,
):
    await _require_active_area(request, area_id)
    values = body.model_dump(exclude_unset=True)
    expected_updated_at = values.pop("expected_updated_at")
    try:
        outcome = await request.app.state.runtime.stores.area_work.update_outcome(
            area_id, key, expected_updated_at=expected_updated_at, **values
        )
    except AreaWorkConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if outcome is None:
        raise HTTPException(status_code=404, detail="Outcome not found")
    await _finish_work_edit(request, area_id, f"user updated outcome '{key}'")
    return outcome


@router.patch("/{area_id}/work/{key}")
async def update_area_work_item(
    request: Request, area_id: str, key: str, body: UpdateAreaWorkItemRequest,
):
    await _require_active_area(request, area_id)
    values = body.model_dump(exclude_unset=True)
    expected_updated_at = values.pop("expected_updated_at")
    try:
        item = await request.app.state.runtime.stores.area_work.update_work_item(
            area_id, key, expected_updated_at=expected_updated_at, **values
        )
    except AreaWorkConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if item is None:
        raise HTTPException(status_code=404, detail="Work item not found")
    await _finish_work_edit(request, area_id, f"user updated work item '{key}'")
    return item


@router.post("/{area_id}/page", response_model=AreaResponse)
async def create_area_page(request: Request, area_id: str):
    try:
        area = await _pages(request).create(area_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Area not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await request.app.state.emit_areas_changed([area_id])
    return area


@router.delete("/{area_id}/page", response_model=AreaResponse)
async def detach_area_page(request: Request, area_id: str):
    try:
        area = await _pages(request).detach(area_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Area not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await request.app.state.emit_areas_changed([area_id])
    return area


@router.patch("/{area_id}", response_model=AreaResponse)
async def update_area(request: Request, area_id: str, req: UpdateAreaRequest):
    patch = {key: getattr(req, key) for key in req.model_fields_set}
    try:
        area = await _lifecycle(request).update(area_id, **patch)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Area not found") from exc
    if not area:
        raise HTTPException(status_code=404, detail="Area not found")
    await request.app.state.emit_areas_changed([area_id])
    return area


@router.delete("/{area_id}")
async def archive_area(request: Request, area_id: str):
    archived = await _lifecycle(request).archive(area_id)
    if not archived:
        raise HTTPException(status_code=404, detail="Area not found")
    return {"status": "archived", "area_id": area_id}


@router.post("/{area_id}/restore", response_model=AreaResponse)
async def restore_area(request: Request, area_id: str):
    try:
        area = await _lifecycle(request).restore(area_id)
    except ValueError as exc:  # active Area already holds this name
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if area is None:
        raise HTTPException(status_code=404, detail="Archived Area not found")
    await request.app.state.emit_areas_changed([area_id])
    return area


@router.put("/{area_id}/autonomy")
async def update_area_autonomy(request: Request, area_id: str, body: AutonomyBody):
    svc = _sessions(request)
    existing = await svc.get_area(area_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Area not found")
    if body.autonomy is not None and not existing.get("page_path"):
        raise HTTPException(status_code=409, detail="Attach a page before granting an agent")
    try:
        area = await _lifecycle(request).update(area_id, autonomy=body.autonomy)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Area not found") from exc
    await request.app.state.emit_areas_changed([area_id])
    return area


@router.post("/{area_id}/asks/{ask_id}/resolve")
async def resolve_ask(request: Request, area_id: str, ask_id: str, body: ResolveBody):
    existing = _svc(request).get_ask(area_id, ask_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Ask not found in this Area")
    try:
        ask = _svc(request).resolve_ask(ask_id, body.state, body.snoozed_until, body.resolution)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    await request.app.state.emit_areas_changed([ask["area_key"]])
    # The user engaged — that's domain activity the custodian should absorb
    # (an approved review unblocks it; a dismissal is steering).
    await request.app.state.request_area_wake(
        area_id, f"user resolved ask '{ask['text'][:80]}' as {body.state}"
    )
    return ask


@router.post("/{area_id}/asks/{ask_id}/reply")
async def reply_to_ask(request: Request, area_id: str, ask_id: str, body: ReplyBody):
    ask = _svc(request).get_ask(area_id, ask_id)
    if ask is None:
        raise HTTPException(status_code=404, detail="Ask not found")
    runtime = request.app.state.runtime
    automation = await runtime.stores.automations.get(f"area:{area_id}")
    dispatch = runtime.dispatch_session_message
    if automation is None or not automation.thread_id or dispatch is None:
        raise HTTPException(status_code=409, detail="Custodian channel unavailable")
    message = f"REPLY TO ASK [{ask.id}]\n{body.message.strip()}"
    await dispatch(
        automation.thread_id,
        message,
        client_id=f"area-ask-reply:{ask.id}",
        skip_approvals=False,
        # The reply turn runs under the same permission contract as the
        # custodian's own runs — the allowlist is dispatch-borne.
        tool_scope=tuple(automation.tool_scope) if automation.tool_scope else None,
    )
    resolved = _svc(request).resolve_ask(ask.id, "done", None, "replied")
    await request.app.state.emit_areas_changed([area_id])
    return resolved
