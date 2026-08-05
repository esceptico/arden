import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from inspect import Parameter, signature
from uuid import uuid4

from arden.agent import Agent, Role, ToolOutcome, ToolOutcomeStatus, ToolResult
from arden.agent.llm.parsing import trailing_incomplete_tool_step
from arden.agent.types.events import Result, ToolCompleted
from arden.agent.types.tool_call import PendingToolCall
from arden.areas.agent import is_custodian_task_id
from arden.automation.prompts import AUTOMATION_SUFFIX, CUSTODIAN_SUFFIX
from arden.constants import CONVERSATION_GAP_THRESHOLD
from arden.context.models import AreaContext, SessionData, SessionState
from arden.core.content import (
    ContextContent,
    ContextManifestEntry,
    ImageContent,
    TextContent,
    context_manifest_entry,
    render_context,
)
from arden.core.factory import AgentConfig, create_agent
from arden.core.naming import generate_conversation_name
from arden.core.prompts import INIT_INSTRUCTION, build_system_blocks
from arden.core.spawn_spec import SpawnSpec
from arden.core.tool_result_files import find_result_file, find_result_payload_file
from arden.core.usage_tracker import UsageTracker
from arden.events.internal import RunCompleted, RunFailed
from arden.events.sse import (
    CompactionFinishedEvent,
    CompactionStartedEvent,
    MessageIngestedEvent,
    RunBackgroundedEvent,
    RunCancelledEvent,
    RunErrorEvent,
    RunFinishedEvent,
    RunStartedEvent,
    SessionUpdatedEvent,
    ThinkingEvent,
    TokenUsageEvent,
)
from arden.llm.models import Provider, get_model, supports_native_deferred_tools
from arden.logging import get_logger
from arden.notifiers.service import NotifierService
from arden.observability import activate_tracing, observed_trace
from arden.server.bus import BusRegistry, SessionBus, prime_bus_cursor_from_store
from arden.server.state import RunRegistry, RunState, RunStatus
from arden.server.stream import run_agent_loop
from arden.services.chat_context import ChatContext
from arden.services.goal_continuation import (
    goal_continuation_prompt,
)
from arden.services.session import SessionService
from arden.services.token_directive import parse_token_budget
from arden.skills.mentions import render_skill_mentions
from arden.skills.registry import SkillRegistry
from arden.tool_call_metadata import split_tool_arguments
from arden.tools.connections import render_connection_catalog
from arden.tools.core.context import BackgroundTaskRegistry, ChildIOFactory, ChildIOParams, ChildSession, IOBridge
from arden.tools.core.types import ToolAction
from arden.tools.deferred import (
    build_deferred_tools_prompt_for_schemas,
    build_native_deferred_tools_prompt_for_schemas,
)
from arden.tools.directives import load_directives
from arden.tools.executor import ToolExecutor
from arden.tools.scopes import ScopeKey, resolve
from arden.wiki.context import WikiContextBuilder

_logger = get_logger(__name__)

INIT_AUTO_APPROVE = {"fact_plan_changes", "fact_commit_changes"}


async def _recover_durable_tool_calls(
    store,
    run_id: str,
    calls: list[PendingToolCall],
    *,
    prepare_result: Callable[[ToolResult, str], ToolResult] | None = None,
) -> dict[str, ToolResult]:
    durable = {row["tool_call_id"]: row for row in await store.list_tool_calls(run_id=run_id)}
    recovered: dict[str, ToolResult] = {}
    for call in calls:
        tool_call_id = call.tool_call.id
        row = durable.get(tool_call_id)
        if not row or row["status"] in {"created", "awaiting"}:
            continue
        if row["status"] == "running":
            if row["tool_name"] == "workflow":
                # Journal-backed: re-executing the workflow tool replays its
                # completed agent spawns from the invocation journal and runs
                # live from the first miss, so a mid-flight call re-executes
                # instead of freezing into a synthesized "uncertain" result.
                # (Its children's subagent_result suspensions are keyed by
                # per-spawn lifecycle_id, not this tool_call_id, so they never
                # flip this row to 'awaiting'.)
                continue
            result = ToolResult.failure(
                code="execution_state_uncertain",
                message=(
                    "Tool execution may have started before the server restarted. "
                    "Verify the external state before retrying."
                ),
                preview="Execution state uncertain",
                status=ToolOutcomeStatus.UNCERTAIN,
                recovery_action="Verify whether the operation completed before retrying.",
            )
            recovered[tool_call_id] = prepare_result(result, tool_call_id) if prepare_result else result
            continue
        stored_result = await store.get_tool_result_for_call(run_id=run_id, tool_call_id=tool_call_id)
        content = (stored_result or {}).get("content")
        if not content:
            result_path = find_result_payload_file(tool_call_id) or find_result_file(tool_call_id)
            if result_path is not None:
                try:
                    content = await asyncio.to_thread(result_path.read_text, encoding="utf-8")
                except OSError:
                    content = None
        content = content or row.get("result_preview") or "Tool call completed."
        outcome = ToolOutcome.from_dict(row["outcome"]) if row.get("outcome") else None
        decoded = ToolResult.from_serialized_payload(content)
        if decoded is not None:
            result = ToolResult(
                content=decoded.content,
                preview=decoded.preview,
                is_error=decoded.is_error,
                data=decoded.data,
                model_content=decoded.model_content,
                source_refs=decoded.source_refs,
                outcome=decoded.outcome or outcome,
            )
        else:
            result = ToolResult(
                content=content,
                preview=row.get("result_preview") or "Recovered tool result",
                is_error=row["status"] != "success",
                outcome=outcome,
            )
        recovered[tool_call_id] = prepare_result(result, tool_call_id) if prepare_result else result
    return recovered


async def _checkpoint_tool_step(
    session_service: SessionService,
    session_state: SessionState,
    run: RunState,
    executor: ToolExecutor,
) -> bool:
    incomplete = trailing_incomplete_tool_step(run.messages)
    if not incomplete:
        return False
    await session_service.save_progress(session_state, _persistable_messages(run))
    for call in incomplete.pending_calls:
        tool = executor.registry.get(call.name)
        policy = getattr(tool, "policy", None)
        action = getattr(getattr(policy, "action", None), "value", None) or str(getattr(policy, "action", "unknown"))
        scope = getattr(getattr(policy, "scope", None), "value", None) or str(getattr(policy, "scope", "internal"))
        _, behavior_args = split_tool_arguments(call.args)
        args_json = json.dumps(behavior_args, sort_keys=True, separators=(",", ":"))
        await session_service.store.record_tool_call_created(
            run_id=run.run_id,
            session_id=run.session_id,
            tool_call_id=call.tool_call.id,
            tool_name=call.name,
            action=action,
            scope=scope,
            args_hash=hashlib.sha256(args_json.encode()).hexdigest(),
        )
    return True


class ChatIdempotencyConflict(Exception):
    def __init__(self, client_id: str):
        super().__init__("idempotency_conflict")
        self.client_id = client_id
        self.code = "idempotency_conflict"
        self.message = "A different chat request already used this client_id."


class ChatSessionNotFound(Exception):
    def __init__(self, session_id: str):
        super().__init__("session_not_found")
        self.session_id = session_id
        self.code = "session_not_found"
        self.message = f"Session {session_id!r} was not found."


