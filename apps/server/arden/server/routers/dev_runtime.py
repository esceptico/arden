from fastapi import APIRouter, HTTPException, Request

router = APIRouter(tags=["runtime-dev"])


@router.post("/runtime/dev/schedules/{schedule_id}/dispatch")
async def dispatch_schedule(schedule_id: str, request: Request):
    runtime = getattr(request.app.state, "runtime", None)
    service = getattr(runtime, "automation_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="Automation service not available")
    task_id = f"fs:{schedule_id}"
    try:
        await service.get(task_id)
        await service.run_now(task_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Schedule {schedule_id!r} not found")
    return {"schedule_id": schedule_id, "task_id": task_id, "status": "queued"}
