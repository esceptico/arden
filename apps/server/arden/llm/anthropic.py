import json
from collections.abc import AsyncGenerator

import anthropic
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
from arden.llm.base import CompletionClient
from arden.llm.models import get_model
from arden.llm.utils import blocks_to_text, parse_args
from arden.observability.judgment import trace_client

_FINISH_REASONS: dict[str, FinishReason] = {
    "end_turn": FinishReason.STOP,
    "tool_use": FinishReason.TOOL_CALLS,
    "max_tokens": FinishReason.LENGTH,
    "stop_sequence": FinishReason.STOP,
}

_THINKING_BUDGETS: dict[str, int] = {
    "minimal": 1024,
    "low": 4096,
    "medium": 8192,
    "high": 16384,
    "max": 32768,
}

_ADAPTIVE_THINKING_MODELS = (
    "claude-opus-4-7",
    "claude-opus-4-6",
    "claude-sonnet-4-6",
)


class AnthropicClient(CompletionClient):
    def __init__(self, api_key: str | None = None, timeout: float = 60.0):
        self._client = trace_client(anthropic.AsyncAnthropic(api_key=api_key, timeout=timeout))

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
        deferred_tools: list[dict] | None = None,
        **kwargs,
    ) -> tuple[str, dict]:
        if max_tokens is None:
            max_tokens = get_model(model).max_output_tokens

        system, api_messages = self._split_system(messages)
        api_messages = self._convert_messages(api_messages)
        api_tools = self._convert_tools(tools, deferred_tools) if tools or deferred_tools else None
        api_tool_choice = self._resolve_tool_choice(api_tools, tool_choice, response_format)

        if response_format is not None:
            api_tools = api_tools or []
            api_tools.append(self._make_schema_tool(response_format))

        self._inject_cache_control_last_tool(api_tools)
        self._inject_cache_control_last_message(api_messages)

        request = self._build_request(
            model=model,
            messages=api_messages,
            system=system,
            tools=api_tools,
            tool_choice=api_tool_choice,
            temperature=temperature,
            max_tokens=max_tokens,
            reasoning_effort=reasoning_effort,
            **kwargs,
        )
        return model, request

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
        model, request = self._prepare(
            messages,
            model,
            tools,
            tool_choice,
            temperature,
            max_tokens,
            reasoning_effort,
            response_format,
            deferred_tools,
            **kwargs,
        )
        async with self._client.messages.stream(**request) as stream:
            response = await stream.get_final_message()
        return self._parse_response(response, model, response_format)

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
        model, request = self._prepare(
            messages,
            model,
            tools,
            tool_choice,
            temperature,
            max_tokens,
            reasoning_effort,
            response_format,
            deferred_tools,
            **kwargs,
        )
        async with self._client.messages.stream(**request) as stream:
            provider_blocks: dict[int, dict] = {}
            async for event in stream:
                if event.type == "content_block_start":
                    block = self._block_to_dict(event.content_block)
                    provider_blocks[event.index] = block
                    if block.get("type") == "server_tool_use" and block.get("name") == "tool_search_tool_bm25":
                        yield ProviderToolCall(
                            id=str(block.get("id") or f"tool_search_{event.index}"),
                            name="tool_search",
                            arguments=json.dumps(block.get("input") or {}),
                            done=False,
                        )
                    continue
                if event.type == "content_block_delta":
                    block = provider_blocks.get(event.index)
                    if block and (delta := self._block_to_dict(event.delta)).get("type") == "input_json_delta":
                        block["input"] = str(block.get("input") or "") + str(delta.get("partial_json") or "")
                    delta = event.delta
                    if delta.type == "text_delta":
                        yield delta.text
                    elif delta.type == "thinking_delta":
                        yield ReasoningContentDelta(delta.thinking)
                    continue
                if event.type == "content_block_stop":
                    block = provider_blocks.get(event.index)
                    if block and block.get("type") == "tool_search_tool_result":
                        yield ProviderToolCall(
                            id=str(block.get("tool_use_id") or f"tool_search_{event.index}"),
                            name="tool_search",
                            arguments=json.dumps({"tools": _tool_reference_names(block.get("content"))}),
                            result=_tool_search_result_text(block.get("content")),
                            done=True,
                        )
                    continue
            response = await stream.get_final_message()
        yield self._parse_response(response, model, response_format)

    async def close(self) -> None:
        await self._client.close()

    # --- Request building ---

    def _resolve_tool_choice(
        self,
        tools: list[dict] | None,
        tool_choice: str | None,
        response_format: type[BaseModel] | None,
    ) -> dict | None:
        if response_format is not None:
            return {"type": "tool", "name": "_structured_output"}
        if tool_choice == "auto" and tools:
            return {"type": "auto"}
        return None

    def _build_request(
        self,
        *,
        model: str,
        messages: list[dict],
        system: list[dict] | None,
        tools: list[dict] | None,
        tool_choice: dict | None,
        temperature: float | None,
        max_tokens: int | None,
        reasoning_effort: str | None,
        **kwargs,
    ) -> dict:
        request: dict = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
        }
        optional = {
            "system": system,
            "tools": tools,
            "tool_choice": tool_choice,
            "thinking": self._thinking_config(model, reasoning_effort, max_tokens),
            "output_config": self._output_config(model, reasoning_effort),
        }
        if temperature is not None and "opus-4-7" not in model:
            optional["temperature"] = temperature
        request.update({k: v for k, v in optional.items() if v is not None})
        if extra := kwargs.get("extra_body"):
            request.update(extra)
        return request

    def _thinking_config(self, model: str, effort: str | None, max_tokens: int | None) -> dict | None:
        if effort is None or max_tokens is None:
            return None
        if self._uses_adaptive_thinking(model):
            config = {"type": "adaptive"}
            if "claude-opus-4-7" in model:
                config["display"] = "summarized"
            return config
        budget_tokens = _THINKING_BUDGETS.get(effort)
        if budget_tokens is None:
            return None
        budget = min(budget_tokens, max_tokens - 1024)
        if budget < 1024:
            return None
        return {"type": "enabled", "budget_tokens": budget}

    def _output_config(self, model: str, effort: str | None) -> dict | None:
        if effort is None or not self._uses_adaptive_thinking(model):
            return None
        return {"effort": effort}

    def _uses_adaptive_thinking(self, model: str) -> bool:
        return any(model_id in model for model_id in _ADAPTIVE_THINKING_MODELS)

    # --- Message conversion ---

    def _split_system(self, messages: list[dict]) -> tuple[list[dict] | None, list[dict]]:
        if not messages or messages[0].get("role") != Role.SYSTEM:
            return None, messages

        content = messages[0]["content"]
        system = content if isinstance(content, list) else [{"type": "text", "text": blocks_to_text(content)}]

        return system, messages[1:]

    def _convert_messages(self, messages: list[dict]) -> list[dict]:
        result: list[dict] = []
        for msg in messages:
            match msg["role"]:
                case Role.ASSISTANT:
                    result.append(self._convert_assistant(msg))
                case Role.TOOL:
                    self._append_tool_result(result, msg)
                case Role.USER:
                    result.append({"role": Role.USER, "content": self._convert_user_content(msg["content"])})
        return result

    def _convert_user_content(self, content: str | list) -> str | list[dict]:
        if isinstance(content, str):
            return content
        result = []
        for block in content:
            match block.get("type"):
                case "text":
                    result.append({"type": "text", "text": block["text"]})
                case "image":
                    result.append(
                        {
                            "type": "image",
                            "source": {"type": "base64", "media_type": block["media_type"], "data": block["data"]},
                        }
                    )
                case "context":
                    result.append({"type": "text", "text": render_context(block)})
        return result

    def _convert_assistant(self, msg: dict) -> dict:
        if content := msg.get("anthropic_content"):
            # Replay COPIES with any stale cache_control stripped: anchors are
            # per-request (the API allows 4 total), and mutating the stored
            # blocks would persist an anchor into session history forever.
            return {
                "role": Role.ASSISTANT,
                "content": [
                    {k: v for k, v in block.items() if k != "cache_control"} if isinstance(block, dict) else block
                    for block in content
                ],
            }

        content_blocks: list[dict] = []
        if text := msg["content"]:
            content_blocks.append({"type": "text", "text": text})

        for tc in msg.get("tool_calls", []):
            fn = tc["function"]
            content_blocks.append(
                {
                    "type": "tool_use",
                    "id": tc["id"],
                    "name": fn["name"],
                    "input": parse_args(fn.get("arguments", "{}")),
                }
            )

        return {"role": Role.ASSISTANT, "content": content_blocks or ""}

    def _append_tool_result(self, result: list[dict], msg: dict) -> None:
        block = {
            "type": "tool_result",
            "tool_use_id": msg["tool_call_id"],
            "content": tool_result_content_for_model(msg["content"], msg.get("outcome")),
        }
        # Merge consecutive tool results into one user message
        if result and result[-1]["role"] == Role.USER and isinstance(result[-1]["content"], list):
            last_types = {b.get("type") for b in result[-1]["content"] if isinstance(b, dict)}
            if "tool_result" in last_types:
                result[-1]["content"].append(block)
                return
        result.append({"role": Role.USER, "content": [block]})

    def _convert_tools(self, tools: list[dict] | None, deferred_tools: list[dict] | None = None) -> list[dict]:
        result = [
            {
                "name": (fn := tool.get("function", tool))["name"],
                "description": fn.get("description", ""),
                "input_schema": fn.get("parameters", {"type": "object", "properties": {}}),
            }
            for tool in (tools or [])
            if not (deferred_tools and (tool.get("function", tool)).get("name") == "tool_search")
        ]
        if deferred_tools:
            result.insert(0, {"type": "tool_search_tool_bm25_20251119", "name": "tool_search_tool_bm25"})
            result.extend(
                {
                    "name": (fn := tool.get("function", tool))["name"],
                    "description": fn.get("description", ""),
                    "input_schema": fn.get("parameters", {"type": "object", "properties": {}}),
                    "defer_loading": True,
                }
                for tool in deferred_tools
            )
        return result

    def _make_schema_tool(self, model_class: type[BaseModel]) -> dict:
        return {
            "name": "_structured_output",
            "description": f"Return structured output as {model_class.__name__}",
            "input_schema": model_class.model_json_schema(),
        }

    def _inject_cache_control_last_message(self, messages: list[dict]) -> None:
        if not messages:
            return
        last = messages[-1]
        content = last.get("content")
        if isinstance(content, str):
            if content:
                last["content"] = [{"type": "text", "text": content, "cache_control": {"type": "ephemeral"}}]
        elif isinstance(content, list) and content:
            for target in reversed(content):
                btype = target.get("type")
                if btype in ("text", "image", "tool_result", "tool_use") and (btype != "text" or target.get("text")):
                    target["cache_control"] = {"type": "ephemeral"}
                    break

    def _inject_cache_control_last_tool(self, tools: list[dict] | None) -> None:
        if not tools:
            return
        for tool in reversed(tools):
            if not tool.get("defer_loading"):
                tool["cache_control"] = {"type": "ephemeral"}
                return

    # --- Response parsing ---

    def _parse_response(self, response, model: str, response_format: type[BaseModel] | None) -> CompletionResponse:
        content, tool_calls, reasoning, anthropic_content = self._parse_content_blocks(
            response.content, response_format
        )
        provider_tool_calls = self._parse_provider_tool_calls(anthropic_content)
        usage = self._parse_usage(response.usage)

        message = Message(
            role=Role.ASSISTANT,
            content=content,
            tool_calls=tool_calls or None,
            reasoning_content=reasoning,
            anthropic_content=anthropic_content,
            provider_tool_calls=provider_tool_calls,
        )

        return CompletionResponse(
            choices=[
                Choice(
                    message=message,
                    finish_reason=_FINISH_REASONS.get(response.stop_reason),
                )
            ],
            usage=usage,
            model=model,
        )

    def _parse_content_blocks(
        self,
        blocks,
        response_format: type[BaseModel] | None,
    ) -> tuple[str | None, list[ToolCall], str | None, list[dict] | None]:
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        reasoning = None
        anthropic_content: list[dict] = []

        for block in blocks:
            anthropic_content.append(self._block_to_dict(block))
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                if response_format and block.name == "_structured_output":
                    text_parts.append(json.dumps(block.input))
                else:
                    tool_calls.append(
                        ToolCall(
                            id=block.id,
                            type="function",
                            function=FunctionCall(
                                name=block.name,
                                arguments=json.dumps(block.input),
                            ),
                        )
                    )
            elif block.type == "thinking":
                reasoning = block.thinking

        content = "\n".join(text_parts) if text_parts else None
        return content, tool_calls, reasoning, anthropic_content or None

    def _parse_provider_tool_calls(self, blocks: list[dict] | None) -> list[ProviderToolCall] | None:
        if not blocks:
            return None
        starts = {
            block.get("id"): block
            for block in blocks
            if block.get("type") == "server_tool_use" and block.get("name") == "tool_search_tool_bm25"
        }
        calls = []
        for block in blocks:
            if block.get("type") != "tool_search_tool_result":
                continue
            tool_use_id = block.get("tool_use_id")
            start = starts.get(tool_use_id)
            if not start:
                continue
            calls.append(
                ProviderToolCall(
                    id=str(tool_use_id),
                    name="tool_search",
                    arguments=json.dumps({"tools": _tool_reference_names(block.get("content"))}),
                    result=_tool_search_result_text(block.get("content")),
                )
            )
        return calls or None

    def _block_to_dict(self, block) -> dict:
        if hasattr(block, "model_dump"):
            return block.model_dump(exclude_none=True)
        return dict(block)

    def _parse_usage(self, usage) -> Usage:
        return Usage(
            prompt_tokens=usage.input_tokens,
            completion_tokens=usage.output_tokens,
            cache_read_tokens=usage.cache_read_input_tokens or 0,
            cache_write_tokens=usage.cache_creation_input_tokens or 0,
        )


def _tool_reference_names(content) -> list[str]:
    if not isinstance(content, dict):
        return []
    refs = content.get("tool_references")
    if not isinstance(refs, list):
        return []
    return [name for ref in refs if isinstance(ref, dict) and isinstance(name := ref.get("tool_name"), str)]


def _tool_search_result_text(content) -> str:
    names = _tool_reference_names(content)
    if names:
        return f"Matched tools: {', '.join(names)}"
    return json.dumps(content or {}, default=str)
