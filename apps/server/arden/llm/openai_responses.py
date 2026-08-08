import warnings
from collections.abc import AsyncGenerator, Mapping
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
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
from arden.agent.types.llm import ProviderToolPayloadError
from arden.agent.types.tools import tool_result_content_for_model
from arden.core.content import ContextContent, ImageContent, TextContent, render_context
from arden.llm.history import (
    AssistantHistoryMessage,
    HistoryContent,
    ModelHistory,
    ReplayProvider,
    SystemHistoryMessage,
    ToolHistoryMessage,
    UserHistoryMessage,
    history_content_to_text,
    unsupported_content_block,
)

REASONING_INCLUDE = ["reasoning.encrypted_content"]

_CLIENT_TOOL_SEARCH_SPEC = {
    "type": "tool_search",
    "execution": "client",
    "description": "Search deferred tool metadata and expose matching tools for the next model call.",
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Exact tool name, select:<names>, or search terms."},
            "limit": {"type": "number", "description": "Maximum matches. Defaults to 5."},
        },
        "required": ["query"],
        "additionalProperties": False,
    },
}


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
    history = ModelHistory.from_raw(messages)
    instructions, input_items = _convert_messages(history)
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
    deferred_names = {
        name for tool in (deferred_tools or []) if (name := _tool_schema_name(tool)) is not None
    }
    discovered_names = _client_tool_names_from_history(input_items)
    api_tools = [
        _convert_tool(tool)
        for tool in (tools or [])
        if not (
            deferred_tools
            and (
                _tool_schema_name(tool) == "tool_search"
                or _tool_schema_name(tool) in deferred_names & discovered_names
            )
        )
    ]
    if deferred_tools:
        api_tools.append(deepcopy(_CLIENT_TOOL_SEARCH_SPEC))
    if api_tools:
        request["tools"] = api_tools
        request["tool_choice"] = _convert_tool_choice(tool_choice)
    if allow_sampling_options:
        if temperature is not None:
            request["temperature"] = temperature
        if max_tokens is not None:
            request["max_output_tokens"] = max_tokens
    if reasoning_effort is not None:
        # `auto` lets the model decide, and current models answer with bare
        # `**Title**` headings whose body is the `<!-- -->` placeholder — a
        # timeline of captions with no reasoning in it. `detailed` is what
        # actually returns prose under each heading.
        request["reasoning"] = {"effort": reasoning_effort, "summary": "detailed"}
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


def _client_tool_names_from_history(items: list[dict[str, Any]]) -> set[str]:
    names: set[str] = set()
    for item in items:
        if item.get("type") != "tool_search_output" or item.get("execution") != "client":
            continue
        names.update(_tool_names(item.get("tools"), path="OpenAI client tool_search_output.tools"))
    return names


async def complete_responses_completion(
    client,
    request: dict[str, Any],
    *,
    model: str,
    deferred_tools: list[dict] | None = None,
) -> AsyncGenerator[str | ReasoningContentDelta | ToolCallStreamDelta | ProviderToolCall | CompletionResponse]:
    with _suppress_pydantic_serializer_warnings():
        response = await client.responses.create(**request)
    parsed = parse_responses_response(response, model, client_tool_catalog=deferred_tools)
    for item in _completion_response_items(parsed):
        yield item


