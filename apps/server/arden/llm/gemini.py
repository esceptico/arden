import base64
import copy
import json
from itertools import count

from google import genai
from google.genai import types
from pydantic import BaseModel

from arden.agent import (
    Choice,
    CompletionResponse,
    FinishReason,
    FunctionCall,
    Message,
    Role,
    ToolCall,
    Usage,
)
from arden.core.content import render_context
from arden.llm.base import CompletionClient, EmbeddingClient
from arden.llm.utils import blocks_to_text, parse_args
from arden.observability.judgment import trace_client

_THINKING_LEVELS = {
    "minimal": types.ThinkingLevel.MINIMAL,
    "low": types.ThinkingLevel.LOW,
    "medium": types.ThinkingLevel.MEDIUM,
    "high": types.ThinkingLevel.HIGH,
}
_SUPPORTED_IMAGE_MIME_TYPES = frozenset({"image/png", "image/jpeg", "image/webp", "image/heic", "image/heif"})


class GeminiClient(CompletionClient, EmbeddingClient):
    def __init__(self, api_key: str | None = None):
        self._client = trace_client(genai.Client(api_key=api_key))

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
        **kwargs,
    ) -> CompletionResponse:
        system_instruction, contents = self._convert_messages(messages)

        config_kwargs: dict = {}
        if temperature is not None:
            config_kwargs["temperature"] = temperature
        if max_tokens is not None:
            config_kwargs["max_output_tokens"] = max_tokens
        if reasoning_effort is not None and (thinking_level := _THINKING_LEVELS.get(reasoning_effort)) is not None:
            config_kwargs["thinking_config"] = types.ThinkingConfig(
                thinking_level=thinking_level,
            )

        if response_format is not None:
            config_kwargs["response_mime_type"] = "application/json"
            config_kwargs["response_schema"] = response_format

        if tools:
            config_kwargs["tools"] = self._convert_tools(tools)
            if tool_choice == "auto":
                config_kwargs["tool_config"] = types.ToolConfig(
                    function_calling_config=types.FunctionCallingConfig(mode="AUTO")
                )

        config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            **config_kwargs,
        )

        response = await self._client.aio.models.generate_content(
            model=model,
            contents=contents,
            config=config,
        )

        return self._parse_response(response, model)

    async def _embedding(self, texts: list[str], model: str) -> list[list[float]]:
        result = await self._client.aio.models.embed_content(
            model=model,
            contents=texts,
        )
        return [e.values for e in result.embeddings]

    async def close(self) -> None:
        pass  # google-genai client doesn't need explicit cleanup

    # --- Message conversion ---

    def _build_tool_name_map(self, messages: list[dict]) -> dict[str, str]:
        name_map: dict[str, str] = {}
        for msg in messages:
            if msg["role"] == Role.ASSISTANT:
                for tc in msg.get("tool_calls", []):
                    name_map[tc["id"]] = tc["function"]["name"]
        return name_map

    def _convert_messages(self, messages: list[dict]) -> tuple[str | None, list[types.Content]]:
        system_instruction = None
        contents: list[types.Content] = []
        tool_name_map = self._build_tool_name_map(messages)

        for msg in messages:
            match msg["role"]:
                case Role.SYSTEM:
                    system_instruction = blocks_to_text(msg["content"])
                case Role.USER:
                    contents.append(self._convert_user(msg))
                case Role.ASSISTANT:
                    if content := self._convert_assistant(msg):
                        contents.append(content)
                case Role.TOOL:
                    part = self._convert_tool_result(msg, tool_name_map)
                    self._append_tool_part(contents, part)

        return system_instruction, contents

    def _convert_user(self, msg: dict) -> types.Content:
        content = msg["content"]
        if isinstance(content, str):
            return types.Content(role="user", parts=[types.Part(text=content)])
        parts: list[types.Part] = []
        for block in content:
            match block.get("type"):
                case "text":
                    parts.append(types.Part(text=block["text"]))
                case "image":
                    if block["media_type"] not in _SUPPORTED_IMAGE_MIME_TYPES:
                        continue
                    parts.append(
                        types.Part.from_bytes(
                            data=base64.b64decode(block["data"]),
                            mime_type=block["media_type"],
                        )
                    )
                case "context":
                    parts.append(types.Part(text=render_context(block)))
        return types.Content(role="user", parts=parts or [types.Part(text="")])

    def _convert_assistant(self, msg: dict) -> types.Content | None:
        parts: list[types.Part] = []
        if text := msg["content"]:
            parts.append(types.Part(text=text))
        for tc in msg.get("tool_calls", []):
            fn = tc["function"]
            part_kwargs: dict = {
                "function_call": types.FunctionCall(
                    name=fn["name"],
                    args=parse_args(fn.get("arguments", "{}")),
                ),
            }
            if sig := tc.get("thought_signature"):
                part_kwargs["thought_signature"] = base64.b64decode(sig)
            parts.append(types.Part(**part_kwargs))
        return types.Content(role="model", parts=parts) if parts else None

    def _convert_tool_result(self, msg: dict, tool_name_map: dict[str, str]) -> types.Part:
        tool_call_id = msg["tool_call_id"]
        tool_name = tool_name_map.get(tool_call_id, "unknown")
        content_str = msg["content"]
        try:
            result_dict = json.loads(content_str)
        except (json.JSONDecodeError, TypeError):
            result_dict = {"result": content_str}

        return types.Part(
            function_response=types.FunctionResponse(
                name=tool_name,
                response=result_dict,
            )
        )

    def _append_tool_part(self, contents: list[types.Content], part: types.Part) -> None:
        if contents and contents[-1].role == "user":
            last_parts = contents[-1].parts
            if last_parts and any(p.function_response for p in last_parts):
                contents[-1].parts.append(part)
                return
        contents.append(types.Content(role="user", parts=[part]))

    # --- Tool schema ---

    def _convert_tools(self, tools: list[dict]) -> list[types.Tool]:
        declarations = []
        for tool in tools:
            fn = tool.get("function", tool)
            params = fn.get("parameters")
            if params:
                params = self._clean_schema(params)
            declarations.append(
                types.FunctionDeclaration(
                    name=fn["name"],
                    description=fn.get("description", ""),
                    parameters=params,
                )
            )
        return [types.Tool(function_declarations=declarations)]

    def _clean_schema(self, schema: dict) -> dict:
        schema = copy.deepcopy(schema)
        self._clean_schema_recursive(schema)
        return schema

    def _clean_schema_recursive(self, schema: dict) -> None:
        # `types.Schema` accepts a narrow JSON Schema dialect: anyOf and enum,
        # but not allOf, oneOf, const or discriminator. Fold the three forms
        # pydantic emits into the accepted ones before pruning keys — allOf is a
        # `$ref` that carried sibling keys (an enum plus a description), oneOf a
        # discriminated union, const a single-value Literal.
        for member in schema.pop("allOf", None) or []:
            if isinstance(member, dict):
                for key, value in member.items():
                    schema.setdefault(key, value)
        if "oneOf" in schema:
            schema["anyOf"] = schema.pop("oneOf")
        if "const" in schema:
            schema["enum"] = [schema.pop("const")]

        for key in (
            "default",
            "exclusiveMaximum",
            "exclusiveMinimum",
            "additionalProperties",
            "$schema",
            "$defs",
            "title",
            # Every member of a discriminated union already pins its own tag
            # value, so the annotation adds no constraint to carry over.
            "discriminator",
        ):
            schema.pop(key, None)

        if schema.get("type") == "string" and "format" in schema:
            if schema["format"] not in {"enum", "date-time"}:
                del schema["format"]

        for prop in (schema.get("properties") or {}).values():
            if isinstance(prop, dict):
                self._clean_schema_recursive(prop)

        if isinstance(schema.get("items"), dict):
            self._clean_schema_recursive(schema["items"])

        for item in schema.get("anyOf") or []:
            if isinstance(item, dict):
                self._clean_schema_recursive(item)

    # --- Response parsing ---

    def _parse_response(self, response, model: str) -> CompletionResponse:
        usage = self._parse_usage(response.usage_metadata)
        candidates = response.candidates
        if not candidates:
            return self._empty_response(model, usage)

        candidate = candidates[0]
        parts = candidate.content.parts if candidate.content else []
        content, tool_calls = self._parse_parts(parts)
        finish_reason = self._map_finish_reason(candidate.finish_reason)

        message = Message(
            role=Role.ASSISTANT,
            content=content,
            tool_calls=tool_calls or None,
            reasoning_content=None,
        )

        return CompletionResponse(
            choices=[Choice(message=message, finish_reason=finish_reason)],
            usage=usage,
            model=model,
        )

    def _empty_response(self, model: str, usage: Usage) -> CompletionResponse:
        message = Message(role=Role.ASSISTANT, content=None, tool_calls=None, reasoning_content=None)
        return CompletionResponse(
            choices=[Choice(message=message, finish_reason=FinishReason.STOP)],
            usage=usage,
            model=model,
        )

    def _parse_parts(self, parts) -> tuple[str | None, list[ToolCall]]:
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        call_seq = count()

        for part in parts:
            if part.text is not None and not part.thought:
                text_parts.append(part.text)
            elif part.function_call is not None:
                fc = part.function_call
                tool_calls.append(
                    ToolCall(
                        id=f"call_{fc.name}_{next(call_seq)}",
                        type="function",
                        function=FunctionCall(
                            name=fc.name,
                            arguments=json.dumps(dict(fc.args)) if fc.args else "{}",
                        ),
                        thought_signature=part.thought_signature,
                    )
                )

        content = "\n".join(text_parts) if text_parts else None
        return content, tool_calls

    def _map_finish_reason(self, reason) -> FinishReason:
        mapping: dict[str, FinishReason] = {
            "STOP": FinishReason.STOP,
            "MAX_TOKENS": FinishReason.LENGTH,
            "SAFETY": FinishReason.CONTENT_FILTER,
            "FinishReason.STOP": FinishReason.STOP,
            "FinishReason.MAX_TOKENS": FinishReason.LENGTH,
        }
        return mapping.get(str(reason) if reason else "STOP", FinishReason.STOP)

    def _parse_usage(self, usage_meta) -> Usage:
        if not usage_meta:
            return Usage(prompt_tokens=0, completion_tokens=0, cache_read_tokens=0, cache_write_tokens=0)
        total_prompt = usage_meta.prompt_token_count or 0
        cache_read = usage_meta.cached_content_token_count or 0
        return Usage(
            prompt_tokens=total_prompt - cache_read,
            completion_tokens=usage_meta.candidates_token_count or 0,
            cache_read_tokens=cache_read,
            cache_write_tokens=0,
        )
