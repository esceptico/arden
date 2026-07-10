from collections.abc import Callable

from fastapi import APIRouter, Depends, HTTPException, Query

from ntrp.agent import Role
from ntrp.areas.triage import TriageDecision, triage_chat
from ntrp.constants import HISTORY_MESSAGE_LIMIT
from ntrp.core.compactor import is_handoff_message
from ntrp.core.content import blocks_to_text
from ntrp.core.llm_client import llm_client
from ntrp.core.model_context_budget import HISTORY_TOOL_RESULT_PREVIEW_CHARS, compact_tool_result_text
from ntrp.core.tool_result_data import persistable_tool_result_data
from ntrp.events.sse import GoalClearedEvent, GoalUpdatedEvent
from ntrp.observability import observed_trace
from ntrp.server.bus import BusRegistry, prime_bus_cursor_from_store
from ntrp.server.deps import get_bus_registry, require_run_registry, require_session_service
from ntrp.server.runtime import Runtime, get_runtime
from ntrp.server.schemas import (
    BranchRequest,
    ClearSessionRequest,
    CreateSessionRequest,
    GoalProposalResponse,
    MoveSessionAreaRequest,
    RenameSessionRequest,
    RevertRequest,
    SessionGoalResponse,
    SessionResponse,
    SetSessionAutoRequest,
    SetSessionGoalRequest,
    SetTodoOverrideRequest,
    UpdateSessionGoalRequest,
    UpdateSessionModelRequest,
)
from ntrp.server.state import RunRegistry
from ntrp.services.session import SessionService

router = APIRouter(tags=["session"])

GOAL_PROPOSAL_SYSTEM_PROMPT = (
    "Write the text the user would put after `/goal` for this conversation. "
    "Reduce their manual typing: infer the current task they want done from the latest user messages. "
    "Keep the user's wording and scope when possible. "
    "Use enough detail for the actual task; simple tasks can be short, complex tasks may need more context. "
    "Include the success definition when the conversation makes it clear. "
    "Do not turn it into a step-by-step checklist or area plan. "
    "Return only the goal text: no labels, bullets, quotes, markdown, or explanation."
)

ACTIVE_RUN_STATUSES = {"pending", "running", "backgrounded"}
SURFACED_TERMINAL_RUN_STATUSES = {"interrupted", "error", "failed"}
SNAPSHOT_RUN_STATUSES = ACTIVE_RUN_STATUSES | SURFACED_TERMINAL_RUN_STATUSES
OPENING_CHECKPOINT_RUN_STATUSES = ACTIVE_RUN_STATUSES | SURFACED_TERMINAL_RUN_STATUSES | {"cancelled", "completed"}


def _runtime_run_from_live(active_run) -> dict | None:
    if active_run is None:
        return None
    status = "backgrounded" if active_run.backgrounded else active_run.status.value
    return {
        "run_id": active_run.run_id,
        "session_id": active_run.session_id,
        "status": status,
        "started_at": active_run.created_at.isoformat(),
        "updated_at": active_run.updated_at.isoformat(),
        "ended_at": None,
        "last_seq": None,
        "stop_reason": active_run.stop_reason,
        "error_code": None,
        "error_message": None,
        "client_id": None,
    }


def _approval_snapshot(row: dict) -> dict:
    return {
        "tool_id": row["tool_call_id"],
        "tool_name": row["tool_name"],
        "preview": row.get("preview"),
        "diff": row.get("diff"),
        "status": "pending",
        "requested_at": row.get("requested_at"),
        "run_id": row.get("run_id"),
    }


def _queued_message_snapshot(row: dict) -> dict:
    message = row.get("message") or {}
    raw_content = message.get("content", "") or ""
    text = blocks_to_text(raw_content) if not isinstance(raw_content, str) else raw_content
    images = []
    if isinstance(raw_content, list):
        images = [
            {"media_type": b["media_type"], "data": b["data"]}
            for b in raw_content
            if isinstance(b, dict) and b.get("type") == "image" and b.get("media_type") and b.get("data")
        ]
    status = "pending" if row.get("status") == "queued" else "failed"
    return {
        "client_id": row["client_id"],
        "text": text,
        "images": images,
        "status": status,
        "server_status": row.get("status"),
        "enqueued_at": row.get("enqueued_at"),
        "run_id": row.get("run_id"),
    }