async def buffered_stream_responses_completion(
    client,
    request: dict[str, Any],
    *,
    model: str,
    deferred_tools: list[dict] | None = None,
) -> AsyncGenerator[str | ReasoningContentDelta | ToolCallStreamDelta | ProviderToolCall | CompletionResponse]:
    request = {**request, "stream": True}
    attempts = 2

    for attempt in range(1, attempts + 1):
        try:
            parsed = await _collect_streamed_responses_completion(
                client, request, model=model, deferred_tools=deferred_tools
            )
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
    deferred_tools: list[dict] | None = None,
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

    yield _resolve_client_tool_searches(collector.to_completion(model), deferred_tools)


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
    deferred_tools: list[dict] | None = None,
) -> CompletionResponse:
    with _suppress_pydantic_serializer_warnings():
        stream = await client.responses.create(**request)
    collector = _ResponsesStreamCollector()

    async for event in stream:
        collector.consume(event, emit_deltas=False)
        if collector.done:
            break

    return _resolve_client_tool_searches(collector.to_completion(model), deferred_tools)


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

        # A reasoning summary is a LIST of parts, each shaped `**Title**\n\nbody`.
        # Their deltas carry no separator, so without a boundary here part N+1's
        # `**Title**` lands mid-line at the end of part N's body — no longer a
        # heading, just inline bold, and the whole summary collapses to one step.
        if event_type in ("response.reasoning_summary_part.added", "response.reasoning_text.done"):
            return self._consume_reasoning_boundary(emit_deltas=emit_deltas)

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

    def _consume_reasoning_boundary(self, *, emit_deltas: bool) -> list[ReasoningContentDelta]:
        """Blank line between summary parts, emitted as a delta so the live
        stream and the collected fallback stay byte-identical."""
        if not self.reasoning_parts:
            return []
        if "".join(self.reasoning_parts).endswith("\n\n"):
            return []
        self.reasoning_parts.append("\n\n")
        return [ReasoningContentDelta("\n\n")] if emit_deltas else []


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
            openai_response_items=msg.openai_response_items,
        ),
        finish_reason=parsed.choices[0].finish_reason,
    )
    return parsed


def parse_responses_response(
    response,
    model: str,
    output_items: list[dict[str, Any]] | None = None,
    *,
    client_tool_catalog: list[dict] | None = None,
) -> CompletionResponse:
    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    reasoning_encrypted_content = None
    tool_calls: list[ToolCall] = []
    provider_tool_calls: list[ProviderToolCall] = []
    tool_search_call_ids: list[str | None] = []
    tool_search_executions: list[object] = []
    tool_search_outputs: list[tuple[str | None, list[str]]] = []

    source_items = output_items if output_items is not None else response.output
    response_items = [deepcopy(item if isinstance(item, dict) else _model_dump(item)) for item in source_items]

    for data in response_items:
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
            # Per ITEM, not across items: `reasoning_parts` accumulates every
            # reasoning item in the response, so testing it here made one item's
            # `reasoning_text` suppress every later item's summary entirely.
            item_parts = [
                part.get("text", "") for part in data.get("content") or [] if part.get("type") == "reasoning_text"
            ]
            if not item_parts:
                item_parts = [
                    part.get("text", "") for part in data.get("summary") or [] if part.get("type") == "summary_text"
                ]
            reasoning_parts.extend(part for part in item_parts if part)
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
            tool_search_call_ids.append(_optional_tool_search_call_id(data, path="OpenAI tool_search_call"))
            tool_search_executions.append(data.get("execution"))
            continue

        if item_type == "tool_search_output":
            if len(tool_search_outputs) >= len(provider_tool_calls):
                raise ProviderToolPayloadError("OpenAI tool_search_output has no preceding unmatched tool_search_call")
            tool_search_outputs.append(
                (
                    _optional_tool_search_call_id(data, path="OpenAI tool_search_output"),
                    _tool_search_output_names(data),
                )
            )

    if provider_tool_calls:
        if tool_search_outputs:
            provider_tool_calls = _pair_tool_search_outputs(
                provider_tool_calls,
                tool_search_call_ids,
                tool_search_outputs,
            )
        elif client_tool_catalog is None and any(execution != "client" for execution in tool_search_executions):
            raise ProviderToolPayloadError("OpenAI response contains tool_search_call without tool_search_output")

    message = Message(
        role=Role.ASSISTANT,
        content="".join(content_parts) or None,
        tool_calls=tool_calls or None,
        # Blank line between parts: each is `**Title**\n\nbody`, and gluing them
        # buries every title after the first mid-line, where it reads as inline
        # bold instead of starting a new step.
        reasoning_content="\n\n".join(reasoning_parts) or None,
        reasoning_encrypted_content=reasoning_encrypted_content,
        provider_tool_calls=provider_tool_calls or None,
        openai_response_items=response_items or None,
    )
    parsed = CompletionResponse(
        choices=[Choice(message=message, finish_reason=_finish_reason(response, tool_calls))],
        usage=_usage(response),
        model=model,
    )
    return _resolve_client_tool_searches(parsed, client_tool_catalog)


