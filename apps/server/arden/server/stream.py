import asyncio
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING

from arden.agent import Result
from arden.agent.types.events import ToolCompleted
from arden.events.sse import (
    RunCancelledEvent,
    TextMessageContentEvent,
    TextMessageEndEvent,
    TextMessageStartEvent,
    agent_events_to_sse,
)
from arden.observability import activate_tracing, observed_trace
from arden.server.state import RunStatus

if TYPE_CHECKING:
    from arden.agent import Agent
    from arden.server.bus import SessionBus
    from arden.services.chat import ChatContext


@observed_trace("chat.agent", tags="chat")
async def run_agent_loop(
    ctx: "ChatContext", agent: "Agent", bus: "SessionBus"
) -> tuple[str | None, AsyncGenerator | None]:
    activate_tracing(getattr(ctx.run, "session_id", None), tags="chat")
    messages = ctx.run.messages

    result = ""
    gen = agent.stream(messages)
    open_text_message_id: str | None = None
    open_text_parts: list[str] = []
    open_text_depth = 0
    open_text_parent_id: str | None = None
    pending_text_end: TextMessageEndEvent | None = None

    def note_text_event(event) -> None:
        nonlocal open_text_depth, open_text_message_id, open_text_parent_id, open_text_parts, pending_text_end
        if isinstance(event, TextMessageStartEvent):
            open_text_message_id = event.message_id
            open_text_parts = []
            open_text_depth = event.depth
            open_text_parent_id = event.parent_id
        elif isinstance(event, TextMessageContentEvent):
            open_text_message_id = open_text_message_id or event.message_id
            open_text_parts.append(event.delta)
            open_text_depth = event.depth
            open_text_parent_id = event.parent_id
        elif isinstance(event, TextMessageEndEvent):
            open_text_message_id = None
            open_text_parts = []
            open_text_depth = 0
            open_text_parent_id = None
            pending_text_end = None

    async def close_open_text() -> None:
        nonlocal open_text_depth, open_text_message_id, open_text_parent_id, open_text_parts, pending_text_end
        if pending_text_end is not None:
            event = pending_text_end
        elif open_text_message_id is not None:
            event = TextMessageEndEvent(
                message_id=open_text_message_id,
                content="".join(open_text_parts),
                depth=open_text_depth,
                parent_id=open_text_parent_id,
            )
        else:
            return
        await bus.emit(event)
        pending_text_end = None
        open_text_message_id = None
        open_text_parts = []
        open_text_depth = 0
        open_text_parent_id = None

    async def emit_cancelled_terminal() -> None:
        if ctx.run.cancel_terminal_emitted:
            return
        await close_open_text()
        await bus.emit(RunCancelledEvent(run_id=ctx.run.run_id))
        registry = getattr(ctx, "run_registry", None)
        if registry is not None:
            registry.finish_cancelled(ctx.run.run_id)
        else:
            ctx.run.cancel_terminal_emitted = True
            ctx.run.status = RunStatus.CANCELLED

    try:
        async for item in gen:
            if isinstance(item, ToolCompleted):
                for source_ref in item.source_refs:
                    ctx.run.add_source_ref(source_ref.to_dict())
            if ctx.run.backgrounded:
                await close_open_text()
                return None, gen
            if isinstance(item, Result):
                if ctx.run.cancelled:
                    break
                result = item.text
                ctx.run.stop_reason = item.stop_reason.value
                ctx.run.structured_output = item.output
            else:
                for sse in agent_events_to_sse(item):
                    if isinstance(sse, TextMessageEndEvent):
                        pending_text_end = sse
                        await bus.emit(sse)
                        note_text_event(sse)
                    else:
                        note_text_event(sse)
                        await bus.emit(sse)
                    await asyncio.sleep(0)
                if ctx.run.cancelled:
                    break
    except asyncio.CancelledError:
        ctx.run.cancelled = True
        result = ""

    if ctx.run.cancelled:
        await emit_cancelled_terminal()
        return None, None

    return result, None
