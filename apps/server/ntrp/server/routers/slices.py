"""Slices router — a container's automations/sessions/asks, keyed by
project_id (slices and projects are one concept; a slice = a project with
capabilities). Wired onto app.state (SliceService is a plain constructor
with injected callables, not a FastAPI Depends chain)."""

from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter(prefix="/slices", tags=["slices"])


class ResolveBody(BaseModel):
    state: Literal["dismissed", "done", "snoozed"]
    snoozed_until: str | None = None


class AutonomyBody(BaseModel):
    autonomy: Literal["observe", "act"]


class AttachBody(BaseModel):
    """Grow capabilities on a container: attach a page (+observe agent) to an
    existing project, or mint a new one. Exactly one of project_id | name."""

    project_id: str | None = None
    name: str | None = None
    page_path: str


def _svc(request: Request):
    return request.app.state.slice_service


def _sessions(request: Request):
    return request.app.state.runtime.session_service


@router.get("")
async def list_slices(request: Request):
    await request.app.state.hydrate_slice_snapshot()
    svc = _svc(request)
    svc.refresh_mechanical()
    overview = svc.overview()
    # Suggestions are page-keyed; a page already attached anywhere must not
    # resurface as a suggestion.
    attached = {Path(s["page_path"]).stem for s in overview["slices"] if s.get("page_path")}
    overview["suggested"] = request.app.state.slice_suggestions.list(exclude_keys=attached)
    return overview


@router.post("/suggestions/{key}/dismiss")
async def dismiss_suggestion(request: Request, key: str):
    request.app.state.slice_suggestions.dismiss(key)
    return {"dismissed": key}


@router.get("/{project_id}")
async def slice_detail(request: Request, project_id: str):
    await request.app.state.hydrate_slice_snapshot()
    try:
        return _svc(request).detail(project_id)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.post("/{project_id}/asks/{ask_id}/resolve")
async def resolve_ask(request: Request, project_id: str, ask_id: str, body: ResolveBody):
    try:
        ask = _svc(request).resolve_ask(ask_id, body.state, body.snoozed_until)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    if ask["slice_key"] != project_id:
        raise HTTPException(
            status_code=404,
            detail=f"ask '{ask_id}' belongs to slice '{ask['slice_key']}', not '{project_id}'",
        )
    await request.app.state.emit_slices_changed([ask["slice_key"]])
    return ask


@router.put("/{project_id}")
async def update_slice_autonomy(request: Request, project_id: str, body: AutonomyBody):
    svc = _sessions(request)
    existing = await svc.get_project(project_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Project not found")
    if not existing.get("page_path"):
        raise HTTPException(status_code=409, detail="Attach a page before granting an agent")
    project = await svc.update_project(project_id, autonomy=body.autonomy)
    await request.app.state.emit_slices_changed([project_id])
    return project


@router.post("")
async def attach_slice(request: Request, body: AttachBody):
    if bool(body.project_id) == bool(body.name):
        raise HTTPException(status_code=422, detail="exactly one of project_id or name")
    svc = _sessions(request)
    if body.project_id:
        project = await svc.update_project(body.project_id, page_path=body.page_path, autonomy="observe")
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
    else:
        project = await svc.create_project(name=body.name, page_path=body.page_path, autonomy="observe")
    await request.app.state.emit_slices_changed([project["project_id"]])
    return project
