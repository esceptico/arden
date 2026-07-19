import asyncio
import json
import time
from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from ntrp.events.sse import (
    EPHEMERAL_EVENT_TYPES,
    ApprovalNeededEvent,
    ConnectionNeededEvent,
    InputNeededEvent,
    TextMessageContentEvent,
)
from ntrp.integrations.base import IntegrationConnectionDescriptor, IntegrationConnectionError
from ntrp.logging import get_logger
from ntrp.server.bus import BusRegistry, StreamRecord
from ntrp.server.deps import get_bus_registry, require_run_registry
from ntrp.server.middleware import SSEStreamingResponse
from ntrp.server.runtime import Runtime, get_runtime
from ntrp.server.schemas import (
    BackgroundAgentRunsResponse,
    BackgroundRequest,
    CancelRequest,
    ChatRequest,
    ChatRunsStatusResponse,
    ChildAgentResultResponse,
    ConnectionResultRequest,
    InjectChildAgentRequest,
    ToolResultRequest,
)
from ntrp.server.sse_stream import keepalive_chunk, live_records, reset_chunk
from ntrp.server.sse_stream import replay_records as iter_replay_records
from ntrp.server.state import RunRegistry, RunStatus
from ntrp.services.chat import ChatIdempotencyConflict, ChatSessionNotFound, submit_chat_message

router = APIRouter(tags=["chat"])
_logger = get_logger(__name__)

KEEPALIVE_INTERVAL = 5
CHILD_AGENT_TERMINAL_STATUSES = {"completed", "failed", "cancelled", "interrupted"}


def _keepalive(session_id: str, latest_seq: int) -> str:
    return keepalive_chunk(session_id, latest_seq)


def _parse_last_event_id(value: str | None) -> int | None:
    if value is None or value.strip() == "":
        return None
    try:
        parsed = int(value.strip())
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid Last-Event-ID") from None
    if parsed < 0:
        raise HTTPException(status_code=400, detail="Invalid Last-Event-ID")
    return parsed


def _effective_after_seq(query_after_seq: int | None, last_event_id: str | None) -> int | None:
    header_after_seq = _parse_last_event_id(last_event_id)
    if query_after_seq is None:
        return header_after_seq
    if header_after_seq is None:
        return query_after_seq
    return max(query_after_seq, header_after_seq)


async def _bus_for_event_stream(session_id: str, bus_registry: BusRegistry, event_store=None):
    bus = bus_registry.get(session_id)
    if event_store is None:
        return bus or bus_registry.get_or_create(session_id)

    latest_seq = await event_store.get_latest_session_event_seq(session_id)
    if latest_seq:
        checkpoint_seq = await event_store.get_latest_session_checkpoint_seq(session_id)
        # session_events is only a cursor ledger; chat_runs.last_seq is
        # the persisted canonical checkpoint written after save/save_progress.
        bus_registry.remember_session_cursor(
            session_id,
            next_seq=latest_seq + 1,
            checkpoint_seq=checkpoint_seq,
        )
        return bus_registry.get_or_create(session_id)

    return bus or bus_registry.get_or_create(session_id)