def _chat_request_hash(
    *,
    session_id: str,
    message: str,
    skip_approvals: bool | None,
    images: list[dict] | None,
    context: list[dict] | None,
) -> str:
    payload = {
        "session_id": session_id,
        "message": message,
        "skip_approvals": bool(skip_approvals),
        "images": images or [],
        "context": context or [],
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _safe_error(exc: BaseException | None = None, message: str = "Chat run failed.") -> tuple[str, str, str]:
    debug_id = f"err_{uuid4().hex[:12]}"
    if exc is None:
        return "internal_error", message, debug_id

    body = getattr(exc, "body", None)
    provider_error = body.get("error") if isinstance(body, dict) and isinstance(body.get("error"), dict) else body
    payload = provider_error if isinstance(provider_error, dict) else {}
    provider_code = str(getattr(exc, "code", None) or payload.get("code") or "").strip()
    provider_message = str(payload.get("message") or getattr(exc, "message", None) or str(exc) or "").strip()
    provider_type = str(payload.get("type") or getattr(exc, "type", None) or "").strip()
    lower_message = provider_message.lower()
    class_names = " ".join(cls.__name__.lower() for cls in type(exc).__mro__)
    class_says_context_exceeded = (
        "contextwindow" in class_names or "context_window" in class_names or "contextlength" in class_names
    ) and "exceed" in class_names

    if (
        provider_code == "context_length_exceeded"
        or class_says_context_exceeded
        or "exceeds the context window" in lower_message
        or "context_length_exceeded" in lower_message
    ):
        return (
            "context_length_exceeded",
            "Your input exceeds the context window of this model. Please shorten the conversation/context "
            "or switch to a larger-context model and try again.",
            debug_id,
        )

    if provider_type == "invalid_request_error" and provider_message:
        return "provider_invalid_request", provider_message, debug_id

    return "internal_error", message, debug_id


@dataclass(frozen=True)
class ChatDeps:
    chat_model: str
    agent_config: AgentConfig
    executor: ToolExecutor
    session_service: SessionService
    run_registry: RunRegistry
    available_integrations: list[str]
    integration_errors: dict[str, str]
    connection_catalog: tuple[object, ...] = ()
    dispatch_session_message: (
        Callable[
            [str, str, str | None, bool | None, list[dict] | None],
            Awaitable[object],
        ]
        | None
    ) = None
    wiki_context: WikiContextBuilder | None = None
    skill_registry: SkillRegistry | None = None
    notifier_service: NotifierService | None = None


async def _apply_generated_session_name(ctx: ChatContext, bus: SessionBus) -> None:
    if ctx.session_name_task is None:
        return
    try:
        name = await ctx.session_name_task
        current = await ctx.session_service.load(ctx.session_state.session_id)
        if current and not await ctx.session_service.rename_if_empty(ctx.session_state.session_id, name):
            return
        ctx.session_state.name = name
        await bus.emit(SessionUpdatedEvent(session_id=ctx.session_state.session_id, name=name))
    except Exception as exc:
        _logger.warning("Session name update failed: %s", exc)


async def _record_run_started(
    service: object,
    run_id: str,
    session_id: str,
    *,
    client_id: str | None = None,
    loop_task_id: str | None = None,
    automation_id: str | None = None,
) -> None:
    fn = getattr(service, "record_chat_run_started", None)
    if fn:
        metadata = {
            key: value
            for key, value in (
                ("client_id", client_id),
                ("loop_task_id", loop_task_id),
                ("automation_id", automation_id),
            )
            if value is not None
        }
        await fn(run_id, session_id, metadata=metadata)


@dataclass(frozen=True)
class _DurableIOCallbacks:
    """The durable write-through closures an IOBridge (and the child-session
    io factory) needs. One builder serves both run_chat and the respawn host."""

    record_approval: Callable[..., Awaitable[None]]
    resolve_approval: Callable[..., Awaitable[None]]
    record_connection: Callable[..., Awaitable[None]]
    resolve_connection: Callable[..., Awaitable[None]]
    get_suspension: Callable[..., Awaitable[dict | None]]
    consume_suspension: Callable[..., Awaitable[None]]
    record_suspension: Callable[..., Awaitable[None]]
    resolve_suspension: Callable[..., Awaitable[None]]


def _durable_io_callbacks(session_service: SessionService) -> _DurableIOCallbacks:
    store = session_service.store

    async def record_approval(**kwargs) -> None:
        await store.record_tool_approval_requested(**kwargs)

    async def resolve_approval(**kwargs) -> None:
        if kwargs.get("status") == "expired":
            kwargs.pop("status", None)
            await store.expire_tool_approval(**kwargs)
        else:
            await store.resolve_tool_approval(**kwargs)

    async def record_connection(**kwargs) -> None:
        await store.record_integration_connection_requested(**kwargs)

    async def resolve_connection(**kwargs) -> None:
        await store.resolve_integration_connection(**kwargs)

    async def get_suspension(**kwargs) -> dict | None:
        return await store.get_run_suspension(**kwargs)

    async def consume_suspension(**kwargs) -> None:
        await store.mark_run_suspension_consumed(**kwargs)

    async def record_suspension(**kwargs) -> None:
        await store.record_run_suspension(**kwargs)

    async def resolve_suspension(**kwargs) -> None:
        await store.resolve_run_suspension(**kwargs)

    return _DurableIOCallbacks(
        record_approval=record_approval,
        resolve_approval=resolve_approval,
        record_connection=record_connection,
        resolve_connection=resolve_connection,
        get_suspension=get_suspension,
        consume_suspension=consume_suspension,
        record_suspension=record_suspension,
        resolve_suspension=resolve_suspension,
    )


def _wire_background_registry(bg_registry, session_service: SessionService, session_id: str) -> None:
    bg_registry.record_event = _background_event_recorder(session_service)
    bg_registry.read_result = lambda task_id: session_service.store.get_background_agent_result(session_id, task_id)
    bg_registry.claim_completion = getattr(session_service.store, "claim_background_agent_completion", None)
    bg_registry.mark_completion_delivered = getattr(session_service.store, "mark_background_completion_delivered", None)


def _background_event_recorder(session_service: SessionService):
    async def record(**event):
        store = session_service.store
        status = str(event.get("status") or "")
        task_id = str(event.get("task_id") or "")
        session_id = str(event.get("session_id") or "")
        if not task_id or not session_id:
            return None
        if status == "started":
            return await store.record_background_agent_started(
                task_id=task_id,
                session_id=session_id,
                parent_run_id=event.get("parent_run_id"),
                parent_tool_call_id=event.get("parent_tool_call_id"),
                suspension_id=event.get("suspension_id"),
                child_session_id=event.get("child_session_id"),
                agent_type=str(event.get("agent_type") or "background_research"),
                wait=bool(event.get("wait")),
                command=str(event.get("command") or ""),
                spawn_spec=event.get("spawn_spec"),
            )
        elif bool(event.get("terminal")):
            await store.record_background_agent_finished(
                task_id=task_id,
                session_id=session_id,
                status=status,
                detail=event.get("detail"),
                result_ref=event.get("result_ref"),
                result_text=event.get("result_text"),
            )
        else:
            await store.record_background_agent_event(
                task_id=task_id,
                session_id=session_id,
                status=status,
                detail=event.get("detail"),
                result_ref=event.get("result_ref"),
            )

    return record


async def _record_run_status(
    service: object,
    run_id: str,
    status: str,
    *,
    stop_reason: str | None = None,
    last_seq: int | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
) -> None:
    fn = getattr(service, "record_chat_run_status", None)
    if fn:
        kwargs = {
            "stop_reason": stop_reason,
            "last_seq": last_seq,
            "error_code": error_code,
            "error_message": error_message,
        }
        try:
            sig = signature(fn)
            accepts_kwargs = any(p.kind == Parameter.VAR_KEYWORD for p in sig.parameters.values())
            if not accepts_kwargs:
                kwargs = {key: value for key, value in kwargs.items() if key in sig.parameters}
        except (TypeError, ValueError):
            pass
        await fn(run_id, status, **kwargs)


def _is_anthropic(model: str) -> bool:
    return get_model(model).provider == Provider.ANTHROPIC


async def _resolve_session(deps: ChatDeps) -> SessionData:
    data = await deps.session_service.load()
    if data and data.messages and len(data.messages) >= 2:
        return data
    return SessionData(deps.session_service.create(), [])


async def _maybe_precompact_loop_history(
    deps: ChatDeps,
    data: SessionData,
    *,
    emit: Callable[[object], Awaitable[None]] | None = None,
) -> SessionData:
    compactor = deps.agent_config.compactor
    if not compactor:
        return data
    if not compactor.should_compact(data.messages, deps.chat_model, data.last_input_tokens):
        return data
    before_count = len(data.messages)
    if emit:
        await emit(CompactionStartedEvent())
    try:
        compacted = await compactor.maybe_compact(data.messages, deps.chat_model, data.last_input_tokens)
    except Exception:
        if emit:
            await emit(CompactionFinishedEvent(messages_before=before_count, messages_after=before_count))
        # Pre-run compaction is maintenance, not a precondition. A failed
        # summary must not silently drop history; the normal provider error and
        # forced-compaction retry path decide whether the fire can continue.
        _logger.warning(
            "Pre-run compaction failed for %s; firing uncompacted",
            data.state.session_id,
            exc_info=True,
        )
        return data
    if compacted is None:
        if emit:
            await emit(CompactionFinishedEvent(messages_before=before_count, messages_after=before_count))
        return data
    await deps.session_service.save(
        data.state,
        compacted,
        metadata={"last_input_tokens": None},
    )
    if emit:
        await emit(CompactionFinishedEvent(messages_before=before_count, messages_after=len(compacted)))
    return SessionData(
        state=data.state,
        messages=compacted,
        last_input_tokens=None,
        context_generation=data.state.context_generation,
        context_etag=data.state.context_etag,
    )


def build_user_content(
    text: str,
    images: list[dict] | None = None,
    context: list[dict] | None = None,
) -> str | list[dict]:
    if not images and not context:
        return text
    blocks = []
    if context:
        blocks.extend(ContextContent(**ctx).model_dump(exclude_none=True) for ctx in context)
    if text:
        blocks.append(TextContent(text=text).model_dump())
    if images:
        blocks.extend(ImageContent(**img).model_dump() for img in images)
    return blocks


def _time_gap_note(last_activity: datetime) -> dict | None:
    gap = (datetime.now(UTC) - last_activity).total_seconds()
    if gap < CONVERSATION_GAP_THRESHOLD:
        return None
    hours = gap / 3600
    if hours < 1:
        elapsed = f"{int(gap / 60)} minutes"
    elif hours < 24:
        elapsed = f"{hours:.1f} hours"
    else:
        elapsed = f"{hours / 24:.1f} days"
    return {"content_type": "time_since_last_message", "content": elapsed}


def _retain_user_content(messages: list[dict]) -> list[dict]:
    result = []
    for msg in messages:
        if msg.get("role") == Role.USER and isinstance(msg.get("content"), list):
            msg = {**msg, "content": [b for b in msg["content"] if b.get("type") != "context"]}
        result.append(msg)
    return result


def _close_interrupted_tool_step(messages: list[dict]) -> None:
    """Close unfinished prior turns without re-executing their tools."""
    repaired: list[dict] = []
    for index, message in enumerate(messages):
        repaired.append(message)
        calls = message.get("tool_calls")
        if message.get("role") != Role.ASSISTANT or not isinstance(calls, list):
            continue
        expected = [call.get("id") for call in calls if isinstance(call, dict) and isinstance(call.get("id"), str)]
        completed: set[str] = set()
        cursor = index + 1
        while cursor < len(messages) and messages[cursor].get("role") == Role.TOOL:
            tool_call_id = messages[cursor].get("tool_call_id")
            if isinstance(tool_call_id, str):
                completed.add(tool_call_id)
            cursor += 1
        for tool_call_id in expected:
            if tool_call_id not in completed:
                repaired.append(
                    {
                        "role": Role.TOOL,
                        "tool_call_id": tool_call_id,
                        "content": "Tool call was interrupted before completion.",
                    }
                )
    messages[:] = repaired


def _is_meta_client_id(client_id: str | None) -> bool:
    return bool(client_id and client_id.startswith(("loop:", "bg:", "goal:")))


async def _load_area_page_context(wiki_context: WikiContextBuilder | None, area_record: dict | None) -> dict | None:
    if not area_record or not area_record.get("page_path") or wiki_context is None:
        return None
    return await wiki_context.page_context(area_record["page_path"], title=area_record.get("name"))


# Below this many chars of user input there is nothing worth a scoped recall


async def _prepare_messages(
    deps: ChatDeps,
    messages: list[dict],
    user_message: str,
    tools: list[dict],
    last_activity: datetime | None = None,
    images: list[dict] | None = None,
    context: list[dict] | None = None,
    client_id: str | None = None,
    session_id: str | None = None,
    run_id: str | None = None,
    goal_context: dict | None = None,
    area_context: AreaContext | None = None,
    area_page_context: dict | None = None,
    todo_override: dict | None = None,
    append_user: bool = True,
    context_manifest: list[ContextManifestEntry] | None = None,
    automation_id: str | None = None,
    skill_mention_blocks: list[str] | None = None,
) -> list[dict]:
    wiki_context = deps.wiki_context
    wiki_resident = await wiki_context.resident_context() if wiki_context is not None else None
    wiki_retrieval = await wiki_context.retrieval_context(user_message) if wiki_context is not None else None
    memory_context = wiki_resident

    skills_context = deps.skill_registry.to_prompt_xml() if deps.skill_registry else None
    directives = load_directives()

    notifiers = deps.notifier_service.list_summary() if deps.notifier_service else None
    native_deferred_tools = supports_native_deferred_tools(deps.chat_model)
    deferred_tools_context = (
        (
            build_native_deferred_tools_prompt_for_schemas
            if native_deferred_tools
            else build_deferred_tools_prompt_for_schemas
        )(deps.executor.registry, frozenset(deps.executor.tool_services), tools)
        if deps.agent_config.deferred_tools
        else None
    )
    system_blocks = build_system_blocks(
        source_details={},
        memory_context=memory_context,
        skills_context=skills_context,
        directives=directives,
        notifiers=notifiers,
        deferred_tools_context=deferred_tools_context,
        goal_context=goal_context,
        area_context=area_context,
        area_page_context=area_page_context,
        todo_override=todo_override,
        connections_context=render_connection_catalog(list(deps.connection_catalog)),
        use_cache_control=_is_anthropic(deps.chat_model),
        native_deferred_tools=native_deferred_tools,
        context_manifest=context_manifest,
        retrieval_context=wiki_retrieval,
    )
    if automation_id is not None:
        suffix = CUSTODIAN_SUFFIX if is_custodian_task_id(automation_id) else AUTOMATION_SUFFIX
        system_blocks.append({"type": "text", "text": suffix})

    messages = _retain_user_content(messages)

    if not messages:
        messages = [{"role": Role.SYSTEM, "content": system_blocks}]
    elif isinstance(messages[0], dict) and messages[0]["role"] == Role.SYSTEM:
        messages[0]["content"] = system_blocks
    else:
        messages.insert(0, {"role": Role.SYSTEM, "content": system_blocks})

    if not append_user:
        return messages

    _close_interrupted_tool_step(messages)

    ctx_blocks = list(context or [])
    if context_manifest is not None:
        for index, raw_context in enumerate(ctx_blocks):
            parsed = ContextContent.model_validate(raw_context)
            metadata = parsed.metadata or {}
            context_manifest.append(
                context_manifest_entry(
                    content_type=parsed.content_type,
                    content=render_context(parsed),
                    source=metadata.get("source", "request"),
                    ref=metadata.get("ref", f"user-context:{index}"),
                    freshness=metadata.get("freshness", "request time"),
                    selection_reason=metadata.get("selection_reason", "explicitly supplied"),
                )
            )
    if last_activity:
        time_gap = _time_gap_note(last_activity)
        if time_gap:
            ctx_blocks.append(time_gap)

    user_msg: dict = {
        "role": Role.USER,
        "content": build_user_content(user_message, images, ctx_blocks or None),
    }
    if client_id:
        # Stamp the desktop client's UI id so /session/revert can later
        # match the saved row when the user edits this message.
        user_msg["client_id"] = client_id
        # Loop/background-dispatched messages aren't user input. Tag
        # is_meta so the model sees them but the desktop transcript hides
        # the bubble. Mirrors Claude Code's isMeta convention.
        if _is_meta_client_id(client_id):
            user_msg["is_meta"] = True
    messages.append(user_msg)

    # Mentioned skills ride as separate model-visible messages right after
    # the user's raw text; is_meta hides the bubble in the transcript.
    for block in skill_mention_blocks or []:
        messages.append({"role": Role.USER, "content": block, "is_meta": True})

    return messages


def _area_context_from_record(area: dict | None) -> AreaContext | None:
    if not area:
        return None
    return AreaContext(
        area_id=area["area_id"],
        name=area["name"],
        page_path=area.get("page_path"),
        default_cwd=area.get("default_cwd"),
        instructions=area.get("instructions"),
        knowledge_scope=area.get("knowledge_scope"),
    )


def _persistable_messages(run: RunState) -> list[dict]:
    """Return canonical model history. Normal execution only appends to it."""
    return run.messages


def _compaction_source_messages(run: RunState) -> list[dict]:
    return run.messages


async def prepare_chat(
    deps: ChatDeps,
    message: str,
    skip_approvals: bool | None = False,
    session_id: str | None = None,
    images: list[dict] | None = None,
    context: list[dict] | None = None,
    client_id: str | None = None,
    loop_task_id: str | None = None,
    automation_id: str | None = None,
    emit: Callable[[object], Awaitable[None]] | None = None,
    tool_scope: ScopeKey | None = None,
    resume_run_id: str | None = None,
) -> ChatContext:
    registry = deps.run_registry

    if session_id:
        session_data = await deps.session_service.load(session_id)
        if not session_data:
            raise ChatSessionNotFound(session_id)
    else:
        session_data = await _resolve_session(deps)
    session_state = session_data.state
    # Pin the per-chat model on first use so a later change to the global
    # default never retroactively moves this chat. deps.chat_model is the
    # already-resolved effective model (session override or global default).
    if session_state.chat_model is None:
        session_state.chat_model = deps.chat_model
    if loop_task_id:
        session_data = await _maybe_precompact_loop_history(deps, session_data, emit=emit)
    messages = session_data.messages

    is_resume = resume_run_id is not None
    user_message = message
    is_init = not is_resume and user_message.strip().lower() == "/init"
    skill_mention_blocks: list[str] = []
    if is_init:
        user_message = INIT_INSTRUCTION
    elif deps.skill_registry and not is_resume:
        # /skill-name anywhere in the text loads the skill: the raw message
        # is preserved and each skill body rides as its own is_meta message.
        skill_mention_blocks = render_skill_mentions(user_message, deps.skill_registry)

    stripped_message = message.strip()
    should_name_session = not is_resume and (
        not session_state.name and not is_init and (stripped_message or images) and not stripped_message.startswith("/")
    )
    session_name_task = (
        asyncio.create_task(generate_conversation_name(deps.chat_model, message, has_images=bool(images)))
        if should_name_session
        else None
    )

    run = registry.create_run(session_state.session_id, run_id=resume_run_id)
    run.token_budget = None if is_resume else parse_token_budget(message)
    run.loop_task_id = loop_task_id
    run.automation_id = automation_id

    tools = deps.executor.get_tools(scope=resolve(tool_scope)) if tool_scope else deps.executor.get_tools()
    get_goal = getattr(deps.session_service, "get_goal", None)
    goal_context = await get_goal(session_state.session_id) if get_goal else None
    get_todo_override = getattr(deps.session_service, "get_todo_override", None)
    todo_override = await get_todo_override(session_state.session_id) if get_todo_override else None
    area_record = await deps.session_service.get_area(session_state.area_id) if session_state.area_id else None
    area_context = _area_context_from_record(area_record)
    # Area context = the container's topic page (a capability on the area
    # row); plain area chats get None and stay ordinary.
    area_page_context = await _load_area_page_context(deps.wiki_context, area_record)
    context_manifest: list[ContextManifestEntry] = []
    messages = await _prepare_messages(
        deps,
        messages,
        user_message,
        tools,
        last_activity=session_state.last_activity,
        images=images,
        context=context,
        client_id=client_id,
        session_id=session_state.session_id,
        run_id=run.run_id,
        goal_context=goal_context,
        area_context=area_context,
        area_page_context=area_page_context,
        todo_override=todo_override,
        append_user=not is_resume,
        context_manifest=context_manifest,
        automation_id=automation_id,
        skill_mention_blocks=skill_mention_blocks,
    )

    run.messages = messages
    run.session_state = session_state
    run.context_manifest = [entry.model_dump() for entry in context_manifest]
    if is_resume:
        for index in range(len(messages) - 1, -1, -1):
            if messages[index].get("role") != Role.USER:
                continue
            prior_client_id = messages[index].get("client_id")
            run.client_id = prior_client_id if isinstance(prior_client_id, str) else None
            run.input_message_index = index
            break
    else:
        run.client_id = client_id
        run.input_message_index = len(messages) - 1
    if skip_approvals is not None:
        run.set_skip_approvals(skip_approvals)
    return ChatContext(
        run=run,
        session_state=session_state,
        is_init=is_init,
        executor=deps.executor,
        tools=tools,
        config=deps.agent_config,
        available_integrations=deps.available_integrations,
        integration_errors=deps.integration_errors,
        connection_catalog=deps.connection_catalog,
        session_service=deps.session_service,
        run_registry=deps.run_registry,
        initial_input_tokens=session_data.last_input_tokens,
        goal_id=goal_context["goal_id"] if goal_context else None,
        session_name_task=session_name_task,
        area_context=area_context,
        dispatch_session_message=deps.dispatch_session_message,
    )


def _run_input_boundary(run: RunState) -> tuple[str | None, int | None]:
    if run.input_message_index is not None and 0 <= run.input_message_index < len(run.messages):
        message = run.messages[run.input_message_index]
        client_id = message.get("client_id")
        return (client_id if isinstance(client_id, str) else run.client_id), run.input_message_index
    for index in range(len(run.messages) - 1, -1, -1):
        message = run.messages[index]
        if message.get("role") != Role.USER:
            continue
        client_id = message.get("client_id")
        return (client_id if isinstance(client_id, str) else run.client_id), index
    return run.client_id, None


async def _update_run_client_idempotency(
    session_service: SessionService | None,
    run: RunState,
    status: str,
) -> None:
    client_id, _ = _run_input_boundary(run)
    if not client_id or session_service is None:
        return
    try:
        await session_service.update_chat_idempotency_key(
            session_id=run.session_id,
            client_id=client_id,
            status=status,
            run_id=run.run_id,
        )
    except Exception:
        _logger.warning("Failed to update chat idempotency status", exc_info=True)


async def _maybe_dispatch_goal_continuation(ctx: ChatContext, run: RunState, *, run_failed: bool) -> None:
    if run_failed or run.cancelled or run.backgrounded or not ctx.goal_id:
        return
    if not ctx.dispatch_session_message:
        return
    get_goal = getattr(ctx.session_service, "get_goal", None)
    if not get_goal:
        return
    goal = await get_goal(ctx.session_state.session_id)
    if not goal or goal.get("goal_id") != ctx.goal_id or goal.get("status") != "active":
        return

    active = ctx.run_registry.get_active_run(ctx.session_state.session_id)
    if active is not None:
        return

    client_id = f"goal:{ctx.goal_id}:{int(datetime.now(UTC).timestamp() * 1000)}"
    # Keywords, deliberately: this call once broke silently when the
    # dispatcher grew a positional param between client_id and the rest.
    await ctx.dispatch_session_message(
        ctx.session_state.session_id,
        goal_continuation_prompt(goal),
        client_id=client_id,
        skip_approvals=True,
    )


async def submit_chat_message(
    run_registry: RunRegistry,
    build_deps: Callable[[], ChatDeps],
    buses: BusRegistry,
    *,
    message: str,
    session_id: str,
    skip_approvals: bool | None = False,
    images: list[dict] | None = None,
    context: list[dict] | None = None,
    client_id: str | None = None,
    session_service: SessionService | None = None,
    tool_scope: ScopeKey | None = None,
    loop_task_id: str | None = None,
    automation_id: str | None = None,
    queue_if_busy: bool = True,
) -> dict[str, str]:
    if session_service is not None and await session_service.load(session_id) is None:
        raise ChatSessionNotFound(session_id)
    async with run_registry.session_lock(session_id):
        return await _submit_chat_message_locked(
            run_registry,
            build_deps,
            buses,
            message=message,
            session_id=session_id,
            skip_approvals=skip_approvals,
            images=images,
            context=context,
            client_id=client_id,
            session_service=session_service,
            tool_scope=tool_scope,
            loop_task_id=loop_task_id,
            automation_id=automation_id,
            queue_if_busy=queue_if_busy,
        )


async def resume_suspended_chat_run(
    run_registry: RunRegistry,
    build_deps: Callable[[], ChatDeps],
    buses: BusRegistry,
    *,
    run_id: str,
    session_id: str,
    session_service: SessionService,
) -> dict[str, str] | None:
    async with run_registry.session_lock(session_id):
        active = run_registry.get_active_run(session_id)
        if active:
            return {"run_id": active.run_id, "session_id": session_id, "status": "active"}
        durable_run = await session_service.store.get_chat_run(run_id)
        if (
            not durable_run
            or durable_run["session_id"] != session_id
            or durable_run["status"] != "interrupted"
            or durable_run["stop_reason"] != "server_restart"
        ):
            return None
        session_data = await session_service.load(session_id)
        if not session_data or not trailing_incomplete_tool_step(session_data.messages):
            return None
        metadata = durable_run.get("metadata")
        stored_loop_task_id = metadata.get("loop_task_id") if isinstance(metadata, dict) else None
        loop_task_id = stored_loop_task_id if isinstance(stored_loop_task_id, str) else None
        stored_automation_id = metadata.get("automation_id") if isinstance(metadata, dict) else None
        automation_id = stored_automation_id if isinstance(stored_automation_id, str) else None

        await prime_bus_cursor_from_store(buses, session_id, session_service.store)
        bus = buses.get_or_create(session_id)
        ctx = await prepare_chat(
            build_deps(),
            message="",
            skip_approvals=False,
            session_id=session_id,
            emit=bus.emit,
            resume_run_id=run_id,
            loop_task_id=loop_task_id,
            automation_id=automation_id,
        )
        task = asyncio.create_task(run_chat(ctx, bus, buses))
        ctx.run.task = task
        _install_cancel_fallback(ctx.run, bus, run_registry, task, session_service)
        if ctx.run.client_id:
            run_registry.register_otid(session_id, ctx.run.client_id, run_id)
        return {"run_id": run_id, "session_id": session_id, "status": "resumed"}


async def respawn_background_agent(
    run_registry: RunRegistry,
    build_deps: Callable[[], ChatDeps],
    buses: BusRegistry,
    *,
    session_id: str,
    task_id: str,
    session_service: SessionService,
) -> bool:
    """Re-dispatch an interrupted DETACHED agent from its stored SpawnSpec.

    Boot-time recovery host: there is no live parent run, so this builds the
    same agent scaffolding run_chat does (factory ToolContext with spawn_fn,
    child-session io factory, durable bg-registry wiring) and re-issues the
    spawn under the original task id. Must no-op cleanly — the outbox event
    that triggers it completes on dispatch and is never retried for outcome.
    """
    row = await session_service.store.get_background_agent_run(session_id, task_id)
    if row is None or row["status"] != "interrupted" or not row.get("spawn_spec"):
        return False
    bg_registry = run_registry.get_background_registry(session_id)
    if any(pending_id == task_id for pending_id, _ in bg_registry.list_pending()):
        return False
    session_data = await session_service.load(session_id)
    if session_data is None:
        return False
    spec = SpawnSpec.from_json(row["spawn_spec"])
    system_prompt = next((m.get("content") for m in spec.context if m.get("role") == "system"), None)
    task = next((m.get("content") for m in reversed(spec.context) if m.get("role") == "user"), None)
    if not isinstance(system_prompt, str) or not isinstance(task, str):
        return False

    deps = build_deps()
    await prime_bus_cursor_from_store(buses, session_id, session_service.store)
    bus = buses.get_or_create(session_id)
    _wire_background_registry(bg_registry, session_service, session_id)

    host_run = RunState(run_id=f"respawn-{uuid4().hex[:10]}", session_id=session_id)

    async def _on_bg_result(messages: list[dict]) -> None:
        # No live parent run: run_finished=True so the result always dispatches
        # a fresh hidden run instead of queueing into a dead injection queue.
        await _handle_background_result(
            run=host_run,
            session_id=session_id,
            messages=messages,
            dispatch_session_message=deps.dispatch_session_message,
            run_finished=True,
        )

    bg_registry.on_result = _on_bg_result

    callbacks = _durable_io_callbacks(session_service)
    io = IOBridge(
        pending_approvals=host_run.pending_approvals,
        emit=bus.emit,
        record_approval=callbacks.record_approval,
        resolve_approval=callbacks.resolve_approval,
        record_connection=callbacks.record_connection,
        resolve_connection=callbacks.resolve_connection,
        get_suspension=callbacks.get_suspension,
        consume_suspension=callbacks.consume_suspension,
        record_suspension=callbacks.record_suspension,
        resolve_suspension=callbacks.resolve_suspension,
        approval_timeout_seconds=deps.agent_config.approval_timeout_seconds,
    )
    child_io_factory = make_child_io_factory(
        buses,
        run_registry,
        session_service,
        record_approval=callbacks.record_approval,
        resolve_approval=callbacks.resolve_approval,
        get_suspension=callbacks.get_suspension,
        consume_suspension=callbacks.consume_suspension,
        record_suspension=callbacks.record_suspension,
        resolve_suspension=callbacks.resolve_suspension,
        approval_timeout_seconds=deps.agent_config.approval_timeout_seconds,
        dispatch_session_message=deps.dispatch_session_message,
    )
    # No area_context: the spec's system prompt already carries the area block
    # (resolved at the original spawn); a ToolContext area would append it again.
    try:
        host_agent = create_agent(
            executor=deps.executor,
            config=deps.agent_config,
            tools=deps.executor.get_tools(),
            session_state=session_data.state,
            run_id=host_run.run_id,
            io=io,
            background_tasks=bg_registry,
            resource_observations=host_run.resource_observations,
            run_registry=run_registry,
            child_io_factory=child_io_factory,
        )
        tool_ctx = host_agent.executor.ctx
        await tool_ctx.spawn_fn(
            tool_ctx,
            task,
            system_prompt=system_prompt,
            tools=list(spec.tools),
            timeout=spec.timeout_seconds,
            model_override=spec.model,
            reasoning_effort_override=spec.reasoning_effort,
            parent_id=spec.parent.tool_call_id,
            isolation=spec.isolation,
            wait=False,
            agent_type=spec.agent_type,
            kind="background",
            task_id=task_id,
        )
    except Exception as exc:
        # A respawn that dies here would otherwise be silent: the outbox event
        # completed on dispatch, and the row would sit "interrupted" forever
        # while the roster claims the agent exists. Settle the row as failed
        # and tell the session, shaped like any other failed agent report.
        _logger.exception("Background agent respawn host failed", session_id=session_id, task_id=task_id)
        failure = f"[respawn failed] {exc}"
        await session_service.store.record_background_agent_finished(
            task_id=task_id,
            session_id=session_id,
            status="failed",
            result_text=failure,
        )
        if deps.dispatch_session_message is not None:
            notification = (
                f'<background_agent_result session_id="{row.get("child_session_id") or ""}" status="failed">\n'
                "This is a hidden completion event. The user cannot see this message.\n"
                f"The agent could not be restarted after a server restart: {failure}\n"
                "This agent's run failed. If you still need this work, assign a follow-up with "
                'app_followup_task(session_id="...") or spawn a fresh agent.\n'
                "</background_agent_result>"
            )
            await deps.dispatch_session_message(session_id, notification, f"bg:{task_id}:respawn-failed", True, None)
            await session_service.store.mark_background_completion_delivered(
                session_id=session_id,
                task_id=task_id,
                completion_id=f"bg:{task_id}:failed",
            )
        return False
    return True


async def _submit_chat_message_locked(
    run_registry: RunRegistry,
    build_deps: Callable[[], ChatDeps],
    buses: BusRegistry,
    *,
    message: str,
    session_id: str,
    skip_approvals: bool | None = False,
    images: list[dict] | None = None,
    context: list[dict] | None = None,
    client_id: str | None = None,
    session_service: SessionService | None = None,
    tool_scope: ScopeKey | None = None,
    loop_task_id: str | None = None,
    automation_id: str | None = None,
    queue_if_busy: bool = True,
) -> dict[str, str]:
    if not queue_if_busy and run_registry.get_accepting_run(session_id) is not None:
        return {"run_id": "", "session_id": session_id, "status": "busy"}

    is_meta_client = _is_meta_client_id(client_id)
    request_hash = _chat_request_hash(
        session_id=session_id,
        message=message,
        skip_approvals=skip_approvals,
        images=images,
        context=context,
    )
    durable_idempotency = None

    # Durable idempotent retry: a POST with a client_id we already accepted
    # returns the same run_id instead of starting a second run or re-queueing
    # the message. The in-memory RunRegistry mapping remains only a hot cache.
    if client_id and session_service:
        claimed, durable_idempotency = await session_service.claim_chat_idempotency_key(
            session_id=session_id,
            client_id=client_id,
            request_hash=request_hash,
        )
        if not claimed:
            if durable_idempotency.get("status") == RunStatus.CANCELLED.value:
                # DELETE can arrive before the matching POST. The durable
                # tombstone wins over the request body so this id can never
                # recreate a queue entry later.
                return {
                    "run_id": durable_idempotency.get("run_id") or "",
                    "session_id": session_id,
                    "status": RunStatus.CANCELLED.value,
                }
            if durable_idempotency["request_hash"] != request_hash:
                raise ChatIdempotencyConflict(client_id)
            if durable_idempotency.get("run_id"):
                run_registry.register_otid(session_id, client_id, durable_idempotency["run_id"])
                return {
                    "run_id": durable_idempotency["run_id"],
                    "session_id": session_id,
                    "status": durable_idempotency.get("status") or "accepted",
                }
    elif client_id:
        existing = run_registry.lookup_otid(session_id, client_id)
        if existing:
            return {"run_id": existing.run_id, "session_id": session_id, "status": "accepted"}

    active_run = run_registry.get_accepting_run(session_id)
    if active_run:
        if skip_approvals is not None:
            active_run.set_skip_approvals(bool(skip_approvals))
        entry: dict = {
            "role": Role.USER,
            "content": build_user_content(message, images, context),
        }
        if client_id:
            entry["client_id"] = client_id
            if is_meta_client:
                entry["is_meta"] = True
        queued = active_run.queue_injection(entry)
        if not queued:
            active_run = None
        elif entry.get("client_id") and session_service:
            queued_client_id = str(entry["client_id"])
            try:
                await session_service.update_chat_idempotency_key(
                    session_id=session_id,
                    client_id=queued_client_id,
                    status="queued",
                    run_id=active_run.run_id,
                )
            except BaseException:
                active_run.cancel_injection(queued_client_id)
                raise
        if active_run is not None:
            if loop_task_id and not active_run.loop_task_id:
                active_run.loop_task_id = loop_task_id
            if client_id:
                run_registry.register_otid(session_id, client_id, active_run.run_id)
            return {"run_id": active_run.run_id, "session_id": session_id, "status": "queued"}

    deps = build_deps()
    cursor_service = session_service or deps.session_service
    await prime_bus_cursor_from_store(buses, session_id, cursor_service.store)
    bus = buses.get_or_create(session_id)
    ctx = await prepare_chat(
        deps,
        message,
        skip_approvals,
        session_id=session_id,
        images=images,
        context=context,
        client_id=client_id,
        loop_task_id=loop_task_id,
        emit=bus.emit,
        tool_scope=tool_scope,
        automation_id=automation_id,
    )
    try:
        await _record_run_started(
            deps.session_service,
            ctx.run.run_id,
            ctx.session_state.session_id,
            client_id=client_id,
            loop_task_id=loop_task_id,
            automation_id=automation_id,
        )
        if client_id and session_service:
            await session_service.update_chat_idempotency_key(
                session_id=session_id,
                client_id=client_id,
                status="running",
                run_id=ctx.run.run_id,
            )
        if is_meta_client:
            ctx.run.is_meta_run = True
        # Persist the user message before the agent starts streaming. Without
        # this the message exists only in `run.messages` (in memory) until the
        # first `on_step_finish` save fires — and a client that switches away
        # and back in that window would lose the message: loadHistory returns
        # the pre-submit history and the SSE replay carries agent events, not
        # the user message itself.
        await deps.session_service.save_progress(ctx.session_state, _persistable_messages(ctx.run))
        await deps.session_service.record_run_context_manifest(
            run_id=ctx.run.run_id,
            session_id=ctx.run.session_id,
            manifest=ctx.run.context_manifest,
        )
    except BaseException as exc:
        ctx.run_registry.error_run(ctx.run.run_id)
        safe_message = "The run failed before streaming could start. Please retry."
        event = RunFailed(
            run_id=ctx.run.run_id,
            session_id=ctx.run.session_id,
            error=safe_message,
            automation_task_id=ctx.run.loop_task_id,
        )
        await deps.session_service.record_chat_run_failed_with_outbox(
            event,
            status=RunStatus.ERROR.value,
            stop_reason=str(exc) or "pre_task_setup_failed",
            last_seq=bus.next_seq - 1,
            error_code="run_preparation_failed",
            error_message=safe_message,
        )
        if client_id and session_service:
            await session_service.update_chat_idempotency_key(
                session_id=session_id,
                client_id=client_id,
                status=RunStatus.ERROR.value,
                run_id=ctx.run.run_id,
            )
        raise
    if ctx.run.cancelled:
        ctx.run.accepting_injections = False
        await bus.emit(RunCancelledEvent(run_id=ctx.run.run_id))
        run_registry.finish_cancelled(ctx.run.run_id)
        event = RunFailed(
            run_id=ctx.run.run_id,
            session_id=ctx.run.session_id,
            error="chat run cancelled",
            automation_task_id=ctx.run.loop_task_id,
        )
        await deps.session_service.record_chat_run_failed_with_outbox(
            event,
            status=RunStatus.CANCELLED.value,
            stop_reason="cancelled",
            last_seq=bus.next_seq - 1,
        )
        await _update_run_client_idempotency(deps.session_service, ctx.run, RunStatus.CANCELLED.value)
        if client_id:
            run_registry.register_otid(session_id, client_id, ctx.run.run_id)
        return {"run_id": ctx.run.run_id, "session_id": ctx.session_state.session_id, "status": "cancelled"}
    activate_tracing(ctx.session_state.session_id, tags="chat")
    task = asyncio.create_task(run_chat(ctx, bus, buses))
    ctx.run.task = task
    _install_cancel_fallback(ctx.run, bus, run_registry, task, deps.session_service)
    if client_id:
        run_registry.register_otid(session_id, client_id, ctx.run.run_id)

    return {
        "run_id": ctx.run.run_id,
        "session_id": ctx.session_state.session_id,
        "status": "running",
    }


async def _emit_cancelled_terminal_fallback(
    run: RunState,
    bus: SessionBus,
    run_registry: RunRegistry,
    session_service: SessionService,
) -> None:
    if run.cancel_terminal_emitted:
        return
    event = RunFailed(
        run_id=run.run_id,
        session_id=run.session_id,
        error="chat run cancelled",
        automation_task_id=run.loop_task_id,
    )
    await session_service.record_chat_run_failed_with_outbox(
        event,
        status=RunStatus.CANCELLED.value,
        stop_reason="cancelled",
        last_seq=bus.next_seq - 1,
    )
    await _update_run_client_idempotency(session_service, run, RunStatus.CANCELLED.value)
    await bus.emit(RunCancelledEvent(run_id=run.run_id))
    run_registry.finish_cancelled(run.run_id)


def _install_cancel_fallback(
    run: RunState,
    bus: SessionBus,
    run_registry: RunRegistry,
    task: asyncio.Task,
    session_service: SessionService,
) -> None:
    loop = asyncio.get_running_loop()

    def _on_done(done: asyncio.Task) -> None:
        if not done.cancelled() or run.cancel_terminal_emitted:
            return
        run.cancelled = True
        loop.create_task(_emit_cancelled_terminal_fallback(run, bus, run_registry, session_service))

    task.add_done_callback(_on_done)


async def _record_completed_run(ctx: ChatContext, event: RunCompleted, *, last_seq: int | None) -> None:
    await ctx.session_service.record_run_evidence(
        run_id=ctx.run.run_id,
        session_id=ctx.run.session_id,
        source_refs=list(ctx.run.source_refs),
    )
    await ctx.session_service.record_chat_run_completed_with_outbox(
        event,
        ctx.run.stop_reason,
        last_seq,
    )
    await _update_run_client_idempotency(ctx.session_service, ctx.run, RunStatus.COMPLETED.value)
    ctx.run_registry.complete_run(ctx.run.run_id)


@observed_trace("chat.backgrounded", tags="chat")
async def _drain_backgrounded(
    gen,
    agent: Agent,
    ctx: ChatContext,
    bg_registry,
    tracker: UsageTracker,
    bus: SessionBus,
) -> None:
    """Continue draining an agent stream silently after the run was backgrounded."""
    activate_tracing(ctx.session_state.session_id, tags="chat")
    read_only = set()
    for t in ctx.tools:
        name = t["function"]["name"]
        tool = ctx.executor.registry.get(name)
        if tool and tool.policy.action == ToolAction.READ:
            read_only.add(name)
    agent.tools = [t for t in agent.tools if t["function"]["name"] in read_only]
    messages = ctx.run.messages
    drain_error: Exception | None = None
    drain_result: Result | None = None
    try:
        async for item in gen:
            if isinstance(item, ToolCompleted):
                for source_ref in item.source_refs:
                    ctx.run.add_source_ref(source_ref.to_dict())
            if isinstance(item, Result):
                drain_result = item
                ctx.run.stop_reason = item.stop_reason.value
    except asyncio.CancelledError:
        if ctx.run.cancelled:
            event = RunFailed(
                run_id=ctx.run.run_id,
                session_id=ctx.session_state.session_id,
                error="chat run cancelled",
                automation_task_id=ctx.run.loop_task_id,
            )
            await ctx.session_service.record_chat_run_failed_with_outbox(
                event,
                status=RunStatus.CANCELLED.value,
                stop_reason="cancelled",
                last_seq=None,
            )
            await _update_run_client_idempotency(ctx.session_service, ctx.run, RunStatus.CANCELLED.value)
            ctx.run_registry.finish_cancelled(ctx.run.run_id)
        return
    except Exception as exc:
        drain_error = exc
        _logger.exception("Backgrounded drain failed (run_id=%s)", ctx.run.run_id)
    ctx.run.usage = tracker.usage

    save_lock = asyncio.Lock()

    async def _save_snapshot() -> None:
        latest = await ctx.session_service.load(ctx.session_state.session_id)
        current_messages = list(latest.messages) if latest else []
        full_view = _persistable_messages(ctx.run)
        await ctx.session_service.save(
            ctx.session_state,
            _merge_background_messages(current_messages, full_view),
        )

    async def _save_directly(injected: list[dict]) -> None:
        async with save_lock:
            existing_client_ids = {message.get("client_id") for message in messages if message.get("client_id")}
            messages.extend(
                message
                for message in injected
                if not message.get("client_id") or message.get("client_id") not in existing_client_ids
            )
            try:
                await _save_snapshot()
            except Exception:
                _logger.exception("Background direct-save failed (run_id=%s)", ctx.run.run_id)
                raise

    bg_registry.on_result = _save_directly

    if injected := ctx.run.drain_injections():
        await _emit_ingested_for_client_entries(injected, bus, ctx.run, ctx.session_service)
        messages.extend(injected)

    try:
        async with save_lock:
            await _save_snapshot()
            bus.mark_checkpoint()
            if drain_error is not None:
                ctx.run_registry.error_run(ctx.run.run_id)
                error_code = "background_drain_failed"
                safe_message = "The backgrounded run failed while finishing. Please retry."
                event = RunFailed(
                    run_id=ctx.run.run_id,
                    session_id=ctx.session_state.session_id,
                    error=safe_message,
                    automation_task_id=ctx.run.loop_task_id,
                )
                await ctx.session_service.record_chat_run_failed_with_outbox(
                    event,
                    status=RunStatus.ERROR.value,
                    stop_reason=str(drain_error) or error_code,
                    last_seq=None,
                    error_code=error_code,
                    error_message=safe_message,
                )
                await _update_run_client_idempotency(ctx.session_service, ctx.run, RunStatus.ERROR.value)
                return
            if ctx.run.usage.total_tokens:
                await ctx.session_service.update_goal(
                    ctx.session_state.session_id,
                    goal_id=ctx.goal_id,
                    tokens_used_delta=ctx.run.usage.total_tokens,
                    time_used_seconds_delta=max(0, int((datetime.now(UTC) - ctx.run.created_at).total_seconds())),
                )
            event = RunCompleted(
                run_id=ctx.run.run_id,
                session_id=ctx.session_state.session_id,
                messages=tuple(ctx.run.messages),
                usage=ctx.run.usage,
                # drain_result is the agent's Result object — passing it
                # whole made the payload non-JSON-serializable, so every
                # backgrounded run silently lost its run-completed event.
                result=drain_result.text if drain_result else None,
                source_refs=tuple(ctx.run.source_refs),
                automation_task_id=ctx.run.loop_task_id,
            )
            await _record_completed_run(ctx, event, last_seq=None)
    except Exception as exc:
        _logger.exception("Backgrounded final save failed (run_id=%s)", ctx.run.run_id)
        ctx.run_registry.error_run(ctx.run.run_id)
        error_code = "run_finalization_failed"
        safe_message = "The run finished but failed to save its final state. Please retry."
        event = RunFailed(
            run_id=ctx.run.run_id,
            session_id=ctx.session_state.session_id,
            error=safe_message,
            automation_task_id=ctx.run.loop_task_id,
        )
        await ctx.session_service.record_chat_run_failed_with_outbox(
            event,
            status=RunStatus.ERROR.value,
            stop_reason=str(exc) or error_code,
            last_seq=None,
            error_code=error_code,
            error_message=safe_message,
        )
        await _update_run_client_idempotency(ctx.session_service, ctx.run, RunStatus.ERROR.value)


async def _emit_ingested_for_client_entries(
    batch: list[dict],
    bus: SessionBus,
    run: RunState,
    session_service: SessionService | None = None,
) -> None:
    # Read but do NOT pop — the saved message keeps its client_id so the
    # desktop can later reference it via /session/revert (edit flow) or
    # /sessions/{id}/branch.
    for entry in batch:
        client_id = entry.get("client_id")
        if client_id:
            await bus.emit(MessageIngestedEvent(client_id=client_id, run_id=run.run_id))
            update_idempotency = getattr(session_service, "update_chat_idempotency_key", None)
            if update_idempotency:
                await update_idempotency(
                    session_id=run.session_id,
                    client_id=client_id,
                    status="ingested",
                    run_id=run.run_id,
                )


def _merge_background_messages(current: list[dict], background: list[dict]) -> list[dict]:
    prefix_len = 0
    for current_msg, background_msg in zip(current, background, strict=False):
        current_id = current_msg.get("message_id") or current_msg.get("client_id")
        background_id = background_msg.get("message_id") or background_msg.get("client_id")
        same_identity = (
            isinstance(current_id, str)
            and bool(current_id)
            and isinstance(background_id, str)
            and current_id == background_id
        )
        if not same_identity and current_msg != background_msg:
            break
        prefix_len += 1
    return [*current, *background[prefix_len:]]


def _response_input_tokens(response) -> int | None:
    usage = getattr(response, "usage", None)
    if not usage:
        return None
    return usage.prompt_tokens + usage.cache_read_tokens + usage.cache_write_tokens


def _build_get_pending(
    bus: SessionBus,
    run: RunState,
    session_service: SessionService | None = None,
    background_tasks: BackgroundTaskRegistry | None = None,
):
    """Closure that drains pending injects and emits message_ingested per client
    entry. When the session's background registry is wired, a hidden
    <agent_roster> note rides along whenever the live-agent set changed — the
    per-run system prompt is frozen at run start, so the pending-messages hook
    is the one per-step channel that can carry it."""

    async def _get_pending() -> list[dict]:
        batch = run.drain_injections()
        if batch:
            await _emit_ingested_for_client_entries(batch, bus, run, session_service)
        if background_tasks is not None:
            roster = background_tasks.roster_note_if_changed()
            if roster is not None:
                batch.append(roster)
        return batch

    return _get_pending


async def _handle_background_result(
    *,
    run: RunState,
    session_id: str,
    messages: list[dict],
    dispatch_session_message: Callable[
        [str, str, str | None, bool | None, list[dict] | None],
        Awaitable[object],
    ]
    | None,
    run_finished: bool,
) -> None:
    if not run_finished and not run.cancelled:
        run.queue_injections(messages)
        return
    # A user Stop cascades cancellation to children. Their settlement events
    # must not start fresh model runs after the parent has been cancelled.
    if any(
        message.get("background_status") == "cancelled" or str(message.get("client_id") or "").endswith(":cancelled")
        for message in messages
    ):
        return
    if not dispatch_session_message:
        return
    for message in messages:
        content = message.get("content")
        if not isinstance(content, str) or not content:
            continue
        client_id = message.get("client_id")
        await dispatch_session_message(
            session_id,
            content,
            client_id if isinstance(client_id, str) else None,
            True,
            None,
        )


def make_child_io_factory(
    buses: BusRegistry,
    run_registry: RunRegistry,
    session_service: SessionService,
    *,
    record_approval: Callable[..., Awaitable[None]],
    resolve_approval: Callable[..., Awaitable[None]],
    approval_timeout_seconds: int,
    get_suspension: Callable[..., Awaitable[dict | None]] | None = None,
    consume_suspension: Callable[..., Awaitable[None]] | None = None,
    record_suspension: Callable[..., Awaitable[None]] | None = None,
    resolve_suspension: Callable[..., Awaitable[None]] | None = None,
    dispatch_session_message: Callable[..., Awaitable[object]] | None = None,
) -> ChildIOFactory:
    """Build the factory that gives a spawned FULL subagent its OWN session bus,
    framed with the standard run lifecycle so the child session streams live
    exactly like a top-level thread: RunStarted on open → content (text/tool/
    reasoning, re-based to depth 0 by the spawner) → RunFinished/RunCancelled on
    finish. The client's live-vs-done state is driven by that lifecycle, not the
    content. The child reuses the parent run's approval plumbing + run_id — the
    run id matches runtime.active_run (surfaced via mark_session_active →
    get_active_run), so the load-time signal and the bus signal agree on one id.
    `finish` is idempotent so the terminal frame is emitted exactly once."""

    async def _child_io_factory(params: ChildIOParams) -> ChildSession:
        # A freshly-spawned child has a brand-new session id with zero prior
        # events, so there is no durable cursor to prime — get_or_create starts at
        # seq 1. (prime_bus_cursor_from_store is for clients RECONNECTING to a
        # stale session; doing it per spawn was 2 blocking DB reads for nothing.)
        child_bus = buses.get_or_create(params.session_id)
        child_io = IOBridge(
            pending_approvals=params.pending_approvals,
            emit=child_bus.emit,
            record_approval=record_approval,
            resolve_approval=resolve_approval,
            get_suspension=get_suspension,
            consume_suspension=consume_suspension,
            record_suspension=record_suspension,
            resolve_suspension=resolve_suspension,
            approval_timeout_seconds=approval_timeout_seconds,
        )
        await child_bus.emit(RunStartedEvent(session_id=params.session_id, run_id=params.run_id))

        # The child session's OWN registry: its spawns (grandchildren) reserve,
        # record durable rows, and deliver through it — the per-session topology
        # cancel_subtree walks. Durable wiring is identical to the top session's.
        child_registry = run_registry.get_background_registry(params.session_id)
        _wire_background_registry(child_registry, session_service, params.session_id)
        parent_registry = params.parent_background_tasks
        child_task_id = params.task_id

        async def _on_child_bg_result(messages: list[dict]) -> None:
            # Nested result delivery: a grandchild's completion surfaces to the
            # CHILD mid-turn by queueing into its steering inbox (drained by its
            # loop at the next step, Claude Code-style). If the child's loop has
            # already ended, RE-INVOKE the child — a hidden continuation run in
            # its own session with the result as input (Claude Code's idle
            # re-invocation with codex's parent routing) — so the parent agent
            # itself decides what its child's report means. Bubbling up the
            # parent chain is only the terminal fallback when no dispatcher is
            # wired; the result is never dropped.
            if parent_registry is None or child_task_id is None:
                _logger.warning("Child background result had no parent registry — dropping")
                return
            leftovers = [m for m in messages if not parent_registry.queue_injection(child_task_id, m)]
            if not leftovers:
                return
            if dispatch_session_message is not None:
                for message in leftovers:
                    content = str(message.get("content") or "")
                    client_id = message.get("client_id")
                    await dispatch_session_message(params.session_id, content, client_id, True, None)
                return
            await parent_registry.inject(leftovers)

        child_registry.on_result = _on_child_bg_result

        finished = False

        async def _finish(status: str) -> None:
            nonlocal finished
            if finished:
                return
            finished = True
            if status == "cancelled":
                await child_bus.emit(RunCancelledEvent(run_id=params.run_id))
            elif status == "completed":
                await child_bus.emit(RunFinishedEvent(run_id=params.run_id))
            else:
                await child_bus.emit(
                    RunErrorEvent(
                        run_id=params.run_id,
                        message=f"Child agent ended with status: {status}.",
                        recoverable=status == "interrupted",
                        code=f"child_agent_{status}",
                    )
                )

        async def _aclose() -> None:
            # Run finished: drain durable writes and drop the bus unless a client
            # is still viewing the child session (subscribers keep it alive) OR its
            # run is still active. Use the SAME liveness predicate as the SSE
            # endpoint teardown (routers/chat.py) so the two paths agree — a
            # `lambda: False` here raced a connecting viewer and reset the bus seq.
            await buses.remove_if_idle(
                params.session_id,
                is_active=lambda: run_registry.get_active_run(params.session_id) is not None,
            )

        return ChildSession(io=child_io, finish=_finish, aclose=_aclose, background_tasks=child_registry)

    return _child_io_factory


@observed_trace("chat", tags="chat")
async def run_chat(ctx: ChatContext, bus: SessionBus, buses: BusRegistry) -> None:
    """Run agent loop, push all events to bus. Fire-and-forget."""
    run = ctx.run
    session_state = ctx.session_state
    activate_tracing(session_state.session_id, tags="chat")
    agent: Agent | None = None
    tracker = UsageTracker()
    result: str | None = None
    run_finished = False
    run_failed = False
    run_finished_event: RunFinishedEvent | None = None
    terminal_status_recorded = False
    terminal_status_error: Exception | None = None

    async def _emit_cancelled_terminal() -> None:
        if run.cancel_terminal_emitted:
            return
        await bus.emit(RunCancelledEvent(run_id=run.run_id))
        ctx.run_registry.finish_cancelled(run.run_id)

    async def _record_cancelled_run() -> None:
        nonlocal terminal_status_recorded
        event = RunFailed(
            run_id=run.run_id,
            session_id=session_state.session_id,
            error="chat run cancelled",
            automation_task_id=run.loop_task_id,
        )
        await ctx.session_service.record_chat_run_failed_with_outbox(
            event,
            status=RunStatus.CANCELLED.value,
            stop_reason="cancelled",
            last_seq=bus.next_seq - 1,
        )
        terminal_status_recorded = True
        await _update_run_client_idempotency(ctx.session_service, run, RunStatus.CANCELLED.value)

    try:
        await bus.emit(
            RunStartedEvent(
                session_id=session_state.session_id,
                run_id=run.run_id,
                integrations=ctx.available_integrations,
                integration_errors=ctx.integration_errors,
                skip_approvals=run.approval_controls.skip_approvals,
                session_name=session_state.name or "",
                is_meta_run=bool(run.loop_task_id) or run.is_meta_run,
                meta_client_id=_run_input_boundary(run)[0] if run.is_meta_run else None,
            )
        )
        if ctx.session_name_task is not None:
            asyncio.create_task(_apply_generated_session_name(ctx, bus))

        await bus.emit(
            ThinkingEvent(
                session_id=session_state.session_id,
                run_id=run.run_id,
                status="processing...",
            )
        )

        bg_registry = ctx.run_registry.get_background_registry(session_state.session_id)
        _wire_background_registry(bg_registry, ctx.session_service, session_state.session_id)

        callbacks = _durable_io_callbacks(ctx.session_service)

        io = IOBridge(
            pending_approvals=run.pending_approvals,
            pending_inputs=run.pending_inputs,
            pending_connections=run.pending_connections,
            pending_connection_descriptors=run.pending_connection_descriptors,
            emit=bus.emit,
            record_approval=callbacks.record_approval,
            resolve_approval=callbacks.resolve_approval,
            record_connection=callbacks.record_connection,
            resolve_connection=callbacks.resolve_connection,
            get_suspension=callbacks.get_suspension,
            consume_suspension=callbacks.consume_suspension,
            record_suspension=callbacks.record_suspension,
            resolve_suspension=callbacks.resolve_suspension,
            approval_timeout_seconds=ctx.config.approval_timeout_seconds,
        )

        # A spawned FULL subagent gets its own session; this gives it an io bound
        # to THAT session's bus, framed with the standard run lifecycle so the
        # child session streams live exactly like a top-level thread. It reuses
        # the parent run's approval plumbing + run_id.
        child_io_factory = make_child_io_factory(
            buses,
            ctx.run_registry,
            ctx.session_service,
            record_approval=callbacks.record_approval,
            resolve_approval=callbacks.resolve_approval,
            get_suspension=callbacks.get_suspension,
            consume_suspension=callbacks.consume_suspension,
            record_suspension=callbacks.record_suspension,
            resolve_suspension=callbacks.resolve_suspension,
            approval_timeout_seconds=ctx.config.approval_timeout_seconds,
            dispatch_session_message=ctx.dispatch_session_message,
        )

        def _new_agent() -> Agent:
            return create_agent(
                executor=ctx.executor,
                config=ctx.config,
                tools=ctx.tools,
                session_state=session_state,
                run_id=run.run_id,
                io=io,
                approval_controls=run.approval_controls,
                extra_auto_approve=INIT_AUTO_APPROVE if ctx.is_init else None,
                background_tasks=bg_registry,
                loaded_tools=run.loaded_tools,
                resource_observations=run.resource_observations,
                loop_task_id=run.loop_task_id,
                automation_id=run.automation_id,
                parent_tracker=tracker,
                initial_input_tokens=ctx.initial_input_tokens,
                run_registry=ctx.run_registry,
                area_context=ctx.area_context,
                token_budget=run.token_budget,
                child_io_factory=child_io_factory,
            )

        async def _track_response(response) -> None:
            await _checkpoint_tool_step(ctx.session_service, session_state, run, ctx.executor)
            await tracker.track(response)
            await bus.emit(
                TokenUsageEvent(
                    run_id=run.run_id,
                    usage=response.usage.to_dict(),
                    cost=tracker.cost,
                    message_count=len(_persistable_messages(run)),
                    context_input_tokens=_response_input_tokens(response),
                )
            )

        async def _checkpoint(_step: int, _response, messages: list[dict]) -> None:
            # Persist after each agent step so a client navigating back to
            # this session sees the in-flight conversation, not the pre-run
            # snapshot. Lightweight UPDATE, leaves metadata alone — the
            # final save in `finally` re-stamps last_input_tokens.
            #
            # mark_checkpoint advances the bus's "events <= this seq are
            # on disk" watermark. The buffer keeps growing (bounded by
            # RECENT_BUFFER_MAX); a reconnecting client with a cursor at
            # or above this watermark gets a clean buffered replay, while
            # a cursor below it triggers a stream_reset → history reload.
            #
            # `messages` here is the same list object as canonical run history.
            await ctx.session_service.save_progress(session_state, _persistable_messages(run))
            bus.mark_checkpoint()
            await _record_run_status(
                ctx.session_service,
                run.run_id,
                RunStatus.RUNNING.value,
                last_seq=bus.checkpoint_seq,
            )

        async def _on_bg_result(messages: list[dict]) -> None:
            await _handle_background_result(
                run=run,
                session_id=session_state.session_id,
                messages=messages,
                dispatch_session_message=ctx.dispatch_session_message,
                run_finished=run_finished,
            )

        bg_registry.on_result = _on_bg_result

        def _configure_agent(next_agent: Agent) -> Agent:
            async def _recover_tool_calls(calls: list[PendingToolCall]) -> dict[str, ToolResult]:
                prepare_result = getattr(next_agent.executor, "prepare_recovered_result", None)
                return await _recover_durable_tool_calls(
                    ctx.session_service.store,
                    run.run_id,
                    calls,
                    prepare_result=prepare_result,
                )

            next_agent.hooks.on_response = _track_response
            next_agent.hooks.on_step_finish = _checkpoint
            next_agent.hooks.get_pending_messages = _build_get_pending(bus, run, ctx.session_service, bg_registry)
            next_agent.hooks.recover_tool_calls = _recover_tool_calls
            return next_agent

        async def _force_context_compaction() -> bool:
            compactor = getattr(ctx.config, "compactor", None)
            if compactor is None:
                return False
            source_messages = _compaction_source_messages(run)
            before_count = len(source_messages)
            await bus.emit(CompactionStartedEvent(run_id=run.run_id))
            try:
                force_compact = getattr(compactor, "force_compact", None)
                model = getattr(ctx.config, "model", "")
                if force_compact:
                    compacted = await force_compact(source_messages, model)
                else:
                    forced_input_tokens = get_model(model).max_context_tokens
                    compacted = await compactor.maybe_compact(
                        source_messages,
                        model,
                        forced_input_tokens,
                    )
            except Exception:
                await bus.emit(
                    CompactionFinishedEvent(
                        run_id=run.run_id,
                        messages_before=before_count,
                        messages_after=before_count,
                    )
                )
                raise
            if compacted is None:
                await bus.emit(
                    CompactionFinishedEvent(
                        run_id=run.run_id,
                        messages_before=before_count,
                        messages_after=before_count,
                    )
                )
                return False

            run.messages.clear()
            run.messages.extend(compacted)
            await ctx.session_service.save(
                session_state,
                _persistable_messages(run),
                metadata={"last_input_tokens": None},
            )
            bus.mark_checkpoint()
            await bus.emit(
                CompactionFinishedEvent(
                    run_id=run.run_id,
                    messages_before=before_count,
                    messages_after=len(run.messages),
                )
            )
            return True

        async def _run_agent_with_context_retry():
            nonlocal agent
            try:
                return await run_agent_loop(ctx, agent, bus)
            except Exception as exc:
                if _safe_error(exc)[0] != "context_length_exceeded":
                    raise
                if not await _force_context_compaction():
                    raise
                agent = _configure_agent(_new_agent())
                return await run_agent_loop(ctx, agent, bus)

        agent = _configure_agent(_new_agent())

        run.status = RunStatus.RUNNING
        await _record_run_status(ctx.session_service, run.run_id, RunStatus.RUNNING.value)
        await _update_run_client_idempotency(ctx.session_service, run, RunStatus.RUNNING.value)

        result, bg_gen = await _run_agent_with_context_retry()

        if bg_gen is not None:
            io.emit = None
            await bus.emit(RunBackgroundedEvent(run_id=run.run_id, session_id=session_state.session_id))
            await _record_run_status(ctx.session_service, run.run_id, "backgrounded", last_seq=bus.next_seq - 1)
            await _update_run_client_idempotency(ctx.session_service, run, "backgrounded")
            run.drain_task = asyncio.create_task(_drain_backgrounded(bg_gen, agent, ctx, bg_registry, tracker, bus))
            return

        if result is None:
            await _record_cancelled_run()
            return

        run.usage = tracker.usage

        # Note: we used to emit a final text-content event with the cumulative
        # `result` here, which made sense back when the wire had a separate
        # "final text" event (replace-semantics). Under AG-UI the text was
        # already streamed through TEXT_MESSAGE_CONTENT deltas — re-emitting
        # would just duplicate the assistant message in the UI.

        usage_dict = run.usage.to_dict()
        usage_dict["cost"] = tracker.cost
        usage_dict["exclusive_cost"] = tracker.own_cost
        run_finished_event = RunFinishedEvent(
            run_id=run.run_id,
            usage=usage_dict,
            context_input_tokens=_response_input_tokens(getattr(agent, "_last_response", None)),
            message_count=len(_persistable_messages(run)),
        )
        run_finished = True

    except asyncio.CancelledError:
        run.cancelled = True
        await _emit_cancelled_terminal()
        await _record_cancelled_run()
        return

    except Exception as e:
        run_failed = True
        error_code, safe_message, debug_id = _safe_error(e)
        if error_code in {"context_length_exceeded", "provider_invalid_request"}:
            _logger.warning(
                "Chat provider rejected request (run_id=%s, session_id=%s, code=%s, debug_id=%s): %s",
                run.run_id,
                session_state.session_id,
                error_code,
                debug_id,
                safe_message,
            )
        else:
            _logger.exception(
                "Chat failed (run_id=%s, session_id=%s, debug_id=%s)",
                run.run_id,
                session_state.session_id,
                debug_id,
            )
        await bus.emit(
            RunErrorEvent(
                run_id=run.run_id,
                message=safe_message,
                recoverable=False,
                code=error_code,
                debug_id=debug_id,
            )
        )
        ctx.run_registry.error_run(run.run_id)
        event = RunFailed(
            run_id=run.run_id,
            session_id=session_state.session_id,
            error=safe_message,
            automation_task_id=run.loop_task_id,
        )
        try:
            await ctx.session_service.record_chat_run_failed_with_outbox(
                event,
                status=RunStatus.ERROR.value,
                stop_reason=str(e) or error_code,
                last_seq=bus.next_seq - 1,
                error_code=error_code,
                error_message=safe_message,
            )
            terminal_status_recorded = True
        except Exception as status_exc:
            terminal_status_error = status_exc
            _logger.exception("Failed to record terminal error status for run %s", run.run_id)
        try:
            await _update_run_client_idempotency(ctx.session_service, run, RunStatus.ERROR.value)
        except Exception:
            _logger.warning("Failed to update terminal error idempotency for run %s", run.run_id, exc_info=True)
    finally:
        if not run.backgrounded:
            if run.cancelled:
                run.close_injections()
                run.usage = tracker.usage
                run_finished = True
            else:
                pending_messages = run.close_injections()
                if pending_messages:
                    # Emit ingestion events for any client-stamped entries so the
                    # frontend queue UI clears, then absorb them into history.
                    try:
                        await _emit_ingested_for_client_entries(pending_messages, bus, run, ctx.session_service)
                    except Exception:
                        _logger.exception("Failed to emit message_ingested in finally")
                    run.messages.extend(pending_messages)
                run.usage = tracker.usage
                run_finished = True
                input_tokens = _response_input_tokens(getattr(agent, "_last_response", None))
                metadata = {"last_input_tokens": input_tokens} if input_tokens is not None else None
                try:
                    if run.usage.total_tokens:
                        await ctx.session_service.update_goal(
                            session_state.session_id,
                            goal_id=ctx.goal_id,
                            tokens_used_delta=run.usage.total_tokens,
                            time_used_seconds_delta=max(0, int((datetime.now(UTC) - run.created_at).total_seconds())),
                        )
                    await ctx.session_service.save(session_state, _persistable_messages(run), metadata=metadata)
                    # Disk now holds the canonical end-of-run state; advance the
                    # checkpoint watermark so any cursor below it gets a
                    # stream_reset → history reload on reconnect.
                    bus.mark_checkpoint()
                    if run_failed:
                        if terminal_status_error is not None:
                            raise RuntimeError(
                                f"Failed to record terminal error status for run {run.run_id}"
                            ) from terminal_status_error
                        return
                    event = RunCompleted(
                        run_id=run.run_id,
                        session_id=session_state.session_id,
                        messages=tuple(run.messages),
                        usage=run.usage,
                        result=result,
                        source_refs=tuple(run.source_refs),
                        automation_task_id=run.loop_task_id,
                    )
                    await _record_completed_run(ctx, event, last_seq=bus.next_seq - 1)
                    terminal_status_recorded = True
                    if run_finished_event is not None:
                        await bus.emit(run_finished_event)
                    try:
                        await _maybe_dispatch_goal_continuation(ctx, run, run_failed=run_failed)
                    except Exception:
                        _logger.warning("Failed to dispatch goal continuation", exc_info=True)
                except Exception as exc:
                    _logger.exception(
                        "Chat finalization failed (run_id=%s, session_id=%s)",
                        run.run_id,
                        session_state.session_id,
                    )
                    if terminal_status_recorded:
                        _logger.warning(
                            "Preserving previously recorded terminal status after finalization failure "
                            "(run_id=%s, session_id=%s)",
                            run.run_id,
                            session_state.session_id,
                        )
                        return
                    ctx.run_registry.error_run(run.run_id)
                    error_code = "run_finalization_failed"
                    safe_message = "The run finished but failed to save its final state. Please retry."
                    _ignored_code, _ignored_message, debug_id = _safe_error(exc)
                    if not run_failed and not run.cancelled:
                        await bus.emit(
                            RunErrorEvent(
                                run_id=run.run_id,
                                message=safe_message,
                                recoverable=False,
                                code=error_code,
                                debug_id=debug_id,
                            )
                        )
                    event = RunFailed(
                        run_id=run.run_id,
                        session_id=session_state.session_id,
                        error=safe_message,
                        automation_task_id=run.loop_task_id,
                    )
                    try:
                        await ctx.session_service.record_chat_run_failed_with_outbox(
                            event,
                            status=RunStatus.ERROR.value,
                            stop_reason=str(exc) or error_code,
                            last_seq=bus.next_seq - 1,
                            error_code=error_code,
                            error_message=safe_message,
                        )
                        await _update_run_client_idempotency(ctx.session_service, run, RunStatus.ERROR.value)
                    except Exception as fallback_exc:
                        _logger.warning("Failed to persist chat finalization error status", exc_info=True)
                        if terminal_status_error is not None:
                            raise RuntimeError(
                                f"Failed to persist terminal status for run {run.run_id}"
                            ) from fallback_exc
