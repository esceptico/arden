from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Any, Literal

from arden.agent.model_request import ModelRequest, ModelRequestNext
from arden.core.compactor import Compactor
from arden.events.sse import CompactionFinishedEvent, CompactionStartedEvent


class CompactionModelRequestMiddleware:
    def __init__(
        self,
        compactor: Compactor | None = None,
        on_compact: Callable[[], None] | None = None,
        get_rehydration_state: Callable[[], dict[str, Any]] | None = None,
        apply_rehydration_state: Callable[[dict[str, Any] | None], None] | None = None,
        emit: Callable[[Any], Awaitable[None]] | None = None,
        run_id: str = "",
        initial_input_tokens: int | None = None,
        scope: Literal["run", "agent"] = "run",
        parent_tool_call_id: str | None = None,
    ):
        self.compactor = compactor
        self.on_compact = on_compact
        self.get_rehydration_state = get_rehydration_state
        self.apply_rehydration_state = apply_rehydration_state
        self.emit = emit
        self.run_id = run_id
        self.initial_input_tokens = initial_input_tokens
        self.scope = scope
        self.parent_tool_call_id = parent_tool_call_id

    def _started_event(self) -> CompactionStartedEvent:
        return CompactionStartedEvent(
            run_id=self.run_id,
            scope=self.scope,
            parent_tool_call_id=self.parent_tool_call_id,
        )

    def _finished_event(self, before: int, after: int) -> CompactionFinishedEvent:
        return CompactionFinishedEvent(
            run_id=self.run_id,
            messages_before=before,
            messages_after=after,
            scope=self.scope,
            parent_tool_call_id=self.parent_tool_call_id,
        )

    async def __call__(self, request: ModelRequest, next_request: ModelRequestNext) -> ModelRequest:
        prepared = await next_request(request)
        if not self.compactor:
            return prepared

        last_input_tokens = (
            prepared.previous_response.usage.input_tokens if prepared.previous_response else self.initial_input_tokens
        )

        # Pre-check so we only emit start/finish around an *actual* compaction.
        should = self.compactor.should_compact(prepared.messages, prepared.model, last_input_tokens)

        emitted_started = False
        if should and self.emit:
            await self.emit(self._started_event())
            emitted_started = True

        rehydration_state = self.get_rehydration_state() if self.get_rehydration_state else None

        # If summarization fails (timeout, provider error) we MUST still emit
        # Finished so the client clears its "compacting" spinner. Without
        # this, the indicator would stay until the user switches sessions.
        try:
            compacted = await self.compactor.maybe_compact(
                prepared.messages,
                prepared.model,
                last_input_tokens,
                rehydration_state=rehydration_state,
            )
        except Exception:
            if emitted_started and self.emit:
                same = len(prepared.messages)
                await self.emit(self._finished_event(same, same))
            raise

        if compacted is None:
            if emitted_started and self.emit:
                same = len(prepared.messages)
                await self.emit(self._finished_event(same, same))
            return prepared

        if self.emit:
            await self.emit(self._finished_event(len(prepared.messages), len(compacted)))

        if self.on_compact:
            self.on_compact()
        if self.apply_rehydration_state:
            self.apply_rehydration_state(rehydration_state)
        return replace(prepared, messages=compacted, compaction_replacement=compacted)
