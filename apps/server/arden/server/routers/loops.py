from fastapi import APIRouter, Depends, HTTPException

from arden.automation.models import Automation
from arden.automation.service import AutomationService
from arden.server.deps import require_automation_service
from arden.server.schemas import CreateLoopRequest, UpdateLoopRequest

router = APIRouter(tags=["loops"])


def _loop_to_dict(a: Automation) -> dict:
    every = a.triggers[0].params().get("every") if a.triggers else None
    return {
        "task_id": a.task_id,
        "session_id": a.thread_id,
        "prompt": a.description,
        "every": every,
        "enabled": a.enabled,
        "iteration_count": a.iteration_count,
        "max_iterations": a.max_iterations,
        "stop_when": a.stop_when,
        "max_age_days": a.max_age_days,
        "created_at": a.created_at.isoformat(),
        "next_run_at": a.next_run_at.isoformat() if a.next_run_at else None,
        "last_run_at": a.last_run_at.isoformat() if a.last_run_at else None,
        "last_result": a.last_result,
        "running_since": a.running_since.isoformat() if a.running_since else None,
    }


@router.post("/loops")
async def create_loop(
    request: CreateLoopRequest,
    svc: AutomationService = Depends(require_automation_service),
):
    try:
        loop = await svc.create_loop(
            session_id=request.session_id,
            prompt=request.prompt,
            every=request.every,
            max_iterations=request.max_iterations,
            stop_when=request.stop_when,
            max_age_days=request.max_age_days,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _loop_to_dict(loop)


@router.get("/loops")
async def list_loops(
    session_id: str,
    svc: AutomationService = Depends(require_automation_service),
):
    loops = await svc.list_loops_by_session(session_id)
    return {"loops": [_loop_to_dict(loop) for loop in loops]}


@router.patch("/loops/{task_id}")
async def update_loop(
    task_id: str,
    request: UpdateLoopRequest,
    svc: AutomationService = Depends(require_automation_service),
):
    try:
        loop = await svc.get(task_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Loop not found")
    if loop.kind != "loop":
        raise HTTPException(status_code=400, detail=f"{task_id} is not a loop")

    name = description = None
    if request.prompt is not None:
        prompt = request.prompt.strip()
        if not prompt:
            raise HTTPException(status_code=400, detail="prompt cannot be empty")
        name = f"Loop: {prompt[:40]}"
        description = prompt

    try:
        updated = await svc.update(
            task_id,
            name=name,
            description=description,
            every=request.every,
            enabled=request.enabled,
            max_iterations=request.max_iterations,
            stop_when=request.stop_when,
            max_age_days=request.max_age_days,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return _loop_to_dict(updated)


@router.delete("/loops/{task_id}")
async def delete_loop(
    task_id: str,
    svc: AutomationService = Depends(require_automation_service),
):
    try:
        loop = await svc.get(task_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Loop not found")
    if loop.kind != "loop":
        raise HTTPException(status_code=400, detail=f"{task_id} is not a loop")
    try:
        disabled_children = await svc.delete(task_id)
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))
    return {"status": "deleted", "disabled_children": disabled_children}