async def _session_runtime_snapshot(
    svc: SessionService,
    runtime: Runtime,
    buses: BusRegistry,
    session_id: str,
) -> dict:
    latest_event_seq = await svc.store.get_latest_session_event_seq(session_id)
    durable_checkpoint_seq = await svc.store.get_latest_session_checkpoint_seq(session_id)
    bus = buses.get(session_id)
    if bus is not None:
        latest_event_seq = max(latest_event_seq, bus.next_seq - 1)
        durable_checkpoint_seq = max(durable_checkpoint_seq, bus.checkpoint_seq)

    run_registry = getattr(runtime, "run_registry", None)
    live_run = run_registry.get_active_run(session_id) if run_registry else None
    durable_run = await svc.store.get_latest_chat_run_for_session(session_id)
    run = _runtime_run_from_live(live_run) or durable_run
    run_status = run.get("status") if run else None
    surfaced_run = run if run_status in SNAPSHOT_RUN_STATUSES else None

    run_checkpoint_seq = 0
    if run and run_status in OPENING_CHECKPOINT_RUN_STATUSES:
        run_checkpoint_seq = int(run.get("last_seq") or 0)
    checkpoint_seq = max(durable_checkpoint_seq, run_checkpoint_seq)

    approval_rows = (
        await svc.store.list_pending_tool_approvals(session_id, run_id=surfaced_run["run_id"]) if surfaced_run else []
    )
    queued_rows = []
    if surfaced_run and run_status in ACTIVE_RUN_STATUSES:
        queued_rows = [
            row
            for row in await svc.store.list_chat_queued_messages(session_id, status="queued")
            if row.get("run_id") == surfaced_run["run_id"]
        ]

    pending_approvals = [_approval_snapshot(row) for row in approval_rows]
    queued_messages = [_queued_message_snapshot(row) for row in queued_rows]

    active_run = None
    if surfaced_run is not None:
        active_run = {
            "run_id": surfaced_run["run_id"],
            "status": surfaced_run["status"],
            "started_at": surfaced_run.get("started_at"),
            "updated_at": surfaced_run.get("updated_at"),
            "ended_at": surfaced_run.get("ended_at"),
            "stop_reason": surfaced_run.get("stop_reason"),
            "checkpoint_seq": checkpoint_seq,
            "latest_event_seq": latest_event_seq,
            "error_code": surfaced_run.get("error_code"),
            "error_message": surfaced_run.get("error_message"),
            "pending_approvals": pending_approvals,
            "queued_messages": queued_messages,
        }

    return {
        "session_id": session_id,
        "latest_event_seq": latest_event_seq,
        "checkpoint_seq": checkpoint_seq,
        "active_run": active_run,
        "pending_approvals": pending_approvals,
        "queued_messages": queued_messages,
    }


def _session_list_runtime_fields(snapshot: dict) -> dict:
    active_run = snapshot.get("active_run")
    return {
        "active_run_id": active_run.get("run_id") if active_run else None,
        "run_status": active_run.get("status") if active_run else None,
        "checkpoint_seq": snapshot["checkpoint_seq"],
        "latest_event_seq": snapshot["latest_event_seq"],
        "is_active": bool(active_run and active_run.get("status") in ACTIVE_RUN_STATUSES),
        "pending_approvals_count": len(snapshot["pending_approvals"]),
        "queued_messages_count": len(snapshot["queued_messages"]),
        "run_error_code": active_run.get("error_code") if active_run else None,
        "run_stop_reason": active_run.get("stop_reason") if active_run else None,
    }


def _goal_proposal_context(messages: list[dict], limit: int = 12) -> str:
    rows: list[str] = []
    for msg in messages[-limit:]:
        raw_role = msg.get("role") or "unknown"
        if raw_role == Role.SYSTEM or raw_role == "system":
            continue
        role = str(raw_role)
        content = blocks_to_text(msg.get("content", "") or "").strip()
        if not content:
            continue
        rows.append(f"{role}: {content}")
    return "\n\n".join(rows)


