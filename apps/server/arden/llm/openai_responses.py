import json
import warnings
from collections.abc import AsyncGenerator
from contextlib import contextmanager
from typing import Any

import httpx
import openai
from pydantic import BaseModel

from arden.agent import (
    Choice,
    CompletionResponse,
    FinishReason,
    FunctionCall,
    Message,
    ProviderToolCall,
    ReasoningContentDelta,
    Role,
    ToolCall,
    ToolCallStreamDelta,
    Usage,
)
from arden.agent.types.tools import tool_result_content_for_model
from arden.core.content import render_context
from arden.llm.utils import blocks_to_text

REASONING_INCLUDE = ["reasoning.encrypted_content"]


@contextmanager
def _suppress_pydantic_serializer_warnings():
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="Pydantic serializer warnings:*", category=UserWarning)
        yield


def _model_dump(obj, *, exclude_none: bool = True) -> dict[str, Any]:
    with _suppress_pydantic_serializer_warnings():
        return obj.model_dump(exclude_none=exclude_none)


class OpenAIResponseStreamError(RuntimeError):
    def __init__(self, message: str, *, error: dict[str, Any] | None = None):
        super().__init__(message)
        self.message = message
        self.body = {"error": error} if isinstance(error, dict) else None
        self.code = error.get("code") if isinstance(error, dict) else None
        self.type = error.get("type") if isinstance(error, dict) else None


def prepare_responses_request(
    *,
    messages: list[dict],
    model: str,
    tools: list[dict] | None,
    tool_choice: str | dict | None,
    temperature: float | None,
    max_tokens: int | None,
    reasoning_effort: str | None,
    response_format: type[BaseModel] | None,
    allow_sampling_options: bool,
    deferred_tools: list[dict] | None = None,
    include_reasoning: bool = True,
    store: bool | None = False,
    **kwargs,
) -> dict[str, Any]:
    instructions, input_items = _convert_messages(messages)
    request: dict[str, Any] = {
        "model": model,
        "input": input_items,
    }
    if include_reasoning:
        request["include"] = REASONING_INCLUDE
    if store is not None:
        request["store"] = store
    if instructions:
        request["instructions"] = instructions
    api_tools = [
        _convert_tool(tool)
        for tool in (tools or [])
        if not (deferred_tools and _tool_schema_name(tool) == "tool_search")
    ]
    if deferred_tools:
        api_tools.extend(_convert_tool(tool, defer_loading=True) for tool in deferred_tools)
        api_tools.append({"type": "tool_search"})
    if api_tools:
        request["tools"] = api_tools
        request["tool_choice"] = _convert_tool_choice(tool_choice)
    if allow_sampling_options:
        if temperature is not None:
            request["temperature"] = temperature
        if max_tokens is not None:
            request["max_output_tokens"] = max_tokens
    if reasoning_effort is not None:
        request["reasoning"] = {"effort": reasoning_effort, "summary": "auto"}
    if response_format is not None:
        request["text"] = {
            "format": {
                "type": "json_schema",
                "name": response_format.__name__,
                "schema": response_format.model_json_schema(),
                "strict": False,
            }
        }
    if prompt_cache_key := kwargs.get("prompt_cache_key"):
        request["prompt_cache_key"] = prompt_cache_key
    if extra_body := kwargs.get("extra_body"):
        request["extra_body"] = dict(extra_body)
    return request


async def complete_responses_completion(
    client,
    request: dict[str, Any],
    *,
    model: str,
) -> AsyncGenerator[str | ReasoningContentDelta | ToolCallStreamDelta | ProviderToolCall | CompletionResponse]:
    with _suppress_pydantic_serializer_warnings():
        response = await client.responses.create(**request)
    parsed = parse_responses_response(response, model)
    for item in _completion_response_items(parsed):
        yield item


