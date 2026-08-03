from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query, Request

from arden.server.deps import require_automation_runtime, require_tool_executor
from arden.server.middleware import _extract_bearer_token
from arden.server.runtime import Runtime, get_runtime
from arden.server.runtime.automation import AutomationRuntime
from arden.server.schemas import (
    HealthResponse,
    OutboxPruneResponse,
    OutboxReplayResponse,
    OutboxStatusResponse,
    ReplayOutboxRequest,
    SchedulerStatusResponse,
)
from arden.settings import verify_api_key
from arden.tools.executor import ToolExecutor

router = APIRouter(tags=["ops"])


@router.get("/health", response_model=HealthResponse, response_model_exclude_none=True)
async def health(request: Request, runtime: Runtime = Depends(get_runtime)):
    result: dict = {
        "status": "ok" if runtime.connected else "unavailable",
        "version": request.app.version,
        "has_providers": runtime.config.has_any_model,
        "outbox": await runtime.get_outbox_health(),
        "warmup": runtime.warmup_status(),
        "storage": runtime.storage_status(),
        **runtime.config_status(),
    }
    token = _extract_bearer_token(request)
    if token and runtime.config.api_key_hash:
        result["auth"] = verify_api_key(token, runtime.config.api_key_hash)
    return result


@router.get("/outbox/status", response_model=OutboxStatusResponse)
async def get_outbox_status(automation: AutomationRuntime = Depends(require_automation_runtime)):
    return await automation.get_outbox_status()


@router.post("/outbox/dead/replay", response_model=OutboxReplayResponse)
async def replay_outbox_dead_events(
    request: ReplayOutboxRequest,
    automation: AutomationRuntime = Depends(require_automation_runtime),
):
    return await automation.replay_outbox_dead_events(request.event_ids)


@router.delete("/outbox/completed", response_model=OutboxPruneResponse)
async def prune_outbox_completed(
    older_than_days: int = Query(default=1, ge=1, le=3650),
    limit: int = Query(default=1000, ge=1, le=10000),
    automation: AutomationRuntime = Depends(require_automation_runtime),
):
    before = datetime.now(UTC) - timedelta(days=older_than_days)
    result = await automation.prune_outbox_completed(before=before, limit=limit)
    return {**result, "older_than_days": older_than_days}


@router.get("/index/status")
async def get_index_status(runtime: Runtime = Depends(get_runtime)):
    return await runtime.get_index_status()


@router.get("/storage/status")
async def get_storage_status(runtime: Runtime = Depends(get_runtime)):
    return runtime.storage_status()


@router.post("/storage/maintain")
async def maintain_storage(runtime: Runtime = Depends(get_runtime)):
    return await runtime.run_storage_maintenance_once()


@router.post("/index/start")
async def start_indexing(runtime: Runtime = Depends(get_runtime)):
    await runtime.start_indexing()
    return await runtime.get_index_status()


@router.get("/scheduler/status", response_model=SchedulerStatusResponse, response_model_exclude_none=True)
async def get_scheduler_status(automation: AutomationRuntime = Depends(require_automation_runtime)):
    return await automation.get_scheduler_status()


@router.get("/tools")
async def list_tools(executor: ToolExecutor = Depends(require_tool_executor)):
    return {"tools": executor.get_tool_metadata()}