def _parse_tool_search_call(data: dict[str, Any], *, done: bool = True) -> ProviderToolCall:
    call_id = _tool_search_call_id(data)
    expected_status = "completed" if done else "in_progress"
    status = data.get("status")
    if status != expected_status:
        raise ProviderToolPayloadError(f"OpenAI tool_search_call.status must be {expected_status!r}, got {status!r}")
    return ProviderToolCall(
        id=call_id,
        name="tool_search",
        arguments=_tool_search_arguments(data, required=done),
        result="",
        done=done,
    )


def _with_tool_search_matches(
    call: ProviderToolCall,
    loaded_tool_names: list[str],
) -> ProviderToolCall:
    names = list(dict.fromkeys(loaded_tool_names))
    result = f"Matched tools: {', '.join(names)}" if names else "No matching tools found."
    return ProviderToolCall(
        id=call.id,
        name=call.name,
        arguments={**call.arguments_dict(), "tools": names},
        result=call.result or result,
        done=True,
        loaded_tool_names=tuple(names),
    )


def _pair_tool_search_outputs(
    calls: list[ProviderToolCall],
    call_ids: list[str | None],
    outputs: list[tuple[str | None, list[str]]],
) -> list[ProviderToolCall]:
    if len(calls) != len(outputs):
        raise ProviderToolPayloadError("OpenAI response must contain one tool_search_output for each tool_search_call")
    if len(calls) == 1:
        call_id = call_ids[0]
        output_call_id, names = outputs[0]
        if call_id is not None and output_call_id is not None and call_id != output_call_id:
            raise ProviderToolPayloadError("OpenAI tool_search_output.call_id does not match tool_search_call.call_id")
        return [_with_tool_search_matches(calls[0], names)]

    output_call_ids = [call_id for call_id, _names in outputs]
    if all(call_id is None for call_id in call_ids) and all(call_id is None for call_id in output_call_ids):
        return [
            _with_tool_search_matches(call, names)
            for call, (_output_call_id, names) in zip(calls, outputs, strict=True)
        ]
    if any(call_id is None for call_id in call_ids) or any(call_id is None for call_id in output_call_ids):
        raise ProviderToolPayloadError(
            "OpenAI multiple tool searches must either omit call_id from every call and output or provide every call_id"
        )
    concrete_call_ids = [call_id for call_id in call_ids if call_id is not None]
    if len(set(concrete_call_ids)) != len(concrete_call_ids):
        raise ProviderToolPayloadError("OpenAI returned duplicate tool_search_call.call_id values")
    names_by_call_id = {call_id: names for call_id, names in outputs if call_id is not None}
    if len(names_by_call_id) != len(outputs):
        raise ProviderToolPayloadError("OpenAI returned duplicate tool_search_output.call_id values")
    if set(names_by_call_id) != set(concrete_call_ids):
        raise ProviderToolPayloadError("OpenAI tool search call/output call_id values do not match")
    return [
        _with_tool_search_matches(call, names_by_call_id[call_id])
        for call, call_id in zip(calls, concrete_call_ids, strict=True)
    ]