def _clean_goal_proposal(text: str) -> str:
    objective = text.strip().strip("`").strip()
    for prefix in ("Goal:", "Objective:", "- ", "* "):
        if objective.startswith(prefix):
            objective = objective[len(prefix) :].strip()
    if (objective.startswith('"') and objective.endswith('"')) or (
        objective.startswith("'") and objective.endswith("'")
    ):
        objective = objective[1:-1].strip()
    return objective


async def _emit_goal_event(
    buses: BusRegistry,
    session_id: str,
    goal: dict | None,
    *,
    event_store: object | None = None,
) -> None:
    await prime_bus_cursor_from_store(buses, session_id, event_store)
    bus = buses.get_or_create(session_id)
    if goal is None:
        await bus.emit(GoalClearedEvent(session_id=session_id))
    else:
        await bus.emit(GoalUpdatedEvent(session_id=session_id, goal=goal))


def _history_tool_calls(msg: dict, tool_meta_for: Callable[[str], tuple[str, str | None]]) -> list[dict]:
    tool_calls = []
    raw_provider_calls = msg.get("provider_tool_calls") or []
    if isinstance(raw_provider_calls, list):
        for tc in raw_provider_calls:
            if not isinstance(tc, dict):
                continue
            call_id = tc.get("id")
            name = tc.get("name")
            if not isinstance(call_id, str) or not isinstance(name, str):
                continue
            arguments = tc.get("arguments", "{}")
            result = tc.get("result", "")
            tool_calls.append(
                {
                    "id": call_id,
                    "name": name,
                    "arguments": arguments if isinstance(arguments, str) else "{}",
                    "kind": "tool",
                    "display_name": "Search Tools" if name == "tool_search" else name,
                    "result": result if isinstance(result, str) else "",
                }
            )
    raw_tool_calls = msg.get("tool_calls") or []
    if not isinstance(raw_tool_calls, list):
        raw_tool_calls = []
    for tc in raw_tool_calls:
        if not isinstance(tc, dict):
            continue
        function = tc.get("function")
        if not isinstance(function, dict):
            continue
        call_id = tc.get("id")
        name = function.get("name")
        if not isinstance(call_id, str) or not isinstance(name, str):
            continue
        arguments = function.get("arguments", "{}")
        kind, display_name = tool_meta_for(name)
        item = {
            "id": call_id,
            "name": name,
            "arguments": arguments if isinstance(arguments, str) else "{}",
            "kind": kind,
        }
        if display_name:
            item["display_name"] = display_name
        tool_calls.append(item)
    return tool_calls


async def _require_area(svc: SessionService, area_id: str | None) -> dict | None:
    if area_id is None:
        return None
    area = await svc.get_area(area_id)
    if not area:
        raise HTTPException(status_code=404, detail="Area not found")
    return area


async def _triage_candidates(svc: SessionService) -> list[dict]:
    """The homes a chat can be filed into: every container (areas and
    areas are one concept, keyed by area_id)."""
    return [{"key": p["area_id"], "title": p.get("name", "")} for p in await svc.list_areas()]


