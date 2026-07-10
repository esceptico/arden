"""Areas router — the single API surface for areas (the one container:
chats file into an area; a page + standing agent are optional capabilities).
Detail/asks/suggestions ride app.state (AreaService is a plain constructor
with injected callables); CRUD goes through the session service's store."""

from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ntrp.server.schemas import AreaResponse, CreateAreaRequest, UpdateAreaRequest

router = APIRouter(prefix="/areas", tags=["areas"])


class ResolveBody(BaseModel):
    state: Literal["dismissed", "done", "snoozed"]
    snoozed_until: str | None = None


class AutonomyBody(BaseModel):
    autonomy: Literal["observe", "act"]


def _svc(request: Request):
    return request.app.state.area_service


def _sessions(request: Request):
    return request.app.state.runtime.session_service


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
    svc = _sessions(request)
    existing = await svc.find_area_by_name(req.name)
    if existing:
        if req.page_path:
            area = await svc.update_area(
                existing["area_id"],
                page_path=req.page_path,
                autonomy=existing.get("autonomy") or "observe",
            )
        else:
            area = existing
    else:
        try:
            area = await svc.create_area(
                name=req.name,
                default_cwd=req.default_cwd,
                instructions=req.instructions,
                knowledge_scope=req.knowledge_scope,
                page_path=req.page_path,
                autonomy="observe" if req.page_path else None,
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


@router.patch("/{area_id}", response_model=AreaResponse)
async def update_area(request: Request, area_id: str, req: UpdateAreaRequest):
    svc = _sessions(request)
    patch = {key: getattr(req, key) for key in req.model_fields_set}
    try:
        area = await svc.update_area(area_id, **patch)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not area:
        raise HTTPException(status_code=404, detail="Area not found")
    if "paused" in patch:
        # Pause is enforced at the switch: the agent automation is disabled
        # outright (no half-alive runs), re-enabled on resume — a stale due
        # time then fires promptly, so resuming reads as waking up.
        automations = request.app.state.runtime.stores.automations
        if await automations.get(f"area:{area_id}"):
            await automations.set_enabled(f"area:{area_id}", not patch["paused"])
    await request.app.state.emit_areas_changed([area_id])
    return area


@router.delete("/{area_id}")
async def archive_area(request: Request, area_id: str):
    archived = await _sessions(request).archive_area(area_id)
    if not archived:
        raise HTTPException(status_code=404, detail="Area not found")
    return {"status": "archived", "area_id": area_id}


@router.put("/{area_id}/autonomy")
async def update_area_autonomy(request: Request, area_id: str, body: AutonomyBody):
    svc = _sessions(request)
    existing = await svc.get_area(area_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Area not found")
    if not existing.get("page_path"):
        raise HTTPException(status_code=409, detail="Attach a page before granting an agent")
    area = await svc.update_area(area_id, autonomy=body.autonomy)
    await request.app.state.emit_areas_changed([area_id])
    return area


@router.post("/{area_id}/asks/{ask_id}/resolve")
async def resolve_ask(request: Request, area_id: str, ask_id: str, body: ResolveBody):
    try:
        ask = _svc(request).resolve_ask(ask_id, body.state, body.snoozed_until)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    if ask["area_key"] != area_id:
        raise HTTPException(
            status_code=404,
            detail=f"ask '{ask_id}' belongs to area '{ask['area_key']}', not '{area_id}'",
        )
    await request.app.state.emit_areas_changed([ask["area_key"]])
    # The user engaged — that's domain activity the custodian should absorb
    # (an approved review unblocks it; a dismissal is steering).
    await request.app.state.request_area_wake(
        area_id, f"user resolved ask '{ask['text'][:80]}' as {body.state}"
    )
    return ask