async def _event_stream(
    session_id: str,
    bus_registry: BusRegistry,
    run_registry: RunRegistry,
    stream: bool = False,
    after_seq: int | None = None,
    event_store=None,
) -> AsyncGenerator[str]:
    bus = await _bus_for_event_stream(session_id, bus_registry, event_store)
    subscription = bus.subscribe_with_replay(after_seq=after_seq)
    snapshot, queue = subscription
    # Boundary for durable replay. Because subscribe_with_replay() has no
    # await, no live emit can interleave between subscription and this read.
    # Events emitted after this point arrive on the live queue; replay only
    # serves records <= replay_upper_seq to avoid DB/live duplicates.
    replay_upper_seq = bus.next_seq - 1

    def should_emit(event) -> bool:
        return stream or not isinstance(event, TextMessageContentEvent)

    # Catch-up replay (durable + in-memory snapshot) emits only STRUCTURAL
    # events — never the ephemeral deltas (token text, tool args, reasoning).
    # A client joining a long/ongoing run must land on the settled current
    # state, not re-stream thousands of historical deltas (the "full replay of
    # tool calls" churn). This mirrors what's persisted, so the in-memory
    # snapshot replay matches the durable DB replay exactly; the live tail
    # (after replay_upper_seq) still carries full deltas for whatever is
    # actively streaming right now.
    def should_replay(event) -> bool:
        return event.type not in EPHEMERAL_EVENT_TYPES

    durable_pending_approval_ids: set[str] | None = None
    durable_pending_connection_ids: set[str] | None = None

    async def is_pending_approval(tool_id: str) -> bool:
        active_run = run_registry.get_active_run(session_id)
        future = active_run.pending_approvals.get(tool_id) if active_run else None
        if future is not None:
            return not future.done()

        nonlocal durable_pending_approval_ids
        if durable_pending_approval_ids is None:
            list_pending = getattr(event_store, "list_pending_tool_approvals", None)
            if list_pending is None:
                durable_pending_approval_ids = set()
            else:
                rows = await list_pending(session_id)
                durable_pending_approval_ids = {
                    row["tool_call_id"] for row in rows if isinstance(row.get("tool_call_id"), str)
                }
        return tool_id in durable_pending_approval_ids

    def is_pending_input(tool_id: str) -> bool:
        active_run = run_registry.get_active_run(session_id)
        future = active_run.pending_inputs.get(tool_id) if active_run else None
        return future is not None and not future.done()

    async def is_pending_connection(tool_id: str) -> bool:
        active_run = run_registry.get_active_run(session_id)
        future = active_run.pending_connections.get(tool_id) if active_run else None
        if future is not None:
            return not future.done()

        nonlocal durable_pending_connection_ids
        if durable_pending_connection_ids is None:
            list_pending = getattr(event_store, "list_pending_integration_connections", None)
            if list_pending is None:
                durable_pending_connection_ids = set()
            else:
                rows = await list_pending(session_id)
                durable_pending_connection_ids = {
                    row["tool_call_id"] for row in rows if isinstance(row.get("tool_call_id"), str)
                }
        return tool_id in durable_pending_connection_ids

    async def filter_replay_records(records: list[StreamRecord]) -> list[StreamRecord]:
        filtered: list[StreamRecord] = []
        for record in records:
            # approval_needed is a UI edge; the canonical state is the live
            # Future / durable tool_approvals row. Do not replay stale cards.
            if isinstance(record.event, ApprovalNeededEvent) and not await is_pending_approval(record.event.tool_id):
                continue
            # input_needed is a UI edge; the resolved state replays via the
            # durable TOOL_CALL_RESULT. Do not replay stale input cards.
            if isinstance(record.event, InputNeededEvent) and not is_pending_input(record.event.tool_id):
                continue
            if isinstance(record.event, ConnectionNeededEvent) and not await is_pending_connection(record.event.tool_id):
                continue
            filtered.append(record)
        return filtered

    async def durable_replay_records() -> list[StreamRecord] | None:
        if event_store is None or after_seq is None:
            return None
        if after_seq >= replay_upper_seq:
            return []
        records = await event_store.list_session_events(session_id, after_seq=after_seq, limit=10000)
        records = [record for record in records if record.seq <= replay_upper_seq]
        # session_events is a SPARSE ledger: ephemeral deltas (token text, tool
        # args, reasoning — see EPHEMERAL_EVENT_TYPES) are intentionally NOT
        # persisted, so seqs are non-contiguous by design. A hole is NOT a gap —
        # the omitted seqs are transient deltas the client re-derives from the
        # persisted START/END + result rows. Requiring contiguity here made
        # nearly every resume return None → a bogus replay_gap reset → a
        # reload loop. The only real concern is whether the DB has caught up to
        # the live boundary; if not, fall back to the in-memory buffer/snapshot.
        if not records or records[-1].seq != replay_upper_seq:
            return None
        return records

    try:
        if after_seq is not None and after_seq > replay_upper_seq:
            yield reset_chunk(session_id, "future_cursor", replay_upper_seq)
            await asyncio.sleep(0)
        elif after_seq is not None and after_seq < bus.checkpoint_seq:
            yield reset_chunk(session_id, "replay_gap", bus.checkpoint_seq)
            await asyncio.sleep(0)
            async for chunk in iter_replay_records(
                session_id,
                await filter_replay_records(snapshot),
                should_emit=should_replay,
            ):
                yield chunk
        else:
            durable_records = await durable_replay_records()
            if durable_records is None:
                if subscription.replay_gap and after_seq is not None:
                    reset_seq = min(max(after_seq + 1, bus.checkpoint_seq), replay_upper_seq)
                    yield reset_chunk(session_id, "replay_gap", reset_seq)
                    await asyncio.sleep(0)
                records_to_replay = snapshot
            else:
                records_to_replay = durable_records

            async for chunk in iter_replay_records(
                session_id,
                await filter_replay_records(records_to_replay),
                should_emit=should_replay,
            ):
                yield chunk

        async for chunk in live_records(
            bus=bus,
            queue=queue,
            session_id=session_id,
            should_emit=should_emit,
            keepalive_interval=KEEPALIVE_INTERVAL,
            replay_upper_seq=replay_upper_seq,
        ):
            yield chunk
    except asyncio.CancelledError:
        pass
    finally:
        bus.unsubscribe(queue)
        if not bus._subscribers and not run_registry.get_active_run(session_id):
            await bus_registry.remove_if_idle(
                session_id,
                is_active=lambda: run_registry.get_active_run(session_id) is not None,
            )


