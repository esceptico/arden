from collections.abc import AsyncGenerator
from typing import Any

import openai
from pydantic import BaseModel

from arden.agent import (
    CompletionResponse,
    ReasoningContentDelta,
    ToolCallStreamDelta,
)
from arden.llm.base import CompletionClient
from arden.llm.models import supports_native_deferred_tools
from arden.llm.openai_codex_auth import CODEX_BASE_URL, codex_request_headers, get_valid_tokens
from arden.llm.openai_responses import (
    buffered_stream_responses_completion,
    parse_responses_response,
    prepare_responses_request,
    stream_responses_completion,
)
from arden.observability.judgment import trace_client

_MODEL_PREFIX = "openai-codex/"
_DEFAULT_INSTRUCTIONS = "Follow the user's request exactly and return the requested output."


class OpenAICodexClient(CompletionClient):
    def __init__(self, timeout: float = 60.0):
        self._timeout = timeout

    async def _completion(
        self,
        messages: list[dict],
        model: str,
        tools: list[dict] | None = None,
        tool_choice: str | dict | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        reasoning_effort: str | None = None,
        response_format: type[BaseModel] | None = None,
        deferred_tools: list[dict] | None = None,
        **kwargs,
    ) -> CompletionResponse:
        client = await self._client()
        try:
            request = self._prepare(
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
            final_response: CompletionResponse | None = None
            async for item in buffered_stream_responses_completion(
                client,
                request,
                model=model,
                deferred_tools=deferred_tools,
            ):
                if isinstance(item, CompletionResponse):
                    final_response = item
            if final_response is None:
                raise RuntimeError("OpenAI Codex stream ended without a final response")
            return final_response
        finally:
            await client.close()

    async def _stream_completion(
        self,
        messages: list[dict],
        model: str,
        tools: list[dict] | None = None,
        tool_choice: str | dict | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        reasoning_effort: str | None = None,
        response_format: type[BaseModel] | None = None,
        deferred_tools: list[dict] | None = None,
        **kwargs,
    ) -> AsyncGenerator[str | ReasoningContentDelta | ToolCallStreamDelta | CompletionResponse]:
        client = await self._client()
        try:
            request = self._prepare(
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
            async for item in stream_responses_completion(
                client,
                request,
                model=model,
                deferred_tools=deferred_tools,
            ):
                yield item
        finally:
            await client.close()

    async def close(self) -> None:
        return None

    async def _client(self) -> openai.AsyncOpenAI:
        tokens = await get_valid_tokens()
        return trace_client(
            openai.AsyncOpenAI(
                api_key=tokens.access,
                base_url=CODEX_BASE_URL,
                timeout=self._timeout,
                default_headers=codex_request_headers(tokens),
            )
        )

    def _prepare(
        self,
        *,
        messages: list[dict],
        model: str,
        tools: list[dict] | None,
        tool_choice: str | dict | None,
        temperature: float | None,
        max_tokens: int | None,
        reasoning_effort: str | None,
        response_format: type[BaseModel] | None,
        deferred_tools: list[dict] | None = None,
        **kwargs,
    ) -> dict[str, Any]:
        request = prepare_responses_request(
            messages=messages,
            model=self._api_model(model),
            tools=tools,
            deferred_tools=deferred_tools if supports_native_deferred_tools(model) else None,
            tool_choice=tool_choice,
            temperature=temperature,
            max_tokens=max_tokens,
            reasoning_effort=reasoning_effort,
            response_format=response_format,
            allow_sampling_options=False,
            store=False,
            **kwargs,
        )
        request.setdefault("instructions", _DEFAULT_INSTRUCTIONS)
        return request

    def _api_model(self, model: str) -> str:
        return model.removeprefix(_MODEL_PREFIX)

    def _parse_response(
        self,
        response,
        model: str,
        output_items: list[dict[str, Any]] | None = None,
        deferred_tools: list[dict] | None = None,
    ) -> CompletionResponse:
        return parse_responses_response(response, model, output_items, client_tool_catalog=deferred_tools)