async def buffered_stream_responses_completion(
    client,
    request: dict[str, Any],
    *,
    model: str,
) -> AsyncGenerator[str | ReasoningContentDelta | ToolCallStreamDelta | ProviderToolCall | CompletionResponse]:
    request = {**request, "stream": True}
    attempts = 2

    for attempt in range(1, attempts + 1):
        try:
            parsed = await _collect_streamed_responses_completion(client, request, model=model)
            for item in _completion_response_items(parsed):
                yield item
            return
        except (httpx.RemoteProtocolError, openai.APIConnectionError) as exc:
            if attempt >= attempts:
                raise RuntimeError("OpenAI response stream disconnected before completion") from exc


async def stream_responses_completion(
    client,
    request: dict[str, Any],
    *,
    model: str,
) -> AsyncGenerator[str | ReasoningContentDelta | ToolCallStreamDelta | ProviderToolCall | CompletionResponse]:
    request = {**request, "stream": True}
    try:
        with _suppress_pydantic_serializer_warnings():
            stream = await client.responses.create(**request)
    except (httpx.RemoteProtocolError, openai.APIConnectionError) as exc:
        raise RuntimeError("OpenAI response stream disconnected before completion") from exc
    collector = _ResponsesStreamCollector()

    try:
        async for event in stream:
            for item in collector.consume(event, emit_deltas=True):
                yield item
            if collector.done:
                break
    except (httpx.RemoteProtocolError, openai.APIConnectionError) as exc:
        raise RuntimeError("OpenAI response stream disconnected before completion") from exc

    yield collector.to_completion(model)


def _completion_response_items(
    response: CompletionResponse,
) -> list[str | ReasoningContentDelta | ToolCallStreamDelta | CompletionResponse]:
    items: list[str | ReasoningContentDelta | ToolCallStreamDelta | CompletionResponse] = []
    if response.choices:
        msg = response.choices[0].message
        if msg.reasoning_content:
            items.append(ReasoningContentDelta(msg.reasoning_content))
        if msg.content:
            items.append(msg.content)
    items.append(response)
    return items


async def _collect_streamed_responses_completion(
    client,
    request: dict[str, Any],
    *,
    model: str,
) -> CompletionResponse:
    with _suppress_pydantic_serializer_warnings():
        stream = await client.responses.create(**request)
    collector = _ResponsesStreamCollector()

    async for event in stream:
        collector.consume(event, emit_deltas=False)
        if collector.done:
            break

    return collector.to_completion(model)


