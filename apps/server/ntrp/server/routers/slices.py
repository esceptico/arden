"""Slices router — a project's automations/sessions/asks grouped under a
`slice_key` (mirrors project_id). Wired onto app.state (SliceService is a
plain constructor with injected callables, not a FastAPI Depends chain)."""

from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter(prefix="/slices", tags=["slices"])


class ResolveBody(BaseModel):
    state: Literal["dismissed", "done", "snoozed"]
    snoozed_until: str | None = None


class AutonomyBody(BaseModel):
    autonomy: Literal["observe", "act"]


class CreateBody(BaseModel):
    key: str
    title: str
    page_path: str


def _svc(request: Request):
    return request.app.state.slice_service


@router.get("")
async def list_slices(request: Request):
    await request.app.state.hydrate_slice_snapshot()
    svc = _svc(request)
    svc.refresh_mechanical()
    overview = svc.overview()
    existing = {s["key"] for s in overview["slices"]}
    overview["suggested"] = request.app.state.slice_suggestions.list(exclude_keys=existing)
    return overview


@router.post("/suggestions/{key}/dismiss")
async def dismiss_suggestion(request: Request, key: str):
    request.app.state.slice_suggestions.dismiss(key)
    return {"dismissed": key}


@router.get("/{key}")
async def slice_detail(request: Request, key: str):
    await request.app.state.hydrate_slice_snapshot()
    try:
        return _svc(request).detail(key)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.post("/{key}/asks/{ask_id}/resolve")
async def resolve_ask(request: Request, key: str, ask_id: str, body: ResolveBody):
    try:
        ask = _svc(request).resolve_ask(ask_id, body.state, body.snoozed_until)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    if ask["slice_key"] != key:
        raise HTTPException(status_code=404, detail=f"ask '{ask_id}' belongs to slice '{ask['slice_key']}', not '{key}'")
    await request.app.state.emit_slices_changed([ask["slice_key"]])
    return ask


# INTERIM (dies in the unification's router rewrite): autonomy + create still
# write the registry file until the boot migration folds it into projects.
@router.put("/{key}")
async def update_slice_autonomy(request: Request, key: str, body: AutonomyBody):
    from dataclasses import asdict

    try:
        slice_ = request.app.state.slice_registry.update_autonomy(key, body.autonomy)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    await request.app.state.emit_slices_changed([key])
    return asdict(slice_)


@router.post("")
async def create_slice(request: Request, body: CreateBody):
    from dataclasses import asdict

    try:
        slice_ = request.app.state.slice_registry.create(body.key, body.title, body.page_path)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    await request.app.state.emit_slices_changed([body.key])
    return asdict(slice_)
