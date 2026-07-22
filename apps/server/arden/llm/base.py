from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator

from pydantic import BaseModel

from arden.agent.types.llm import CompletionResponse, ProviderToolCall, ReasoningContentDelta, ToolCallStreamDelta
from arden.llm.retry import with_retry


class CompletionClient(ABC):
    @abstractmethod
    async def _completion(
        self,
        messages: list[dict],
        model: str,
        tools: list[dict] | None = None,
        tool_choice: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        reasoning_effort: str | None = None,
        response_format: type[BaseModel] | None = None,
        deferred_tools: list[dict] | None = None,
        **kwargs,
    ) -> CompletionResponse: ...

    async def completion(self, **kwargs) -> CompletionResponse:
        return await with_retry(self._completion, **kwargs)

    async def _stream_completion(
        self,
        messages: list[dict],
        model: str,
        tools: list[dict] | None = None,
        tool_choice: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        reasoning_effort: str | None = None,
        response_format: type[BaseModel] | None = None,
        deferred_tools: list[dict] | None = None,
        **kwargs,
    ) -> AsyncGenerator[str | ReasoningContentDelta | ToolCallStreamDelta | ProviderToolCall | CompletionResponse]:
        """Yield text deltas, then the final CompletionResponse.

        Default: non-streaming fallback.
        """
        response = await self._completion(
            messages=messages,
            model=model,
            tools=tools,
            tool_choice=tool_choice,
            temperature=temperature,
            max_tokens=max_tokens,
            reasoning_effort=reasoning_effort,
            response_format=response_format,
            deferred_tools=deferred_tools,
            **kwargs,
        )
        text = response.choices[0].message.content if response.choices else None
        if text:
            yield text
        yield response

    async def stream_completion(
        self, **kwargs
    ) -> AsyncGenerator[str | ReasoningContentDelta | ToolCallStreamDelta | ProviderToolCall | CompletionResponse]:
        async for item in self._stream_completion(**kwargs):
            yield item

    @abstractmethod
    async def close(self) -> None: ...


class EmbeddingClient(ABC):
    @abstractmethod
    async def _embedding(
        self,
        texts: list[str],
        model: str,
    ) -> list[list[float]]: ...

    async def embedding(self, **kwargs) -> list[list[float]]:
        return await with_retry(self._embedding, **kwargs)

    @abstractmethod
    async def close(self) -> None: ...