class _ResponsesStreamCollector:
    def __init__(self) -> None:
        self.text_parts: list[str] = []
        self.reasoning_parts: list[str] = []
        self.completed_items: list[dict[str, Any]] = []
        self.final_response = None
        self.done = False

    def consume(
        self, event, *, emit_deltas: bool
    ) -> list[str | ReasoningContentDelta | ToolCallStreamDelta | ProviderToolCall]:
        data = _model_dump(event)
        event_type = data.get("type")

        if event_type == "response.output_text.delta":
            return self._consume_text_delta(data.get("delta"), emit_deltas=emit_deltas)

        if event_type in ("response.output_text.done", "response.content_part.done"):
            return self._consume_text_delta(_missing_done_text_delta(data, self.text_parts), emit_deltas=emit_deltas)

        if event_type in ("response.reasoning_text.delta", "response.reasoning_summary_text.delta"):
            return self._consume_reasoning_delta(data.get("delta"), emit_deltas=emit_deltas)

        if event_type == "response.output_item.added":
            item = data.get("item")
            if isinstance(item, dict) and item.get("type") == "function_call":
                return [
                    ToolCallStreamDelta(
                        index=_event_output_index(data),
                        tool_id=item.get("call_id"),
                        name=item.get("name"),
                        arguments_delta=item.get("arguments") or None,
                    )
                ]
            if isinstance(item, dict) and item.get("type") == "tool_search_call":
                return [_parse_tool_search_call(item, done=False)]
            return []

        if event_type == "response.function_call_arguments.delta":
            delta = data.get("delta")
            return [
                ToolCallStreamDelta(
                    index=_event_output_index(data),
                    arguments_delta=delta if isinstance(delta, str) and delta else None,
                )
            ]

        if event_type == "response.completed":
            self.final_response = event.response
            self.done = True
            return []

        if event_type == "response.output_item.done":
            item = data.get("item")
            if isinstance(item, dict):
                self.completed_items.append(item)
                if item.get("type") == "function_call":
                    return [
                        ToolCallStreamDelta(
                            index=_event_output_index(data),
                            tool_id=item.get("call_id"),
                            name=item.get("name"),
                            done=True,
                        )
                    ]
                if item.get("type") == "tool_search_call":
                    return [_parse_tool_search_call(item, done=True)]
            return []

        if event_type in ("response.failed", "response.incomplete"):
            response = data.get("response")
            error = response.get("error") if isinstance(response, dict) else None
            message = error.get("message") if isinstance(error, dict) else None
            raise OpenAIResponseStreamError(
                message or f"OpenAI response failed: {event_type}",
                error=error if isinstance(error, dict) else None,
            )

        if event_type == "error":
            error = data.get("error")
            if isinstance(error, dict) and isinstance(error.get("message"), str):
                raise OpenAIResponseStreamError(error["message"], error=error)
            raise OpenAIResponseStreamError("OpenAI response stream returned an error")

        return []

    def to_completion(self, model: str) -> CompletionResponse:
        if self.final_response is None:
            raise RuntimeError("OpenAI response stream ended before completion")

        parsed = parse_responses_response(self.final_response, model, self.completed_items or None)
        return _with_streamed_fallbacks(parsed, text_parts=self.text_parts, reasoning_parts=self.reasoning_parts)

    def _consume_text_delta(self, delta: Any, *, emit_deltas: bool) -> list[str]:
        if not isinstance(delta, str) or not delta:
            return []
        self.text_parts.append(delta)
        return [delta] if emit_deltas else []

    def _consume_reasoning_delta(self, delta: Any, *, emit_deltas: bool) -> list[ReasoningContentDelta]:
        if not isinstance(delta, str) or not delta:
            return []
        self.reasoning_parts.append(delta)
        return [ReasoningContentDelta(delta)] if emit_deltas else []


def _missing_done_text_delta(data: dict[str, Any], text_parts: list[str]) -> str | None:
    if data.get("type") == "response.output_text.done":
        text = data.get("text")
    else:
        part = data.get("part")
        text = part.get("text") if isinstance(part, dict) and part.get("type") in ("output_text", "text") else None
    if not isinstance(text, str) or not text:
        return None

    current = "".join(text_parts)
    if text == current:
        return None
    if text.startswith(current):
        return text[len(current) :]
    if not current:
        return text
    return None


def _event_output_index(data: dict[str, Any]) -> int:
    index = data.get("output_index")
    if isinstance(index, int):
        return index
    return 0


def _with_streamed_fallbacks(
    parsed: CompletionResponse,
    *,
    text_parts: list[str],
    reasoning_parts: list[str],
) -> CompletionResponse:
    if not parsed.choices:
        return parsed

    msg = parsed.choices[0].message
    content = msg.content or ("".join(text_parts) or None)
    reasoning_content = msg.reasoning_content or ("".join(reasoning_parts) or None)
    if content == msg.content and reasoning_content == msg.reasoning_content:
        return parsed

    parsed.choices[0] = Choice(
        message=Message(
            role=msg.role,
            content=content,
            tool_calls=msg.tool_calls,
            reasoning_content=reasoning_content,
            reasoning_encrypted_content=msg.reasoning_encrypted_content,
            provider_tool_calls=msg.provider_tool_calls,
        ),
        finish_reason=parsed.choices[0].finish_reason,
    )
    return parsed


