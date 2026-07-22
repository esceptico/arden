from collections.abc import AsyncGenerator

from pydantic import BaseModel

from arden.agent import (
    CompletionResponse,
    ProviderToolCall,
    ReasoningContentDelta,
    SpecificTool,
    ToolCallStreamDelta,
    ToolChoice,
    ToolChoiceMode,
)
from arden.llm.models import get_model
from arden.llm.router import get_completion_client


def _tool_choice_to_str(tc: ToolChoice | None) -> str | dict | None:
    match tc:
        case None | ToolChoiceMode.AUTO:
            return "auto"
        case ToolChoiceMode.NONE:
            return "none"
        case ToolChoiceMode.REQUIRED:
            return "required"
        case SpecificTool(name=name):
            return {"type": "function", "function": {"name": name}}


class ArdenLLMClient:
    def _supported_reasoning_effort(self, model: str, effort: str | None) -> str | None:
        if effort is None:
            return None
        return effort if effort in get_model(model).reasoning_efforts else None

    async def stream(
        self,
        messages: list[dict],
        model: str,
        tools: list[dict],
        tool_choice: ToolChoice | None = None,
        reasoning_effort: str | None = None,
        prompt_cache_key: str | None = None,
        deferred_tools: list[dict] | None = None,
    ) -> AsyncGenerator[str | ReasoningContentDelta | ToolCallStreamDelta | ProviderToolCall | CompletionResponse]:
        client = get_completion_client(model)
        reasoning_effort = self._supported_reasoning_effort(model, reasoning_effort)
        kwargs = {}
        if prompt_cache_key is not None:
            kwargs["prompt_cache_key"] = prompt_cache_key
        if deferred_tools:
            kwargs["deferred_tools"] = deferred_tools
        async for item in client.stream_completion(
            model=model,
            messages=messages,
            tools=tools,
            tool_choice=_tool_choice_to_str(tool_choice),
            reasoning_effort=reasoning_effort,
            **kwargs,
        ):
            yield item

    async def complete(
        self,
        model: str,
        messages: list[dict],
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format: type[BaseModel] | None = None,
    ) -> CompletionResponse:
        client = get_completion_client(model)
        kwargs: dict = {"model": model, "messages": messages}
        if temperature is not None:
            kwargs["temperature"] = temperature
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if response_format is not None:
            kwargs["response_format"] = response_format
        return await client.completion(**kwargs)


llm_client = ArdenLLMClient()