@router.get("/session/history")
async def get_session_history(
    svc: SessionService = Depends(require_session_service),
    runtime: Runtime = Depends(get_runtime),
    buses: BusRegistry = Depends(get_bus_registry),
    session_id: str | None = None,
    limit: int = Query(default=HISTORY_MESSAGE_LIMIT, ge=1, le=250),
    before: str | None = None,
    after: str | None = None,
    around: str | None = None,
    around_seq: int | None = Query(default=None, ge=0),
):
    data = await svc.load(session_id)
    if not data:
        return {
            "messages": [],
            "active_run_id": None,
            "runtime": {
                "session_id": session_id,
                "latest_event_seq": 0,
                "checkpoint_seq": 0,
                "active_run": None,
                "pending_approvals": [],
                "queued_messages": [],
            },
            "page": {"has_more_before": False, "has_more_after": False},
        }

    # Runtime snapshot: durable run state + event cursor. The desktop renders
    # this first, then opens the SSE stream with after_seq=checkpoint_seq so
    # replay is a delta rather than the whole active-session reconstruction.
    sid = data.state.session_id
    runtime_snapshot = await _session_runtime_snapshot(svc, runtime, buses, sid)
    active_run = runtime_snapshot["active_run"]
    active_run_id = active_run["run_id"] if active_run and active_run["status"] in ACTIVE_RUN_STATUSES else None

    # Tools carry a `kind` ("tool" | "agent") that the desktop renderer uses
    # to pick a row surface. We thread it into the history payload so a
    # reloaded session keeps the same UI as the live stream.
    def _tool_meta_for(name: str) -> tuple[str, str | None]:
        executor = getattr(runtime, "executor", None)
        if not executor:
            return "tool", None
        tool = executor.registry.get(name)
        if not tool:
            return "tool", None
        return getattr(tool, "kind", "tool"), getattr(tool, "display_name", None)

    page = await svc.list_messages(
        sid,
        limit=limit,
        before=before,
        after=after,
        around=around,
        around_seq=around_seq,
    )

    history = []
    for row in page["messages"]:
        msg = row["message"]
        role = msg["role"]
        if role == Role.SYSTEM:
            continue
        if is_handoff_message(msg):
            continue

        raw_content = msg.get("content", "") or ""
        if role == Role.USER and isinstance(raw_content, list):
            text_parts = [b["text"] for b in raw_content if isinstance(b, dict) and b.get("type") == "text"]
            images = [
                {"media_type": b["media_type"], "data": b["data"]}
                for b in raw_content
                if isinstance(b, dict) and b.get("type") == "image"
            ]
            context = [
                {k: v for k, v in b.items() if k != "type" and v is not None}
                for b in raw_content
                if isinstance(b, dict) and b.get("type") == "context"
            ]
            entry: dict = {"role": role, "content": "\n\n".join(text_parts)}
            if images:
                entry["images"] = images
            if context:
                entry["context"] = context
        else:
            content = blocks_to_text(raw_content)
            if role == Role.TOOL and len(content) > HISTORY_TOOL_RESULT_PREVIEW_CHARS:
                content = compact_tool_result_text(content, surface="history display")
            entry = {"role": role, "content": content}

        if role == Role.ASSISTANT:
            tool_calls = _history_tool_calls(msg, _tool_meta_for)
            if tool_calls:
                entry["tool_calls"] = tool_calls
        if role == Role.ASSISTANT and msg.get("reasoning_content"):
            entry["reasoning_content"] = msg["reasoning_content"]

        if role == Role.TOOL and "tool_call_id" in msg:
            entry["tool_call_id"] = msg["tool_call_id"]
            if result_data := persistable_tool_result_data(msg.get("data")):
                entry["data"] = result_data

        # Stable client-side id (the same one we streamed in SSE for assistant
        # turns). Lets the desktop client key React components and reference
        # the message in branch / edit calls without positional games.
        if msg.get("client_id"):
            entry["id"] = msg["client_id"]
        elif row.get("message_id"):
            entry["id"] = row["message_id"]

        entry["message_id"] = row["message_id"]
        entry["seq"] = row["seq"]

        if created_at := msg.get("created_at"):
            entry["created_at"] = created_at

        # Loop-fired user messages are visible to the model (kept in history
        # so the agent can act on them) but hidden from the transcript UI.
        if msg.get("is_meta"):
            entry["is_meta"] = True

        history.append(entry)

    return {
        "messages": history,
        "active_run_id": active_run_id,
        "runtime": runtime_snapshot,
        "page": {
            "has_more_before": page["has_more_before"],
            "has_more_after": page["has_more_after"],
            "before": page["before"],
            "after": page["after"],
        },
        # Snapshot of the session's budget-relevant counters so the desktop
        # can populate the BudgetDial immediately on session open instead of
        # waiting for the next run to finish and emit RunFinishedEvent.
        # `last_message_count` reflects durable transcript pressure. Falls
        # back to the on-disk count for sessions saved before this field
        # existed.
        "usage": {
            "last_input_tokens": data.last_input_tokens or 0,
            "message_count": data.last_message_count if data.last_message_count is not None else len(data.messages),
        },
    }


@router.get("/session/turns")
async def get_session_turns(
    svc: SessionService = Depends(require_session_service),
    session_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
):
    data = await svc.load(session_id)
    if not data:
        return {"turns": []}
    return {"turns": await svc.list_turns(data.state.session_id, limit=limit)}


