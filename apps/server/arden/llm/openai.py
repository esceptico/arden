from collections.abc import AsyncGenerator

import httpx
import openai
from pydantic import BaseModel

from arden.agent import (
    Choice,
    CompletionResponse,
    FinishReason,
    FunctionCall,
    Message,
    ReasoningContentDelta,
    Role,
    ToolCall,
    ToolCallStreamDelta,
    Usage,
)
from arden.agent.types.tools import tool_result_content_for_model
from arden.core.content import render_context
from arden.llm.base import CompletionClient, EmbeddingClient
from arden.llm.models import Provider, get_model, supports_native_deferred_tools
from arden.llm.openai_responses import (
    complete_responses_completion,
    parse_responses_response,
    prepare_responses_request,
)
from arden.llm.utils import blocks_to_text
from arden.observability.judgment import trace_client

# Keys we attach for arden internals that must be stripped before an API call.
_INTERNAL_MESSAGE_KEYS = frozenset(
    {
        "client_id",
        "created_at",
        "message_id",
        "compaction",
        "data",
        "outcome",
        "anthropic_content",
        "provider_tool_calls",
        "background_status",
    }
)


def _map_finish_reason(reason: str | None) -> FinishReason:
    if not reason:
        return FinishReason.STOP
    try:
        return FinishReason(reason)
    except ValueError:
        return FinishReason.STOP


