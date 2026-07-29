import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query

from arden.automation.models import Automation
from arden.automation.service import AutomationService
from arden.notifiers.service import NotifierService
from arden.server.bus import BusRegistry
from arden.server.deps import (
    get_bus_registry,
    require_automation_service,
    require_notifier_service,
)
from arden.server.middleware import SSEStreamingResponse
from arden.server.runtime import Runtime, get_runtime
from arden.server.schemas import (
    CreateAutomationRequest,
    CreateNotifierRequest,
    UpdateAutomationRequest,
    UpdateNotifierRequest,
)
from arden.server.sse_stream import live_records, reset_chunk
from arden.server.sse_stream import replay_records as iter_replay_records

router = APIRouter(tags=["automations"])


def _automation_to_dict(a: Automation, recent_statuses: list[str] | None = None) -> dict:
    statuses = recent_statuses or []
    return {
        # Newest-first run outcomes for the card's sparkline + last-status pip.
        "recent_statuses": statuses,
        "last_status": statuses[0] if statuses else None,
        "task_id": a.task_id,
        "name": a.name,
        "description": a.description,
        "description_source": a.description_source,
        "prompt": a.prompt,
        "model": a.model,
        "triggers": [{"type": t.type, **t.params()} for t in a.triggers],
        "enabled": a.enabled,
        "created_at": a.created_at.isoformat(),
        "last_run_at": a.last_run_at.isoformat() if a.last_run_at else None,
        "next_run_at": a.next_run_at.isoformat() if a.next_run_at else None,
        "last_result": a.last_result,
        "auto_approve": a.auto_approve,
        "running_since": a.running_since.isoformat() if a.running_since else None,
        "handler": a.handler,
        "builtin": a.builtin,
        "cooldown_minutes": a.cooldown_minutes,
        "tool_scope": a.tool_scope,
        "output_schema": a.output_schema,
        "kind": a.kind,
        "read_history": a.read_history,
    }