def _resolve_client_tool_searches(
    response: CompletionResponse,
    catalog: list[dict] | None,
) -> CompletionResponse:
    if catalog is None or not response.choices:
        return response
    choice = response.choices[0]
    message = choice.message
    calls = message.provider_tool_calls or []
    if not calls or all(call.loaded_tool_names for call in calls):
        return response
    items = deepcopy(message.openai_response_items or [])
    call_by_id = {call.id: call for call in calls}
    completed_client_call_ids = {
        item.get("call_id")
        for item in items
        if item.get("type") == "tool_search_output"
        and item.get("execution") == "client"
        and isinstance(item.get("call_id"), str)
    }
    resolved: dict[str, ProviderToolCall] = {}
    projected: list[dict] = []
    for item in items:
        projected.append(item)
        if item.get("type") != "tool_search_call":
            continue
        item_id = item.get("id")
        call = call_by_id.get(item_id)
        if call is None or call.loaded_tool_names:
            continue
        if item.get("execution") != "client":
            raise ProviderToolPayloadError("OpenAI client tool_search_call.execution must be 'client'")
        call_id = item.get("call_id")
        if not isinstance(call_id, str) or not call_id.strip():
            raise ProviderToolPayloadError("OpenAI client tool_search_call.call_id must be a non-empty string")
        if call_id in completed_client_call_ids:
            resolved[call.id] = call
            continue
        matches = _search_client_tool_catalog(call.arguments_dict(), catalog)
        names = [_tool_schema_name(schema) for schema in matches]
        loaded_names = [name for name in names if name is not None]
        resolved[call.id] = _with_tool_search_matches(call, loaded_names)
        projected.append(
            {
                "type": "tool_search_output",
                "call_id": call_id,
                "status": "completed",
                "execution": "client",
                "tools": [_convert_tool(schema, defer_loading=True) for schema in matches],
            }
        )
    unresolved = [call.id for call in calls if not call.loaded_tool_names and call.id not in resolved]
    if unresolved:
        raise ProviderToolPayloadError(f"OpenAI client tool search calls missing from response items: {unresolved}")
    provider_calls = [resolved.get(call.id, call) for call in calls]
    updated_message = replace(
        message,
        provider_tool_calls=provider_calls,
        openai_response_items=projected,
    )
    choices = list(response.choices)
    choices[0] = replace(choice, message=updated_message)
    return replace(response, choices=choices)


def _search_client_tool_catalog(arguments: dict[str, Any], catalog: list[dict]) -> list[dict]:
    query = arguments.get("query")
    if not isinstance(query, str) or not query.strip():
        raise ProviderToolPayloadError("OpenAI client tool_search query must be a non-empty string")
    raw_limit = arguments.get("limit", 5)
    if not isinstance(raw_limit, int) or isinstance(raw_limit, bool) or raw_limit <= 0:
        raise ProviderToolPayloadError("OpenAI client tool_search limit must be a positive integer")
    limit = min(raw_limit, 20)
    schemas_by_name = {
        name: schema
        for schema in catalog
        if (name := _tool_schema_name(schema)) is not None
    }
    normalized = query.strip().lower()
    if normalized.startswith("select:"):
        requested = [name.strip() for name in query.split(":", 1)[1].split(",") if name.strip()]
        return [schemas_by_name[name] for name in requested if name in schemas_by_name][:limit]
    for name, schema in schemas_by_name.items():
        if name.lower() == normalized:
            return [schema]
    tokens = [token for token in normalized.replace("_", " ").replace("-", " ").split() if token]
    scored: list[tuple[int, str]] = []
    for name, schema in schemas_by_name.items():
        fn = schema.get("function", schema)
        description = " ".join(str(fn.get("description") or "").lower().split())
        name_text = name.lower().replace("_", " ").replace("-", " ")
        score = sum(
            10 if token in name_text.split() else 5 if token in name_text else 2 if token in description else 0
            for token in tokens
        )
        if score:
            scored.append((score, name))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [schemas_by_name[name] for _score, name in scored[:limit]]