@router.get("/session")
async def get_session(
    runtime: Runtime = Depends(get_runtime),
    svc: SessionService = Depends(require_session_service),
    session_id: str | None = None,
) -> SessionResponse:
    data = await svc.load(session_id)
    if data:
        session_state = data.state
    else:
        session_state = svc.create(chat_model=runtime.config.chat_model)

    return SessionResponse(
        session_id=session_state.session_id,
        integrations=runtime.get_available_integrations(),
        integration_errors=runtime.get_integration_errors(),
        name=session_state.name,
        area_id=session_state.area_id,
        chat_model=session_state.chat_model,
    )


@router.get("/sessions/{session_id}/goal", response_model=SessionGoalResponse | None)
async def get_session_goal(
    session_id: str,
    svc: SessionService = Depends(require_session_service),
):
    return await svc.get_goal(session_id)


@router.post("/sessions/{session_id}/goal", response_model=SessionGoalResponse)
async def set_session_goal(
    session_id: str,
    req: SetSessionGoalRequest,
    svc: SessionService = Depends(require_session_service),
    buses: BusRegistry = Depends(get_bus_registry),
):
    if not await svc.load(session_id):
        raise HTTPException(status_code=404, detail="Session not found")
    objective = req.objective.strip()
    if not objective:
        raise HTTPException(status_code=422, detail="Goal objective cannot be blank")
    goal = await svc.set_goal(session_id, objective, token_budget=req.token_budget)
    if not goal:
        raise HTTPException(status_code=500, detail="Failed to set goal")
    await _emit_goal_event(buses, session_id, goal, event_store=svc.store)
    return goal


@router.post("/sessions/{session_id}/goal/propose", response_model=GoalProposalResponse)
@observed_trace("session.goal", tags="session")
async def propose_session_goal(
    session_id: str,
    svc: SessionService = Depends(require_session_service),
    runtime: Runtime = Depends(get_runtime),
):
    data = await svc.load(session_id)
    if not data:
        raise HTTPException(status_code=404, detail="Session not found")
    context = _goal_proposal_context(data.messages)
    if not context:
        raise HTTPException(status_code=422, detail="Not enough context to propose a goal")
    response = await llm_client.complete(
        runtime.config.chat_model,
        [
            {"role": Role.SYSTEM, "content": GOAL_PROPOSAL_SYSTEM_PROMPT},
            {"role": Role.USER, "content": f"Recent conversation:\n\n{context}"},
        ],
        temperature=0,
        max_tokens=120,
    )
    content = response.choices[0].message.content if response.choices else ""
    objective = _clean_goal_proposal(content or "")
    if not objective:
        raise HTTPException(status_code=502, detail="Goal proposal was empty")
    return GoalProposalResponse(objective=objective)


@router.patch("/sessions/{session_id}/goal", response_model=SessionGoalResponse)
async def update_session_goal(
    session_id: str,
    req: UpdateSessionGoalRequest,
    svc: SessionService = Depends(require_session_service),
    buses: BusRegistry = Depends(get_bus_registry),
):
    blocked_reason = req.blocked_reason.strip() if req.blocked_reason else None
    if req.status == "blocked" and not blocked_reason:
        raise HTTPException(status_code=422, detail="blocked_reason is required when blocking a goal")
    goal = await svc.update_goal(
        session_id,
        status=req.status,
        evidence=req.evidence.strip() if req.evidence else None,
        blocked_reason=blocked_reason,
    )
    if not goal:
        raise HTTPException(status_code=404, detail="Goal not found")
    await _emit_goal_event(buses, session_id, goal, event_store=svc.store)
    return goal


@router.delete("/sessions/{session_id}/goal")
async def clear_session_goal(
    session_id: str,
    svc: SessionService = Depends(require_session_service),
    buses: BusRegistry = Depends(get_bus_registry),
):
    cleared = await svc.clear_goal(session_id)
    if cleared:
        await _emit_goal_event(buses, session_id, None, event_store=svc.store)
    return {"status": "cleared", "session_id": session_id}


@router.get("/sessions/{session_id}/todo")
async def get_session_todo_override(
    session_id: str,
    svc: SessionService = Depends(require_session_service),
):
    return await svc.get_todo_override(session_id)


