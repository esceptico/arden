from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from ntrp.agent.types.llm import CompletionResponse
from ntrp.agent.types.tool_call import PendingToolCall
from ntrp.agent.types.tools import ToolResult

OnResponseFn = Callable[[CompletionResponse], Awaitable[None]]
OnStepFinishFn = Callable[[int, CompletionResponse, list[dict]], Awaitable[None]]
OnErrorFn = Callable[[Exception], Awaitable[None]]
OnFinishFn = Callable[[str, int, list[dict]], Awaitable[None]]
GetPendingMessagesFn = Callable[[], Awaitable[list[dict]]]
RecoverToolCallsFn = Callable[[list[PendingToolCall]], Awaitable[dict[str, ToolResult]]]


@dataclass
class AgentHooks:
    on_response: OnResponseFn | None = None
    on_step_finish: OnStepFinishFn | None = None
    on_error: OnErrorFn | None = None
    on_finish: OnFinishFn | None = None
    get_pending_messages: GetPendingMessagesFn | None = None
    recover_tool_calls: RecoverToolCallsFn | None = None