@router.post("/automations")
async def create_automation(
    request: CreateAutomationRequest, svc: AutomationService = Depends(require_automation_service)
):
    try:
        automation = await svc.create(
            name=request.name,
            prompt=request.prompt,
            description=request.description,
            model=request.model,
            trigger_type=request.trigger_type,
            at=request.at,
            days=request.days,
            every=request.every,
            event_type=request.event_type,
            lead_minutes=request.lead_minutes,
            idle_minutes=request.idle_minutes,
            every_n=request.every_n,
            auto_approve=request.auto_approve,
            start=request.start,
            end=request.end,
            triggers=request.triggers,
            cooldown_minutes=request.cooldown_minutes,
            tool_scope=request.tool_scope,
            output_schema=request.output_schema,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _automation_to_dict(automation)


@router.get("/automations")
async def list_automations(svc: AutomationService = Depends(require_automation_service)):
    automations = await svc.list_all()
    statuses = await svc.store.recent_run_statuses([a.task_id for a in automations])
    return {"automations": [_automation_to_dict(a, statuses.get(a.task_id, [])) for a in automations]}


KEEPALIVE_INTERVAL = 5
AUTOMATION_BUS_KEY = "automation:events"


async def _bus_for_automation_event_stream(bus_registry: BusRegistry, event_store=None):
    bus = bus_registry.get(AUTOMATION_BUS_KEY)
    if event_store is None:
        return bus or bus_registry.get_or_create(AUTOMATION_BUS_KEY)

    latest_seq = await event_store.get_latest_session_event_seq(AUTOMATION_BUS_KEY)
    if latest_seq:
        bus_registry.remember_session_cursor(
            AUTOMATION_BUS_KEY,
            next_seq=latest_seq + 1,
            checkpoint_seq=0,
        )
        return bus_registry.get_or_create(AUTOMATION_BUS_KEY)

    return bus or bus_registry.get_or_create(AUTOMATION_BUS_KEY)


async def _automation_event_stream(bus_registry: BusRegistry, after_seq: int | None = None, event_store=None):
    bus = await _bus_for_automation_event_stream(bus_registry, event_store)
    subscription = bus.subscribe_with_replay(after_seq=after_seq) if after_seq is not None else None
    queue = subscription.queue if subscription is not None else bus.subscribe()

    def should_emit(_event) -> bool:
        return True

    try:
        if subscription is not None and subscription.replay_gap:
            newest_seq = bus.next_seq - 1
            reset_seq = min(max(after_seq + 1, bus.checkpoint_seq), newest_seq)
            yield reset_chunk(bus.session_id, "replay_gap", reset_seq)
            await asyncio.sleep(0)

        if subscription is not None:
            async for chunk in iter_replay_records(bus.session_id, subscription.snapshot, should_emit=should_emit):
                yield chunk

        async for chunk in live_records(
            bus=bus,
            queue=queue,
            session_id=AUTOMATION_BUS_KEY,
            should_emit=should_emit,
            keepalive_interval=KEEPALIVE_INTERVAL,
        ):
            yield chunk
    except asyncio.CancelledError:
        pass
    finally:
        bus.unsubscribe(queue)


def _effective_after_seq(query_after_seq: int | None, last_event_id: str | None) -> int | None:
    """Resume cursor from the query param OR the standard Last-Event-ID header
    (re-sent by SSE clients like @microsoft/fetch-event-source on reconnect),
    whichever is higher. A malformed header is ignored rather than failing the
    reconnect — at worst the client replays from the query cursor / live."""
    header_after_seq: int | None = None
    if last_event_id is not None:
        try:
            parsed = int(last_event_id.strip())
        except ValueError:
            parsed = -1
        if parsed >= 0:
            header_after_seq = parsed
    if query_after_seq is None:
        return header_after_seq
    if header_after_seq is None:
        return query_after_seq
    return max(query_after_seq, header_after_seq)


@router.get("/automations/events")
async def automation_events(
    runtime: Runtime = Depends(get_runtime),
    bus_registry: BusRegistry = Depends(get_bus_registry),
    after_seq: Annotated[int | None, Query(ge=0)] = None,
    last_event_id: Annotated[str | None, Header()] = None,
):
    effective_after_seq = _effective_after_seq(after_seq, last_event_id)
    session_service = getattr(runtime, "session_service", None)
    event_store = session_service.store if session_service else None
    return SSEStreamingResponse(
        _automation_event_stream(bus_registry, after_seq=effective_after_seq, event_store=event_store),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/automations/{task_id}")
async def get_automation(task_id: str, svc: AutomationService = Depends(require_automation_service)):
    try:
        automation = await svc.get(task_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Automation not found")

    statuses = await svc.store.recent_run_statuses([task_id])
    return _automation_to_dict(automation, statuses.get(task_id, []))


@router.post("/automations/{task_id}/description/generate")
async def generate_automation_description(
    task_id: str,
    svc: AutomationService = Depends(require_automation_service),
):
    try:
        automation = await svc.generate_description(task_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Automation not found")
    return _automation_to_dict(automation)


@router.get("/automations/{task_id}/runs")
async def list_automation_runs(
    task_id: str,
    limit: int = Query(default=20, ge=1, le=200),
    svc: AutomationService = Depends(require_automation_service),
):
    """Recent run history for an automation — did it fire, and what did it do?"""
    return {"runs": await svc.store.list_runs(task_id, limit)}


@router.post("/automations/{task_id}/toggle")
async def toggle_automation(task_id: str, svc: AutomationService = Depends(require_automation_service)):
    try:
        new_enabled = await svc.toggle_enabled(task_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Automation not found")
    return {"enabled": new_enabled}


@router.post("/automations/{task_id}/auto-approve")
async def toggle_auto_approve(task_id: str, svc: AutomationService = Depends(require_automation_service)):
    try:
        new_auto_approve = await svc.toggle_auto_approve(task_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Automation not found")
    return {"auto_approve": new_auto_approve}


@router.post("/automations/{task_id}/run")
async def run_automation(task_id: str, svc: AutomationService = Depends(require_automation_service)):
    try:
        await svc.run_now(task_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Automation not found")
    except RuntimeError:
        raise HTTPException(status_code=503, detail="Scheduler not available")
    return {"status": "started"}


@router.patch("/automations/{task_id}")
async def update_automation(
    task_id: str, request: UpdateAutomationRequest, svc: AutomationService = Depends(require_automation_service)
):
    try:
        automation = await svc.update(
            task_id,
            name=request.name,
            prompt=request.prompt,
            description=request.description,
            model=request.model,
            trigger_type=request.trigger_type,
            at=request.at,
            days=request.days,
            every=request.every,
            event_type=request.event_type,
            lead_minutes=request.lead_minutes,
            idle_minutes=request.idle_minutes,
            every_n=request.every_n,
            start=request.start,
            end=request.end,
            auto_approve=request.auto_approve,
            enabled=request.enabled,
            triggers=request.triggers,
            cooldown_minutes=request.cooldown_minutes,
            tool_scope=request.tool_scope,
            output_schema=request.output_schema,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="Automation not found")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _automation_to_dict(automation)


@router.get("/notifiers")
async def list_notifiers(runtime: Runtime = Depends(get_runtime)):
    if not runtime.notifier_service:
        return {"notifiers": []}
    return {"notifiers": runtime.notifier_service.list_summary()}


@router.delete("/automations/{task_id}")
async def delete_automation(task_id: str, svc: AutomationService = Depends(require_automation_service)):
    try:
        disabled_children = await svc.delete(task_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Automation not found")
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))
    return {"status": "deleted", "disabled_children": disabled_children}


# --- Notifier config CRUD ---


@router.get("/notifiers/configs")
async def list_notifier_configs(svc: NotifierService = Depends(require_notifier_service)):
    configs = await svc.list_configs()
    return {
        "configs": [
            {
                "name": c.name,
                "type": c.type,
                "config": c.config,
                "created_at": c.created_at.isoformat(),
            }
            for c in configs
        ]
    }


@router.get("/notifiers/types")
async def list_notifier_types(svc: NotifierService = Depends(require_notifier_service)):
    return {"types": svc.get_types()}


@router.post("/notifiers/configs")
async def create_notifier_config(
    request: CreateNotifierRequest, svc: NotifierService = Depends(require_notifier_service)
):
    try:
        cfg = await svc.create(request.name, request.type, request.config)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {"name": cfg.name, "type": cfg.type, "config": cfg.config, "created_at": cfg.created_at.isoformat()}


@router.put("/notifiers/configs/{name}")
async def update_notifier_config(
    name: str, request: UpdateNotifierRequest, svc: NotifierService = Depends(require_notifier_service)
):
    try:
        cfg = await svc.update(name, request.config, new_name=request.name)
    except KeyError:
        raise HTTPException(status_code=404, detail="Notifier not found")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {"name": cfg.name, "type": cfg.type, "config": cfg.config, "created_at": cfg.created_at.isoformat()}


@router.delete("/notifiers/configs/{name}")
async def delete_notifier_config(name: str, svc: NotifierService = Depends(require_notifier_service)):
    try:
        await svc.delete(name)
    except KeyError:
        raise HTTPException(status_code=404, detail="Notifier not found")

    return {"status": "deleted"}


@router.post("/notifiers/configs/{name}/test")
async def test_notifier(name: str, svc: NotifierService = Depends(require_notifier_service)):
    try:
        await svc.test(name)
    except KeyError:
        raise HTTPException(status_code=404, detail="Notifier not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return {"status": "sent"}