@router.post("/sessions/{session_id}/todo")
async def set_session_todo_override(
    session_id: str,
    req: SetTodoOverrideRequest,
    svc: SessionService = Depends(require_session_service),
):
    if not await svc.load(session_id):
        raise HTTPException(status_code=404, detail="Session not found")
    items = [{"content": item.content, "status": item.status} for item in req.items]
    result = await svc.set_todo_override(session_id, items, req.explanation)
    if result is None:
        raise HTTPException(status_code=500, detail="Failed to set todo override")
    return result


@router.delete("/sessions/{session_id}/todo")
async def clear_session_todo_override(
    session_id: str,
    svc: SessionService = Depends(require_session_service),
):
    cleared = await svc.clear_todo_override(session_id)
    return {"status": "cleared" if cleared else "noop", "session_id": session_id}


@router.post("/session/clear")
async def clear_session(svc: SessionService = Depends(require_session_service), req: ClearSessionRequest | None = None):
    target_id = req.session_id if req else None

    data = await svc.load(target_id)
    if not data:
        return {"status": "cleared", "session_id": None}

    data.state.last_activity = data.state.started_at
    await svc.save(data.state, [])

    return {
        "status": "cleared",
        "session_id": data.state.session_id,
    }


@router.post("/session/revert")
async def revert_session(svc: SessionService = Depends(require_session_service), req: RevertRequest | None = None):
    target_id = req.session_id if req else None
    turns = req.turns if req else 1
    message_id = req.message_id if req else None
    result = await svc.revert(target_id, turns=turns, message_id=message_id)
    if not result:
        raise HTTPException(status_code=400, detail="Nothing to revert")
    return result


# --- Multi-session ---


@router.post("/sessions/{session_id}/branch")
async def branch_session(
    session_id: str,
    req: BranchRequest | None = None,
    svc: SessionService = Depends(require_session_service),
):
    name = req.name if req else None
    up_to_id = req.up_to_message_id if req else None
    from_end = req.from_end_index if req else None
    state = await svc.branch(
        session_id,
        name=name,
        up_to_message_id=up_to_id,
        from_end_index=from_end,
    )
    if not state:
        raise HTTPException(status_code=404, detail="Session not found")
    return {
        "session_id": state.session_id,
        "name": state.name,
        "started_at": state.started_at.isoformat(),
        "last_activity": state.last_activity.isoformat(),
        "area_id": state.area_id,
    }


@router.post("/sessions")
async def create_session(
    runtime: Runtime = Depends(get_runtime),
    svc: SessionService = Depends(require_session_service),
    req: CreateSessionRequest | None = None,
):
    name = req.name if req else None
    area_id = req.area_id if req else None
    await _require_area(svc, area_id)
    state = svc.create(name=name, area_id=area_id, chat_model=runtime.config.chat_model)
    await svc.save(state, [])
    return {
        "session_id": state.session_id,
        "name": state.name,
        "started_at": state.started_at.isoformat(),
        "last_activity": state.last_activity.isoformat(),
        "message_count": 0,
        "area_id": state.area_id,
        "chat_model": state.chat_model,
    }


@router.get("/sessions")
async def list_sessions(
    svc: SessionService = Depends(require_session_service),
    runtime: Runtime = Depends(get_runtime),
    buses: BusRegistry = Depends(get_bus_registry),
    area_id: str | None = Query(default=None),
    inbox: bool = Query(default=False),
    include_agents: bool = Query(default=True),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    # Tests call route functions directly, so FastAPI's Query defaults are
    # not resolved before the function body runs.
    area_id = area_id if isinstance(area_id, str) else None
    inbox = inbox if isinstance(inbox, bool) else False
    include_agents = include_agents if isinstance(include_agents, bool) else True
    limit = limit if isinstance(limit, int) else 100
    offset = offset if isinstance(offset, int) else 0
    if inbox:
        sessions = await svc.list_sessions(limit=limit, area_id=None, include_agents=include_agents, offset=offset)
    elif area_id is not None:
        await _require_area(svc, area_id)
        sessions = await svc.list_sessions(
            limit=limit,
            area_id=area_id,
            include_agents=include_agents,
            offset=offset,
        )
    else:
        sessions = await svc.list_sessions(limit=limit, include_agents=include_agents, offset=offset)
    enriched = []
    for session in sessions:
        snapshot = await _session_runtime_snapshot(svc, runtime, buses, session["session_id"])
        enriched.append({**session, **_session_list_runtime_fields(snapshot)})
    return {"sessions": enriched}


@router.get("/sessions/{session_id}/state")
async def get_session_state_snapshot(
    session_id: str,
    svc: SessionService = Depends(require_session_service),
    runtime: Runtime = Depends(get_runtime),
    buses: BusRegistry = Depends(get_bus_registry),
):
    data = await svc.load(session_id)
    if not data:
        raise HTTPException(status_code=404, detail="Session not found")
    return await _session_runtime_snapshot(svc, runtime, buses, data.state.session_id)


@router.patch("/sessions/{session_id}")
async def rename_session(
    session_id: str, req: RenameSessionRequest, svc: SessionService = Depends(require_session_service)
):
    updated = await svc.rename(session_id, req.name)
    if not updated:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"session_id": session_id, "name": req.name}