def parse_responses_response(
    response,
    model: str,
    output_items: list[dict[str, Any]] | None = None,
) -> CompletionResponse:
    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    reasoning_encrypted_content = None
    tool_calls: list[ToolCall] = []
    provider_tool_calls: list[ProviderToolCall] = []

    for item in output_items if output_items is not None else response.output:
        data = item if isinstance(item, dict) else _model_dump(item)
        item_type = data.get("type")
        if item_type == "message":
            for part in data.get("content", []):
                if part.get("type") == "output_text":
                    content_parts.append(part.get("text", ""))
                elif part.get("type") == "refusal":
                    content_parts.append(part.get("refusal", ""))
            continue

        if item_type == "reasoning":
            if encrypted := data.get("encrypted_content"):
                reasoning_encrypted_content = encrypted
            for part in data.get("content") or []:
                if part.get("type") == "reasoning_text":
                    reasoning_parts.append(part.get("text", ""))
            if not reasoning_parts:
                for part in data.get("summary") or []:
                    if part.get("type") == "summary_text":
                        reasoning_parts.append(part.get("text", ""))
            continue

        if item_type == "function_call":
            call_id = data["call_id"]
            tool_calls.append(
                ToolCall(
                    id=call_id,
                    type="function",
                    function=FunctionCall(name=data["name"], arguments=data.get("arguments", "{}")),
                )
            )
            continue

        if item_type == "tool_search_call":
            provider_tool_calls.append(_parse_tool_search_call(data))

    if provider_tool_calls:
        provider_tool_calls = _with_tool_search_matches(provider_tool_calls, tool_calls)

    message = Message(
        role=Role.ASSISTANT,
        content="".join(content_parts) or None,
        tool_calls=tool_calls or None,
        reasoning_content="".join(reasoning_parts) or None,
        reasoning_encrypted_content=reasoning_encrypted_content,
        provider_tool_calls=provider_tool_calls or None,
    )
    return CompletionResponse(
        choices=[Choice(message=message, finish_reason=_finish_reason(response, tool_calls))],
        usage=_usage(response),
        model=model,
    )


def _parse_tool_search_call(data: dict[str, Any], *, done: bool = True) -> ProviderToolCall:
    call_id = data.get("id") or data.get("call_id") or data.get("item_id") or "tool_search"
    query = data.get("query") or data.get("queries") or data.get("input") or {}
    result = data.get("results") or data.get("output") or data.get("tools") or data.get("status") or ""
    if result == "completed":
        result = ""
    provider_item = dict(data)
    provider_item["type"] = "tool_search_call"
    provider_item["id"] = str(call_id)
    if done:
        provider_item["status"] = provider_item.get("status") or "completed"
    return ProviderToolCall(
        id=str(call_id),
        name="tool_search",
        arguments=json.dumps({"query": query}, default=str),
        result=_provider_tool_result_text(result),
        done=done,
        provider_item=provider_item,
    )


def _with_tool_search_matches(calls: list[ProviderToolCall], tool_calls: list[ToolCall]) -> list[ProviderToolCall]:
    names = [tc.function.name for tc in tool_calls]
    if not names:
        return calls
    result = f"Matched tools: {', '.join(names)}"
    arguments = json.dumps({"tools": names})
    return [
        ProviderToolCall(
            id=call.id,
            name=call.name,
            arguments=arguments if call.arguments in ('{"query": {}}', '{"query": ""}') else call.arguments,
            result=call.result or result,
            done=True,
            provider_item=call.provider_item,
        )
        for call in calls
    ]


def _provider_tool_result_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, default=str)


