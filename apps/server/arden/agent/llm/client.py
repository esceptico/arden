from collections.abc import AsyncGenerator
from typing import Protocol

from pydantic import BaseModel

from arden.agent.types.llm import CompletionResponse, ProviderToolCall, ReasoningContentDelta, ToolCallStreamDelta
from arden.agent.types.tool_choice import ToolChoice


class LLMClient(Protocol):
    async def stream(
        self,
        messages: list[dict],
        model: str,
        tools: list[dict],
        tool_choice: ToolChoice | None = None,
        reasoning_effort: str | None = None,
        prompt_cache_key: str | None = None,
        deferred_tools: list[dict] | None = None,
    ) -> AsyncGenerator[str | ReasoningContentDelta | ToolCallStreamDelta | ProviderToolCall | CompletionResponse]: ...

    async def complete(
        self,
        model: str,
        messages: list[dict],
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format: type[BaseModel] | None = None,
    ) -> CompletionResponse: ...