@router.put("/sessions/{session_id}/model")
async def update_session_model(
    session_id: str, req: UpdateSessionModelRequest, svc: SessionService = Depends(require_session_service)
):
    updated = await svc.update_chat_model(session_id, req.chat_model)
    if not updated:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"session_id": session_id, "chat_model": req.chat_model}


@router.post("/sessions/{session_id}/area")
async def move_session_to_area(
    session_id: str,
    req: MoveSessionAreaRequest,
    svc: SessionService = Depends(require_session_service),
):
    if not await svc.load(session_id):
        raise HTTPException(status_code=404, detail="Session not found")
    await _require_area(svc, req.area_id)
    moved = await svc.move_session_to_area(session_id, req.area_id)
    if not moved:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"session_id": session_id, "area_id": req.area_id}


@router.post("/sessions/{session_id}/triage", response_model=TriageDecision)
@observed_trace("session.triage", tags="session")
async def triage_session(
    session_id: str,
    svc: SessionService = Depends(require_session_service),
    runtime: Runtime = Depends(get_runtime),
):
    """Classify a just-started chat against existing homes and return a filing
    proposal (move / create / none). Read-only — the client runs the chosen
    action. Degrades to `none` whenever the cheap model is unavailable or the
    chat has no substance yet."""
    data = await svc.load(session_id)
    if not data:
        raise HTTPException(status_code=404, detail="Session not found")
    cheap_llm = runtime.automation.get_cheap_llm()
    model = runtime.automation.cheap_model
    transcript = _goal_proposal_context(data.messages)
    if cheap_llm is None or not model or not transcript:
        return TriageDecision(decision="none")
    candidates = await _triage_candidates(svc)
    return await triage_chat(transcript=transcript, candidates=candidates, cheap_llm=cheap_llm, model=model)


@router.post("/sessions/{session_id}/auto")
async def set_session_auto(
    session_id: str,
    req: SetSessionAutoRequest,
    run_registry: RunRegistry = Depends(require_run_registry),
):
    """Apply an Auto-mode toggle to the live run.

    When `value=True`: future tool calls in the active run skip approval, and
    any approval Futures currently awaiting user input resolve as approved.
    When `value=False`: just flips the flag; pending approvals stay pending.
    """
    active = run_registry.get_accepting_run(session_id)
    resolved = 0
    if active is not None:
        resolved = active.set_skip_approvals(req.value)
    return {"status": "ok", "skip_approvals": req.value, "auto_resolved": resolved}


@router.delete("/sessions/{session_id}")
async def archive_session(session_id: str, svc: SessionService = Depends(require_session_service)):
    archived = await svc.archive(session_id)
    if not archived:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"status": "archived", "session_id": session_id}


@router.get("/sessions/archived")
async def list_archived_sessions(svc: SessionService = Depends(require_session_service)):
    sessions = await svc.list_archived(limit=20)
    return {"sessions": sessions}


@router.post("/sessions/{session_id}/restore")
async def restore_session(session_id: str, svc: SessionService = Depends(require_session_service)):
    restored = await svc.restore(session_id)
    if not restored:
        raise HTTPException(status_code=404, detail="Archived session not found")
    return {"status": "restored", "session_id": session_id}


@router.delete("/sessions/{session_id}/permanent")
async def permanently_delete_session(session_id: str, svc: SessionService = Depends(require_session_service)):
    deleted = await svc.permanently_delete(session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Archived session not found")
    return {"status": "deleted", "session_id": session_id}