def _provider_tool_arguments_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value:
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _convert_messages(messages: list[dict]) -> tuple[str | None, list[dict[str, Any]]]:
    instructions: list[str] = []
    input_items: list[dict[str, Any]] = []

    for msg in messages:
        role = msg["role"]
        content = msg.get("content") or ""

        if role == Role.SYSTEM or role == "system":
            instructions.append(_content_to_text(content))
            continue

        if role == Role.USER or role == "user":
            input_items.append({"role": "user", "content": _convert_user_content(content)})
            continue

        if role == Role.ASSISTANT or role == "assistant":
            input_items.extend(_convert_assistant_message(msg))
            continue

        if role == Role.TOOL or role == "tool":
            input_items.append(
                {
                    "type": "function_call_output",
                    "call_id": msg["tool_call_id"],
                    "output": tool_result_content_for_model(_content_to_text(content), msg.get("outcome")),
                }
            )

    return ("\n\n".join(part for part in instructions if part) or None), input_items


def _content_to_text(content: str | list) -> str:
    if isinstance(content, str):
        return content
    return blocks_to_text(content)


def _convert_user_content(content: str | list) -> str | list[dict[str, Any]]:
    if isinstance(content, str):
        return content
    result: list[dict[str, Any]] = []
    for block in content:
        match block.get("type"):
            case "text":
                result.append({"type": "input_text", "text": block["text"]})
            case "image":
                result.append(
                    {
                        "type": "input_image",
                        "detail": "auto",
                        "image_url": f"data:{block['media_type']};base64,{block['data']}",
                    }
                )
            case "context":
                result.append({"type": "input_text", "text": render_context(block)})
    return result


def _convert_assistant_message(msg: dict) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if encrypted := msg.get("reasoning_encrypted_content"):
        items.append({"type": "reasoning", "encrypted_content": encrypted, "summary": []})
    if content := msg.get("content"):
        items.append({"role": "assistant", "content": _content_to_text(content)})
    for call in msg.get("provider_tool_calls") or []:
        if call.get("name") == "tool_search":
            provider_item = call.get("provider_item") or {}
            items.append(
                {
                    "type": "tool_search_call",
                    "id": provider_item.get("id") or call["id"],
                    "status": provider_item.get("status") or "completed",
                    "arguments": _provider_tool_arguments_object(
                        provider_item.get("arguments") or call.get("arguments")
                    ),
                }
            )
    for tc in msg.get("tool_calls", []):
        fn = tc["function"]
        items.append(
            {
                "type": "function_call",
                "call_id": tc["id"],
                "name": fn["name"],
                "arguments": fn.get("arguments", "{}"),
                "status": "completed",
            }
        )
    return items


def _convert_tool(tool: dict, *, defer_loading: bool = False) -> dict[str, Any]:
    fn = tool.get("function", tool)
    result = {
        "type": "function",
        "name": fn["name"],
        "description": fn.get("description", ""),
        "parameters": fn.get("parameters", {"type": "object", "properties": {}}),
        "strict": False,
    }
    if defer_loading:
        result["defer_loading"] = True
    return result


def _tool_schema_name(tool: dict) -> str | None:
    fn = tool.get("function", tool)
    name = fn.get("name") if isinstance(fn, dict) else None
    return name if isinstance(name, str) else None


def _convert_tool_choice(tool_choice: str | dict | None) -> str | dict[str, str]:
    if tool_choice in (None, "auto"):
        return "auto"
    if tool_choice in ("none", "required"):
        return tool_choice
    if isinstance(tool_choice, dict):
        fn = tool_choice.get("function")
        if isinstance(fn, dict) and isinstance(fn.get("name"), str):
            return {"type": "function", "name": fn["name"]}
    return "auto"


def _finish_reason(response, tool_calls: list[ToolCall]) -> FinishReason:
    if tool_calls:
        return FinishReason.TOOL_CALLS
    if response.status == "incomplete":
        return FinishReason.LENGTH
    return FinishReason.STOP


def _usage(response) -> Usage:
    if response.usage is None:
        return Usage()
    usage = response.usage
    cache_read = usage.input_tokens_details.cached_tokens if usage.input_tokens_details else 0
    return Usage(
        prompt_tokens=usage.input_tokens - cache_read,
        completion_tokens=usage.output_tokens,
        cache_read_tokens=cache_read,
        cache_write_tokens=0,
    )