class OpenAIClient(CompletionClient, EmbeddingClient):
    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: float = 60.0,
        native_openai: bool = True,
    ):
        self._client = trace_client(openai.AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=timeout))
        self._native_openai = native_openai

    def _prepare(
        self,
        messages: list[dict],
        model: str,
        tools: list[dict] | None,
        tool_choice: str | None,
        temperature: float | None,
        max_tokens: int | None,
        reasoning_effort: str | None,
        response_format: type[BaseModel] | None,
        **kwargs,
    ) -> dict:
        messages = self._preprocess_messages(messages)
        request: dict = {"model": model, "messages": messages}

        if not self._supports_temperature(model):
            temperature = None

        token_key = "max_completion_tokens" if self._native_openai else "max_tokens"
        optional = {
            "tools": tools,
            "tool_choice": tool_choice if tools else None,
            "temperature": temperature,
            token_key: max_tokens,
        }
        request.update({k: v for k, v in optional.items() if v is not None})

        if response_format is not None:
            schema = response_format.model_json_schema()
            request["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": response_format.__name__,
                    "schema": schema,
                    "strict": False,
                },
            }

        if extra := kwargs.get("extra_body"):
            request["extra_body"] = dict(extra)

        if self._native_openai and (prompt_cache_key := kwargs.get("prompt_cache_key")):
            request["prompt_cache_key"] = prompt_cache_key

        self._apply_reasoning_effort(request, model, reasoning_effort)

        return request

    def _supports_temperature(self, model: str) -> bool:
        if not self._native_openai:
            return True
        return not get_model(model).reasoning_efforts

    def _uses_responses_api(
        self,
        model: str,
        tools: list[dict] | None,
        reasoning_effort: str | None,
        deferred_tools: list[dict] | None = None,
    ) -> bool:
        return (
            self._native_openai
            and bool(tools)
            and (reasoning_effort is not None or (bool(deferred_tools) and supports_native_deferred_tools(model)))
        )

    def _prepare_responses(
        self,
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
    ) -> dict:
        return prepare_responses_request(
            messages=messages,
            model=model,
            tools=tools,
            deferred_tools=deferred_tools if supports_native_deferred_tools(model) else None,
            tool_choice=tool_choice,
            temperature=temperature if self._supports_temperature(model) else None,
            max_tokens=max_tokens,
            reasoning_effort=reasoning_effort,
            response_format=response_format,
            allow_sampling_options=True,
            store=False,
            **kwargs,
        )

    def _apply_reasoning_effort(self, request: dict, model: str, effort: str | None) -> None:
        if effort is None:
            return
        if self._native_openai:
            request["reasoning_effort"] = effort
            return
        provider = get_model(model).provider
        if provider == Provider.OPENROUTER:
            extra_body = dict(request.get("extra_body") or {})
            extra_body["reasoning"] = {"effort": "high" if effort == "xhigh" else effort}
            request["extra_body"] = extra_body
            return
        if provider == Provider.CUSTOM:
            request["reasoning_effort"] = effort

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
    ) -> CompletionResponse:
        if self._uses_responses_api(model, tools, reasoning_effort, deferred_tools):
            request = self._prepare_responses(
                messages,
                model,
                tools,
                tool_choice,
                temperature,
                max_tokens,
                reasoning_effort,
                response_format,
                deferred_tools=deferred_tools,
                **kwargs,
            )
            response = await self._client.responses.create(**request)
            return parse_responses_response(response, model)

        request = self._prepare(
            messages,
            model,
            tools,
            tool_choice,
            temperature,
            max_tokens,
            reasoning_effort,
            response_format,
            **kwargs,
        )
        response = await self._client.chat.completions.create(**request)
        return self._parse_response(response, model)

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
    ) -> AsyncGenerator[str | ReasoningContentDelta | ToolCallStreamDelta | CompletionResponse]:
        if self._uses_responses_api(model, tools, reasoning_effort, deferred_tools):
            request = self._prepare_responses(
                messages,
                model,
                tools,
                tool_choice,
                temperature,
                max_tokens,
                reasoning_effort,
                response_format,
                deferred_tools=deferred_tools,
                **kwargs,
            )
            async for item in complete_responses_completion(self._client, request, model=model):
                yield item
            return

        request = self._prepare(
            messages,
            model,
            tools,
            tool_choice,
            temperature,
            max_tokens,
            reasoning_effort,
            response_format,
            **kwargs,
        )
        request["stream"] = True
        request["stream_options"] = {"include_usage": True}

        try:
            stream = await self._client.chat.completions.create(**request)
        except (httpx.RemoteProtocolError, openai.APIConnectionError) as exc:
            raise RuntimeError("OpenAI chat completion stream disconnected before completion") from exc

        content_parts: list[str] = []
        tool_call_chunks: dict[int, dict] = {}
        finish_reason = FinishReason.STOP
        usage_chunk = None
        reasoning_parts: list[str] = []

        try:
            async for chunk in stream:
                if chunk.usage:
                    usage_chunk = chunk.usage
                if not chunk.choices:
                    continue

                choice = chunk.choices[0]
                if choice.finish_reason:
                    finish_reason = _map_finish_reason(choice.finish_reason)
                delta = choice.delta

                if delta.content:
                    yield delta.content
                    content_parts.append(delta.content)

                if rc := getattr(delta, "reasoning_content", None):
                    reasoning_parts.append(rc)
                    yield ReasoningContentDelta(rc)

                if delta.tool_calls:
                    for tc in delta.tool_calls:
                        if tc.index not in tool_call_chunks:
                            tool_call_chunks[tc.index] = {"id": "", "name": "", "arguments": ""}
                        entry = tool_call_chunks[tc.index]
                        tool_id = tc.id or None
                        name = None
                        arguments_delta = None
                        if tc.id:
                            entry["id"] = tc.id
                        if tc.function:
                            name = tc.function.name or None
                            arguments_delta = tc.function.arguments or None
                            if tc.function.name:
                                entry["name"] = tc.function.name
                            if tc.function.arguments:
                                entry["arguments"] += tc.function.arguments
                        if tool_id or name or arguments_delta:
                            yield ToolCallStreamDelta(
                                index=tc.index,
                                tool_id=tool_id,
                                name=name,
                                arguments_delta=arguments_delta,
                            )
        except (httpx.RemoteProtocolError, openai.APIConnectionError) as exc:
            raise RuntimeError("OpenAI chat completion stream disconnected before completion") from exc

        content = "".join(content_parts) or None
        tool_calls = None
        if tool_call_chunks:
            tool_calls = [
                ToolCall(
                    id=tc["id"],
                    type="function",
                    function=FunctionCall(name=tc["name"], arguments=tc["arguments"]),
                )
                for _, tc in sorted(tool_call_chunks.items())
            ]
            for index, tc in sorted(tool_call_chunks.items()):
                yield ToolCallStreamDelta(
                    index=index,
                    tool_id=tc["id"] or None,
                    name=tc["name"] or None,
                    done=True,
                )

        if usage_chunk:
            details = getattr(usage_chunk, "prompt_tokens_details", None)
            cache_read = (details.cached_tokens or 0) if details else 0
            usage = Usage(
                prompt_tokens=usage_chunk.prompt_tokens - cache_read,
                completion_tokens=usage_chunk.completion_tokens,
                cache_read_tokens=cache_read,
                cache_write_tokens=0,
            )
        else:
            usage = Usage(prompt_tokens=0, completion_tokens=0, cache_read_tokens=0, cache_write_tokens=0)

        reasoning = "".join(reasoning_parts) if reasoning_parts else None

        message = Message(
            role=Role.ASSISTANT,
            content=content,
            tool_calls=tool_calls,
            reasoning_content=reasoning,
        )

        yield CompletionResponse(
            choices=[Choice(message=message, finish_reason=finish_reason)],
            usage=usage,
            model=model,
        )

    async def _embedding(self, texts: list[str], model: str) -> list[list[float]]:
        response = await self._client.embeddings.create(
            model=model,
            input=texts,
        )
        sorted_data = sorted(response.data, key=lambda x: x.index)
        return [item.embedding for item in sorted_data]

    async def close(self) -> None:
        await self._client.close()

    def _preprocess_messages(self, messages: list[dict]) -> list[dict]:
        # Drop arden-internal keys before sending to the OpenAI API. Most
        # notably `client_id`, which we keep on stored messages so the
        # desktop can match user rows for edit/branch flows but which the
        # provider doesn't recognize.
        result = []
        for msg in messages:
            stripped = {k: v for k, v in msg.items() if k not in _INTERNAL_MESSAGE_KEYS}
            if msg.get("role") == Role.TOOL:
                stripped["content"] = tool_result_content_for_model(stripped.get("content") or "", msg.get("outcome"))
            content = stripped["content"]
            if not isinstance(content, list):
                result.append(stripped)
                continue
            match stripped["role"]:
                case Role.SYSTEM:
                    result.append({**stripped, "content": blocks_to_text(content)})
                case Role.USER:
                    result.append({**stripped, "content": self._convert_user_content(content)})
                case _:
                    result.append(stripped)
        return result

    def _convert_user_content(self, content: list) -> list[dict]:
        result = []
        for block in content:
            match block.get("type"):
                case "text":
                    result.append({"type": "text", "text": block["text"]})
                case "image":
                    result.append(
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{block['media_type']};base64,{block['data']}"},
                        }
                    )
                case "context":
                    result.append({"type": "text", "text": render_context(block)})
        return result

    def _parse_response(self, response, model: str) -> CompletionResponse:
        choice = response.choices[0]
        msg = choice.message

        tool_calls = None
        if msg.tool_calls:
            tool_calls = [
                ToolCall(
                    id=tc.id,
                    type=tc.type,
                    function=FunctionCall(
                        name=tc.function.name,
                        arguments=tc.function.arguments,
                    ),
                )
                for tc in msg.tool_calls
            ]

        details = response.usage.prompt_tokens_details
        cache_read = (details.cached_tokens or 0) if details else 0

        usage = Usage(
            prompt_tokens=response.usage.prompt_tokens - cache_read,
            completion_tokens=response.usage.completion_tokens,
            cache_read_tokens=cache_read,
            cache_write_tokens=0,
        )

        reasoning_content = getattr(msg, "reasoning_content", None)
        if reasoning_content is None:
            extra = getattr(msg, "model_extra", None) or {}
            reasoning_content = extra.get("reasoning_content")

        message = Message(
            role=Role(msg.role),
            content=msg.content,
            tool_calls=tool_calls,
            reasoning_content=reasoning_content,
        )

        return CompletionResponse(
            choices=[Choice(message=message, finish_reason=_map_finish_reason(choice.finish_reason))],
            usage=usage,
            model=model,
        )