def _tool_search_output_names(data: dict[str, Any]) -> list[str]:
    if data.get("status") != "completed":
        raise ProviderToolPayloadError("OpenAI tool_search_output.status must be 'completed'")
    if "tools" not in data:
        raise ProviderToolPayloadError("OpenAI tool_search_output is missing tools")
    return _tool_names(data["tools"], path="OpenAI tool_search_output.tools")


def _tool_names(value: Any, *, path: str) -> list[str]:
    if not isinstance(value, list):
        raise ProviderToolPayloadError(f"{path} must be an array")
    names: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise ProviderToolPayloadError(f"{path}[{index}] must be an object")
        if item.get("type") == "namespace":
            if "tools" not in item:
                raise ProviderToolPayloadError(f"{path}[{index}] namespace is missing tools")
            names.extend(_tool_names(item["tools"], path=f"{path}[{index}].tools"))
            continue
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ProviderToolPayloadError(f"{path}[{index}].name must be a non-empty string")
        names.append(name)
    return list(dict.fromkeys(names))


def _tool_search_call_id(data: Mapping[str, Any]) -> str:
    value = data.get("id")
    if not isinstance(value, str) or not value.strip():
        raise ProviderToolPayloadError("OpenAI tool_search_call.id must be a non-empty string")
    return value


def _optional_tool_search_call_id(data: Mapping[str, Any], *, path: str) -> str | None:
    value = data.get("call_id")
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ProviderToolPayloadError(f"{path}.call_id must be a non-empty string")
    return value


def _tool_search_arguments(data: Mapping[str, Any], *, required: bool) -> dict[str, Any]:
    if "arguments" not in data:
        if required:
            raise ProviderToolPayloadError("OpenAI completed tool_search_call is missing arguments")
        return {}
    arguments = data["arguments"]
    if not isinstance(arguments, Mapping):
        raise ProviderToolPayloadError("OpenAI tool_search_call.arguments must be an object")
    return dict(arguments)


def _convert_messages(history: ModelHistory) -> tuple[str | None, list[dict[str, Any]]]:
    instructions: list[str] = []
    input_items: list[dict[str, Any]] = []

    for message in history.messages:
        match message:
            case SystemHistoryMessage():
                instructions.append(history_content_to_text(message.content))
            case UserHistoryMessage():
                input_items.append({"role": "user", "content": _convert_user_content(message.content)})
            case AssistantHistoryMessage():
                input_items.extend(_convert_assistant_message(message))
            case ToolHistoryMessage():
                input_items.append(
                    {
                        "type": "function_call_output",
                        "call_id": message.tool_call_id,
                        "output": tool_result_content_for_model(message.content_text, message.outcome),
                    }
                )

    return ("\n\n".join(part for part in instructions if part) or None), input_items


def _convert_user_content(content: HistoryContent) -> str | list[dict[str, Any]]:
    if isinstance(content, str):
        return content
    result: list[dict[str, Any]] = []
    for block in content:
        match block:
            case TextContent():
                result.append({"type": "input_text", "text": block.text})
            case ImageContent():
                result.append(
                    {
                        "type": "input_image",
                        "detail": "auto",
                        "image_url": f"data:{block.media_type};base64,{block.data}",
                    }
                )
            case ContextContent():
                result.append({"type": "input_text", "text": render_context(block)})
            case _:
                raise unsupported_content_block("OpenAI Responses", block)
    return result


def _convert_assistant_message(message: AssistantHistoryMessage) -> list[dict[str, Any]]:
    if replay := message.replay_for(ReplayProvider.OPENAI_RESPONSES):
        return replay.materialize()

    items: list[dict[str, Any]] = []
    if encrypted := message.reasoning_encrypted_content:
        items.append({"type": "reasoning", "encrypted_content": encrypted, "summary": []})
    if content := history_content_to_text(message.content):
        items.append({"role": "assistant", "content": content})
    for tool_call in message.tool_calls:
        items.append(
            {
                "type": "function_call",
                "call_id": tool_call.id,
                "name": tool_call.function.name,
                "arguments": tool_call.function.arguments,
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