@router.get("/chat/events/{session_id}")
async def chat_events(
    session_id: str,
    request: Request,
    stream: bool = False,
    after_seq: Annotated[int | None, Query(ge=0)] = None,
    buses: BusRegistry = Depends(get_bus_registry),
    run_registry: RunRegistry = Depends(require_run_registry),
):
    runtime = getattr(request.app.state, "runtime", None)
    session_service = getattr(runtime, "session_service", None)
    event_store = session_service.store if session_service else None
    effective_after_seq = _effective_after_seq(after_seq, request.headers.get("last-event-id"))
    return SSEStreamingResponse(
        _event_stream(
            session_id,
            buses,
            run_registry,
            stream=stream,
            after_seq=effective_after_seq,
            event_store=event_store,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# Event types that drive the client's in-memory workflows domain (the FleetView
# cards). That domain is built only from live SSE events, so after a reload it is
# empty and the cards collapse to bare tool rows. These events ARE persisted in
# session_events, so we replay the relevant ones on history load to rehydrate it.
_WORKFLOW_DOMAIN_EVENT_TYPES = frozenset(
    {"workflow_started", "workflow_finished", "task_started", "task_progress", "task_finished", "token_usage"}
)
# task_* / token_usage events also fire for non-workflow runs; only those tagged
# with a workflow_id belong to a workflow card.
_WORKFLOW_TAGGED_EVENT_TYPES = frozenset({"task_started", "task_progress", "task_finished", "token_usage"})


def reconstruct_workflow_events(
    records: list[StreamRecord], session_id: str, *, max_seq: int | None = None
) -> list[dict]:
    """Filter a session's persisted event ledger down to the events that drive
    the client's workflows domain, returned in the same payload shape the live
    SSE stream delivers. task_*/token_usage events are kept only when tagged
    with a workflow_id (they also fire for non-workflow runs).

    `max_seq` bounds rehydration to the transcript checkpoint: events beyond it
    belong to an in-flight workflow and are delivered by the live SSE tail, so
    including them here would double-apply (and double-count token_usage, which
    accumulates) once the stream re-delivers them."""
    events: list[dict] = []
    for record in records:
        if max_seq is not None and record.seq > max_seq:
            continue
        event_type = record.event.type.value
        if event_type not in _WORKFLOW_DOMAIN_EVENT_TYPES:
            continue
        payload = json.loads(record.event.to_sse()["data"])
        if event_type in _WORKFLOW_TAGGED_EVENT_TYPES and not payload.get("workflow_id"):
            continue
        payload["seq"] = record.seq
        payload["session_id"] = payload.get("session_id") or session_id
        events.append(payload)
    return events


@router.get("/chat/{session_id}/workflows")
async def get_session_workflow_events(session_id: str, request: Request):
    """Persisted workflow-domain events for a session, so the client can rehydrate
    its FleetView cards after a reload (the live workflows store is in-memory and
    is otherwise lost when the transcript is rebuilt from history)."""
    runtime = getattr(request.app.state, "runtime", None)
    session_service = getattr(runtime, "session_service", None)
    store = session_service.store if session_service else None
    if store is None:
        return {"events": []}
    records = await store.list_session_events(session_id, after_seq=0, limit=50000)
    # Bound to the transcript checkpoint so rehydration never overlaps the live
    # SSE tail (which re-delivers events beyond the checkpoint on reconnect).
    checkpoint_seq = await store.get_latest_session_checkpoint_seq(session_id)
    return {"events": reconstruct_workflow_events(records, session_id, max_seq=checkpoint_seq)}


@router.get("/chat/runs/status", response_model=ChatRunsStatusResponse)
async def get_chat_runs_status(run_registry: RunRegistry = Depends(require_run_registry)):
    return run_registry.get_status()


@router.post("/chat/message")
async def chat_message(
    request: ChatRequest,
    runtime: Runtime = Depends(get_runtime),
    buses: BusRegistry = Depends(get_bus_registry),
):
    session_id = request.session_id
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id required")

    images = [img.model_dump() for img in request.images] if request.images else None
    context = [ctx.model_dump(exclude_none=True) for ctx in request.context] if request.context else None

    chat_model = await runtime.resolve_session_chat_model(session_id)
    try:
        return await submit_chat_message(
            runtime.run_registry,
            lambda: runtime.build_chat_deps(chat_model=chat_model),
            buses,
            message=request.message,
            skip_approvals=request.skip_approvals,
            session_id=session_id,
            images=images,
            context=context,
            client_id=request.client_id,
            session_service=getattr(runtime, "session_service", None),
        )
    except ChatIdempotencyConflict as e:
        raise HTTPException(
            status_code=409,
            detail={"code": e.code, "message": e.message, "client_id": e.client_id},
        ) from e
    except ChatSessionNotFound as e:
        raise HTTPException(
            status_code=404,
            detail={"code": e.code, "message": e.message, "session_id": e.session_id},
        ) from e
    except Exception as e:
        debug_id = f"err_{int(time.time() * 1000)}"
        raise HTTPException(
            status_code=500,
            detail={"code": "internal_error", "message": "Chat request failed.", "debug_id": debug_id},
        ) from e


@router.delete("/chat/inject/{client_id}")
async def cancel_inject(
    client_id: str,
    session_id: str,
    run_registry: RunRegistry = Depends(require_run_registry),
    runtime: Runtime = Depends(get_runtime),
):
    active_run = run_registry.get_active_run(session_id)
    if active_run is None:
        raise HTTPException(status_code=404, detail="No active run")

    if active_run.cancel_injection(client_id):
        session_service = getattr(runtime, "session_service", None)
        mark_cancelled = getattr(session_service, "mark_chat_queued_message_cancelled", None)
        if mark_cancelled:
            await mark_cancelled(client_id)
        return {"status": "cancelled", "client_id": client_id}

    raise HTTPException(status_code=409, detail="Already ingested")


@router.post("/tools/result")
async def submit_tool_result(
    request: ToolResultRequest,
    run_registry: RunRegistry = Depends(require_run_registry),
    runtime: Runtime = Depends(get_runtime),
):
    session_service = getattr(runtime, "session_service", None)
    store = getattr(session_service, "store", None)

    async def resolve_durable_approval_if_pending() -> bool:
        if store is None:
            return False
        row = await store.get_tool_approval(run_id=request.run_id, tool_call_id=request.tool_id)
        if row is None:
            return False
        if row["status"] != "pending":
            raise HTTPException(status_code=409, detail="Approval already resolved")
        resolved = await store.resolve_tool_approval(
            run_id=request.run_id,
            tool_call_id=request.tool_id,
            status="approved" if request.approved else "rejected",
            result_feedback=request.result.strip() or None,
        )
        if not resolved:
            raise HTTPException(status_code=409, detail="Approval already resolved")
        resume = getattr(runtime, "resume_suspended_chat_run", None)
        if resume:
            try:
                await resume(request.run_id, row["session_id"])
            except Exception:
                _logger.exception("Failed to resume run %s after durable tool resolution", request.run_id)
        return True

    run = run_registry.get_run(request.run_id)

    if not run:
        if await resolve_durable_approval_if_pending():
            return {"status": "ok"}
        raise HTTPException(status_code=404, detail="Run not found")

    input_future = run.pending_inputs.get(request.tool_id)
    if input_future is not None:
        if input_future.done():
            raise HTTPException(status_code=409, detail="Input already resolved")
        input_future.set_result(
            {
                "type": "tool_response",
                "tool_id": request.tool_id,
                "result": request.result,
                "approved": request.approved,
            }
        )
        return {"status": "ok"}

    future = run.pending_approvals.get(request.tool_id)
    if future is None:
        if await resolve_durable_approval_if_pending():
            return {"status": "ok"}
        raise HTTPException(status_code=404, detail="No pending approval for this tool")
    if future.done():
        raise HTTPException(status_code=409, detail="Approval already resolved")

    durable_row_exists = False
    if store is not None:
        try:
            row = await store.get_tool_approval(run_id=request.run_id, tool_call_id=request.tool_id)
            if row is not None:
                durable_row_exists = True
                if row["status"] != "pending":
                    raise HTTPException(status_code=409, detail="Approval already resolved")
        except HTTPException:
            raise
        except Exception:
            pass

    if future.done():
        raise HTTPException(status_code=409, detail="Approval already resolved")

    future.set_result(
        {
            "type": "tool_response",
            "tool_id": request.tool_id,
            "result": request.result,
            "approved": request.approved,
        }
    )

    if store is not None and durable_row_exists:
        try:
            await store.resolve_tool_approval(
                run_id=request.run_id,
                tool_call_id=request.tool_id,
                status="approved" if request.approved else "rejected",
                result_feedback=request.result.strip() or None,
            )
        except Exception:
            pass

    return {"status": "ok"}


def _connection_descriptor_from_payload(payload: dict) -> IntegrationConnectionDescriptor:
    return IntegrationConnectionDescriptor(
        integration_id=str(payload["integration_id"]),
        connection_id=str(payload["connection_id"]),
        label=str(payload["label"]),
        capability=str(payload["capability"]),
        action=str(payload["action"]),  # type: ignore[arg-type]
        settings_tab=str(payload.get("settings_tab") or "integrations"),
        state=str(payload.get("reason") or "not_configured"),  # type: ignore[arg-type]
        detail=str(payload.get("detail") or "") or None,
        required_scopes=tuple(str(value) for value in payload.get("required_scopes") or []),
        tool_names=tuple(str(value) for value in payload.get("tool_names") or []),
    )


@router.post("/connections/result")
async def submit_connection_result(
    request: ConnectionResultRequest,
    run_registry: RunRegistry = Depends(require_run_registry),
    runtime: Runtime = Depends(get_runtime),
):
    session_service = getattr(runtime, "session_service", None)
    store = getattr(session_service, "store", None)
    run = run_registry.get_run(request.run_id)
    descriptor = run.pending_connection_descriptors.get(request.tool_id) if run else None
    durable_row = None
    if store is not None:
        durable_row = await store.get_run_suspension(run_id=request.run_id, suspension_id=request.tool_id)
        if durable_row is not None and durable_row.get("kind") != "integration_connection":
            durable_row = None
        if descriptor is None and durable_row is not None:
            descriptor = _connection_descriptor_from_payload(durable_row.get("payload") or {})

    if descriptor is None:
        raise HTTPException(status_code=404, detail="No pending connection for this tool")

    if request.approved:
        await runtime.reload_config()
        try:
            await runtime.connection_service.verify_connection(descriptor.integration_id)
        except IntegrationConnectionError as exc:
            raise HTTPException(status_code=409, detail=exc.detail) from exc

    future = run.pending_connections.get(request.tool_id) if run else None
    if future is not None and future.done():
        raise HTTPException(status_code=409, detail="Connection request already resolved")

    if store is not None and durable_row is not None:
        if durable_row["status"] != "pending":
            raise HTTPException(status_code=409, detail="Connection request already resolved")
        resolved = await store.resolve_integration_connection(
            run_id=request.run_id,
            tool_call_id=request.tool_id,
            status="approved" if request.approved else "rejected",
            result_feedback=request.result.strip() or None,
        )
        if not resolved:
            raise HTTPException(status_code=409, detail="Connection request already resolved")

    if future is not None:
        future.set_result(
            {
                "type": "connection_response",
                "tool_id": request.tool_id,
                "result": request.result,
                "approved": request.approved,
            }
        )

    if future is None and durable_row is not None:
        resume = getattr(runtime, "resume_suspended_chat_run", None)
        if resume:
            await resume(request.run_id, durable_row["session_id"])
    elif future is None:
        raise HTTPException(status_code=404, detail="No pending connection for this tool")

    return {"status": "ok"}


@router.post("/cancel", status_code=202)
async def cancel_run(request: CancelRequest, run_registry: RunRegistry = Depends(require_run_registry)):
    run_id = request.run_id
    if not run_id and request.session_id:
        active = run_registry.get_active_run(request.session_id)
        run_id = active.run_id if active else None
    if not run_id:
        raise HTTPException(status_code=404, detail="Run not found")
    result = run_registry.cancel_run(run_id)
    if not result["found"]:
        raise HTTPException(status_code=404, detail="Run not found")
    return {"status": "cancelling", "run_id": run_id, **result}


def _resolve_run_id(request: CancelRequest, run_registry: RunRegistry) -> str:
    run_id = request.run_id
    if not run_id and request.session_id:
        active = run_registry.get_active_run(request.session_id)
        run_id = active.run_id if active else None
    if not run_id:
        raise HTTPException(status_code=404, detail="Run not found")
    return run_id


@router.post("/chat/subagents/{tool_call_id}/cancel", status_code=202)
async def cancel_subagent(
    tool_call_id: str,
    run_id: str,
    run_registry: RunRegistry = Depends(require_run_registry),
):
    result = run_registry.cancel_subagent(run_id, tool_call_id)
    if not result["found"]:
        raise HTTPException(status_code=404, detail="Subagent not found or already done")
    return {"status": "cancelling", **result}


@router.post("/chat/background")
async def background_run(request: BackgroundRequest, run_registry: RunRegistry = Depends(require_run_registry)):
    run = run_registry.get_run(request.run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    if run.status != RunStatus.RUNNING:
        raise HTTPException(status_code=400, detail="Run is not active")
    run.backgrounded = True
    return {"status": "backgrounding"}


async def _list_child_agents(
    session_id: str,
    runtime: Runtime,
    run_registry: RunRegistry,
) -> dict:
    session_service = getattr(runtime, "session_service", None)
    store = getattr(session_service, "store", None)
    if store is not None:
        return {"tasks": await store.list_background_agent_runs(session_id)}

    registry = run_registry.get_background_registry(session_id)
    pending = registry.list_pending()
    now = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    return {
        "tasks": [
            {
                "task_id": tid,
                "child_run_id": tid,
                "session_id": session_id,
                "parent_run_id": None,
                "parent_tool_call_id": None,
                "agent_type": "background_research",
                "wait": False,
                "status": "running",
                "command": cmd,
                "detail": None,
                "result_ref": None,
                "created_at": now,
                "started_at": None,
                "updated_at": now,
                "ended_at": None,
                "cancel_requested_at": None,
                "notified_at": None,
            }
            for tid, cmd in pending
        ]
    }


async def _child_agent_result_snapshot(
    child_run_id: str,
    session_id: str,
    runtime: Runtime,
    run_registry: RunRegistry,
) -> dict | None:
    session_service = getattr(runtime, "session_service", None)
    store = getattr(session_service, "store", None)
    if store is not None:
        rows = await store.list_background_agent_runs(session_id)
        row = next((item for item in rows if item["task_id"] == child_run_id), None)
        if row is None:
            return None
        result = await store.get_background_agent_result(session_id, child_run_id)
        status = row["status"]
        return {
            "task_id": row["task_id"],
            "child_run_id": row.get("child_run_id") or row["task_id"],
            "session_id": row["session_id"],
            "status": status,
            "terminal": status in CHILD_AGENT_TERMINAL_STATUSES,
            "result": result,
            "result_ref": row["result_ref"],
        }

    registry = run_registry.get_background_registry(session_id)
    result = await registry.read_background_result(child_run_id)
    if result is not None:
        return {
            "task_id": child_run_id,
            "child_run_id": child_run_id,
            "session_id": session_id,
            "status": "completed",
            "terminal": True,
            "result": result,
            "result_ref": None,
        }

    pending = dict(registry.list_pending())
    if child_run_id not in pending:
        return None
    return {
        "task_id": child_run_id,
        "child_run_id": child_run_id,
        "session_id": session_id,
        "status": "running",
        "terminal": False,
        "result": None,
        "result_ref": None,
    }


async def _get_child_agent_result(
    child_run_id: str,
    session_id: str,
    runtime: Runtime,
    run_registry: RunRegistry,
    *,
    wait: bool,
    timeout_seconds: float,
) -> dict:
    deadline = time.monotonic() + timeout_seconds
    while True:
        snapshot = await _child_agent_result_snapshot(child_run_id, session_id, runtime, run_registry)
        if snapshot is None:
            raise HTTPException(status_code=404, detail="Child agent not found")
        if not wait or snapshot["terminal"] or snapshot["result"] is not None:
            return snapshot
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return snapshot
        await asyncio.sleep(min(0.25, remaining))


async def _cancel_child_agent(
    child_run_id: str,
    session_id: str,
    runtime: Runtime,
    run_registry: RunRegistry,
) -> dict:
    requested = False
    session_service = getattr(runtime, "session_service", None)
    store = getattr(session_service, "store", None)
    if store is not None:
        requested = await store.request_background_agent_cancel(session_id, child_run_id)

    registry = run_registry.get_background_registry(session_id)
    command = registry.cancel(child_run_id)
    if command is None and not requested:
        raise HTTPException(status_code=404, detail="Task not found or already done")

    # Cascade: cancelling an agent also stops everything it spawned, otherwise
    # detached grandchildren (in their own session registries) keep running.
    for desc_session, desc_task in run_registry.cancel_subtree(registry.child_session(child_run_id)):
        if store is not None:
            await store.request_background_agent_cancel(desc_session, desc_task)

    return {
        "status": "cancelled" if command is not None else "cancel_requested",
        "task_id": child_run_id,
        "child_run_id": child_run_id,
        "command": command,
    }


@router.get("/chat/background-tasks", response_model=BackgroundAgentRunsResponse)
async def list_background_tasks(
    session_id: str,
    runtime: Runtime = Depends(get_runtime),
    run_registry: RunRegistry = Depends(require_run_registry),
):
    return await _list_child_agents(session_id, runtime, run_registry)


@router.get("/chat/child-agents", response_model=BackgroundAgentRunsResponse)
async def list_child_agents(
    session_id: str,
    runtime: Runtime = Depends(get_runtime),
    run_registry: RunRegistry = Depends(require_run_registry),
):
    return await _list_child_agents(session_id, runtime, run_registry)


@router.get("/chat/child-agents/{child_run_id}/result", response_model=ChildAgentResultResponse)
async def get_child_agent_result(
    child_run_id: str,
    session_id: str,
    wait: bool = False,
    timeout_seconds: Annotated[float, Query(ge=0, le=30)] = 0.0,
    runtime: Runtime = Depends(get_runtime),
    run_registry: RunRegistry = Depends(require_run_registry),
):
    return await _get_child_agent_result(
        child_run_id,
        session_id,
        runtime,
        run_registry,
        wait=wait,
        timeout_seconds=timeout_seconds,
    )


@router.post("/chat/background-tasks/{task_id}/cancel")
async def cancel_background_task(
    task_id: str,
    session_id: str,
    runtime: Runtime = Depends(get_runtime),
    run_registry: RunRegistry = Depends(require_run_registry),
):
    return await _cancel_child_agent(task_id, session_id, runtime, run_registry)


@router.post("/chat/child-agents/{child_run_id}/cancel")
async def cancel_child_agent(
    child_run_id: str,
    session_id: str,
    runtime: Runtime = Depends(get_runtime),
    run_registry: RunRegistry = Depends(require_run_registry),
):
    return await _cancel_child_agent(child_run_id, session_id, runtime, run_registry)


@router.post("/chat/child-agents/{child_run_id}/inject", status_code=202)
async def inject_child_agent(
    child_run_id: str,
    session_id: str,
    body: InjectChildAgentRequest,
    run_registry: RunRegistry = Depends(require_run_registry),
):
    """Steer a running background agent — deliver a message into its loop at
    its next step. `session_id` is the PARENT session that owns the agent."""
    registry = run_registry.get_background_registry(session_id)
    delivered = registry.queue_steering(child_run_id, body.message)
    if not delivered:
        raise HTTPException(status_code=404, detail="Agent is not running")
    return {"status": "delivered", "child_run_id": child_run_id}
