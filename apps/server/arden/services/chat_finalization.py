from collections.abc import Iterable
from datetime import UTC, datetime
from uuid import uuid4

from arden.agent import Usage
from arden.events.internal import RunCompleted, RunFailed
from arden.events.sse import MessageIngestedEvent, RunErrorEvent, RunFinishedEvent
from arden.server.bus import SessionBus
from arden.server.state import RunState, RunStatus
from arden.services.chat_context import ChatContext
from arden.services.goal_continuation import goal_continuation_prompt
from arden.services.session import SessionService


def combine_errors(message: str, errors: Iterable[BaseException | None]) -> BaseException | None:
    present = [error for error in errors if error is not None]
    if not present:
        return None
    if len(present) == 1:
        return present[0]
    return BaseExceptionGroup(message, present)


def run_input_boundary(run: RunState) -> tuple[str | None, int | None]:
    if run.input_message_index is not None and 0 <= run.input_message_index < len(run.messages):
        message = run.messages[run.input_message_index]
        client_id = message.get("client_id")
        return (client_id if isinstance(client_id, str) else run.client_id), run.input_message_index
    for index in range(len(run.messages) - 1, -1, -1):
        message = run.messages[index]
        if message.get("role") != "user":
            continue
        client_id = message.get("client_id")
        return (client_id if isinstance(client_id, str) else run.client_id), index
    return run.client_id, None


async def update_run_client_idempotency(
    session_service: SessionService | None,
    run: RunState,
    status: str,
) -> None:
    client_id, _ = run_input_boundary(run)
    if not client_id or session_service is None:
        return
    await session_service.update_chat_idempotency_key(
        session_id=run.session_id,
        client_id=client_id,
        status=status,
        run_id=run.run_id,
    )


async def emit_ingested_for_client_entries(
    batch: list[dict],
    bus: SessionBus,
    run: RunState,
    session_service: SessionService | None = None,
) -> None:
    for entry in batch:
        client_id = entry.get("client_id")
        if not client_id:
            continue
        await bus.emit(MessageIngestedEvent(client_id=client_id, run_id=run.run_id))
        if session_service is None:
            continue
        await session_service.update_chat_idempotency_key(
            session_id=run.session_id,
            client_id=client_id,
            status="ingested",
            run_id=run.run_id,
        )


async def record_completed_run(ctx: ChatContext, event: RunCompleted, *, last_seq: int | None) -> None:
    await ctx.session_service.record_run_evidence(
        run_id=ctx.run.run_id,
        session_id=ctx.run.session_id,
        source_refs=tuple(ctx.run.source_refs),
    )
    await ctx.session_service.record_chat_run_completed_with_outbox(
        event,
        ctx.run.stop_reason,
        last_seq,
    )
    ctx.run_registry.complete_run(ctx.run.run_id)
    await update_run_client_idempotency(ctx.session_service, ctx.run, RunStatus.COMPLETED.value)


async def maybe_dispatch_goal_continuation(ctx: ChatContext) -> None:
    run = ctx.run
    if run.cancelled or run.backgrounded or not ctx.goal_id or not ctx.dispatch_session_message:
        return
    goal = await ctx.session_service.get_goal(ctx.session_state.session_id)
    if not goal or goal.get("goal_id") != ctx.goal_id or goal.get("status") != "active":
        return
    if ctx.run_registry.get_active_run(ctx.session_state.session_id) is not None:
        return

    client_id = f"goal:{ctx.goal_id}:{int(datetime.now(UTC).timestamp() * 1000)}"
    await ctx.dispatch_session_message(
        ctx.session_state.session_id,
        goal_continuation_prompt(goal),
        client_id=client_id,
        skip_approvals=True,
    )


async def finalize_foreground_run(
    ctx: ChatContext,
    bus: SessionBus,
    *,
    usage: Usage,
    result: str | None,
    input_tokens: int | None,
    finished_event: RunFinishedEvent | None,
    run_failed: bool,
) -> None:
    run = ctx.run
    pending_messages = run.close_injections()
    run.usage = usage
    if run.cancelled:
        return

    run.messages.extend(pending_messages)
    if run.usage.total_tokens:
        await ctx.session_service.update_goal(
            ctx.session_state.session_id,
            goal_id=ctx.goal_id,
            tokens_used_delta=run.usage.total_tokens,
            time_used_seconds_delta=max(0, int((datetime.now(UTC) - run.created_at).total_seconds())),
        )

    metadata = {"last_input_tokens": input_tokens} if input_tokens is not None else None
    await ctx.session_service.save(ctx.session_state, run.messages, metadata=metadata)
    bus.mark_checkpoint()
    if pending_messages:
        await emit_ingested_for_client_entries(pending_messages, bus, run, ctx.session_service)

    if run_failed:
        return

    event = RunCompleted(
        run_id=run.run_id,
        session_id=ctx.session_state.session_id,
        messages=tuple(run.messages),
        usage=run.usage,
        result=result,
        source_refs=tuple(run.source_refs),
        automation_task_id=run.loop_task_id,
    )
    await record_completed_run(ctx, event, last_seq=bus.next_seq - 1)
    if finished_event is not None:
        await bus.emit(finished_event)
    await maybe_dispatch_goal_continuation(ctx)


async def record_finalization_failure(
    ctx: ChatContext,
    bus: SessionBus,
    error: BaseException,
    *,
    emit_error: bool,
    terminal_status_recorded: bool,
) -> None:
    run = ctx.run
    if terminal_status_recorded or run.status in {RunStatus.COMPLETED, RunStatus.CANCELLED}:
        return

    ctx.run_registry.error_run(run.run_id)
    error_code = "run_finalization_failed"
    safe_message = "The run finished but failed to save its final state. Please retry."
    event = RunFailed(
        run_id=run.run_id,
        session_id=ctx.session_state.session_id,
        error=safe_message,
        automation_task_id=run.loop_task_id,
    )
    record_error: BaseException | None = None
    try:
        await ctx.session_service.record_chat_run_failed_with_outbox(
            event,
            status=RunStatus.ERROR.value,
            stop_reason=str(error) or error_code,
            last_seq=bus.next_seq - 1,
            error_code=error_code,
            error_message=safe_message,
        )
        await update_run_client_idempotency(ctx.session_service, run, RunStatus.ERROR.value)
    except BaseException as exc:
        record_error = exc

    emit_failure: BaseException | None = None
    if emit_error:
        try:
            await bus.emit(
                RunErrorEvent(
                    run_id=run.run_id,
                    message=safe_message,
                    recoverable=False,
                    code=error_code,
                    debug_id=f"err_{uuid4().hex[:12]}",
                )
            )
        except BaseException as exc:
            emit_failure = exc

    reporting_error = combine_errors(
        f"Failed to record finalization failure for run {run.run_id}",
        (record_error, emit_failure),
    )
    if reporting_error is not None:
        raise reporting_error
