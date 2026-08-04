from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from arden.agent.types.llm import CompletionResponse
from arden.agent.types.tool_choice import ToolChoice


@dataclass(frozen=True)
class ModelRequest:
    step: int
    messages: list[dict]
    model: str
    tools: list[dict]
    deferred_tools: list[dict]
    tool_choice: ToolChoice
    reasoning_effort: str | None
    previous_response: CompletionResponse | None
    # Most middleware fields shape one provider request only. Full compaction
    # is the sole exception: it deliberately starts a new canonical context
    # epoch. The explicit field prevents ordinary request projection from
    # rewriting live history.
    compaction_replacement: list[dict] | None = None


ModelRequestNext = Callable[[ModelRequest], Awaitable[ModelRequest]]
ModelRequestMiddleware = Callable[[ModelRequest, ModelRequestNext], Awaitable[ModelRequest]]


async def apply_model_request_middlewares(
    request: ModelRequest,
    model_request_middlewares: Sequence[ModelRequestMiddleware],
) -> ModelRequest:
    async def dispatch(index: int, current: ModelRequest) -> ModelRequest:
        if index == len(model_request_middlewares):
            return current

        middleware = model_request_middlewares[index]

        async def next_request(next_current: ModelRequest) -> ModelRequest:
            return await dispatch(index + 1, next_current)

        return await middleware(current, next_request)

    return await dispatch(0, request)
