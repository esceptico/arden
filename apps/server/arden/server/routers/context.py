from fastapi import APIRouter, Depends, HTTPException

from arden.core.compactor import compactable_range
from arden.events.sse import CompactionFinishedEvent, CompactionStartedEvent
from arden.llm.models import get_model
from arden.server.bus import BusRegistry, prime_bus_cursor_from_store
from arden.server.deps import get_bus_registry, require_session_service
from arden.server.runtime import Runtime, get_runtime
from arden.server.schemas import CompactRequest, UpdateDirectivesRequest
from arden.services.session import SessionService, compact_session
from arden.tools.deferred import is_deferred_tool, tool_schema_names, visible_tool_names
from arden.tools.directives import load_directives, save_directives

router = APIRouter(tags=["context"])


@router.get("/context")
async def get_context_usage(
    runtime: Runtime = Depends(get_runtime),
    svc: SessionService = Depends(require_session_service),
    session_id: str | None = None,
):
    model = runtime.config.chat_model
    if not model:
        raise HTTPException(status_code=503, detail="No chat model configured")
    model_limit = get_model(model).max_context_tokens

    data = await svc.load(session_id)
    resolved_session_id = data.state.session_id if data else session_id
    active_run = runtime.run_registry.get_active_run(resolved_session_id) if resolved_session_id else None
    messages = active_run.messages if active_run else (data.messages if data else [])
    message_count = len(messages)
    last_input_tokens = data.last_input_tokens if data else None
    tools = runtime.executor.get_tools() if runtime.executor else []
    allowed_tool_names = tool_schema_names(tools)
    loaded_tools = active_run.loaded_tools if active_run else set()
    capabilities = frozenset(runtime.executor.tool_services) if runtime.executor else frozenset()
    visible_tools = (
        visible_tool_names(
            runtime.executor.registry,
            capabilities,
            loaded_tools,
            allowed_names=allowed_tool_names,
        )
        if runtime.executor
        else set()
    )
    deferred_tools = {
        name for name in allowed_tool_names if runtime.executor and is_deferred_tool(name, runtime.executor.registry)
    }
    loaded_deferred_tools = loaded_tools & deferred_tools

    return {
        "model": model,
        "limit": model_limit,
        "total": last_input_tokens,
        "message_count": message_count,
        "tool_count": len(tools),
        "visible_tool_count": len(visible_tools),
        "deferred_tool_count": len(deferred_tools),
        "loaded_tool_count": len(loaded_deferred_tools),
    }


@router.post("/compact")
async def compact_context(
    runtime: Runtime = Depends(get_runtime),
    buses: BusRegistry = Depends(get_bus_registry),
    req: CompactRequest | None = None,
):
    session_id = req.session_id if req else None

    data = await runtime.session_service.load(session_id)
    resolved_session_id = data.state.session_id if data else session_id
    if resolved_session_id and runtime.run_registry.get_active_run(resolved_session_id):
        raise HTTPException(
            status_code=409,
            detail="Cannot compact while a chat run is active",
        )

    if not data:
        return {"status": "no_session", "message": "No active session to compact"}

    if compactable_range(data.messages, keep_ratio=runtime.config.compression_keep_ratio) is None:
        return {
            "status": "nothing_to_compact",
            "message": f"Nothing to compact ({len(data.messages)} messages)",
            "message_count": len(data.messages),
            "before_tokens": data.last_input_tokens,
        }

    if resolved_session_id:
        await prime_bus_cursor_from_store(buses, resolved_session_id, runtime.session_service.store.events)
    bus = buses.get_or_create(resolved_session_id) if resolved_session_id else None

    if bus:
        await bus.emit(CompactionStartedEvent(run_id=""))

    try:
        result = await compact_session(
            runtime.session_service,
            model=runtime.config.chat_model,
            session_id=session_id,
            keep_ratio=runtime.config.compression_keep_ratio,
            summary_max_tokens=runtime.config.summary_max_tokens,
        )
    except Exception as e:
        if bus:
            same = int(len(data.messages) if data else 0)
            await bus.emit(CompactionFinishedEvent(run_id="", messages_before=same, messages_after=same))
        raise HTTPException(status_code=500, detail=str(e))

    if bus:
        before = int(result.get("before_messages", result.get("message_count", 0)) or 0)
        after = int(result.get("after_messages", result.get("message_count", before)) or before)
        await bus.emit(CompactionFinishedEvent(run_id="", messages_before=before, messages_after=after))
    return result


@router.get("/directives")
async def get_directives():
    return {"content": load_directives() or ""}


@router.put("/directives")
async def update_directives(req: UpdateDirectivesRequest):
    save_directives(req.content)
    return {"content": req.content.strip()}
