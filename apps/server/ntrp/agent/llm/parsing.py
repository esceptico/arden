import base64
import json
import logging
from dataclasses import dataclass

from ntrp.agent.types.llm import Message, Role
from ntrp.agent.types.tool_call import FunctionCall, PendingToolCall, ToolArgumentError, ToolCall

_logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class IncompleteToolStep:
    raw_tool_calls: list[ToolCall]
    pending_calls: list[PendingToolCall]


def normalize_assistant_message(message: Message) -> dict:
    """Convert a Message dataclass to a plain dict for the message history."""
    sanitized: dict = {"role": Role.ASSISTANT, "content": message.content or ""}
    if message.tool_calls:
        tool_calls = []
        for tc in message.tool_calls:
            tc_dict: dict = {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.function.name, "arguments": tc.function.arguments},
            }
            if tc.thought_signature:
                tc_dict["thought_signature"] = base64.b64encode(tc.thought_signature).decode()
            tool_calls.append(tc_dict)
        sanitized["tool_calls"] = tool_calls
    if message.reasoning_content:
        sanitized["reasoning_content"] = message.reasoning_content
    if message.reasoning_encrypted_content:
        sanitized["reasoning_encrypted_content"] = message.reasoning_encrypted_content
    if message.anthropic_content:
        sanitized["anthropic_content"] = message.anthropic_content
    if provider_tool_calls := getattr(message, "provider_tool_calls", None):
        sanitized["provider_tool_calls"] = [
            {
                "id": call.id,
                "name": call.name,
                "arguments": call.arguments,
                "result": call.result,
                "done": call.done,
                **({"provider_item": call.provider_item} if call.provider_item else {}),
            }
            for call in provider_tool_calls
        ]
    return sanitized


def parse_tool_calls(tool_calls: list[ToolCall]) -> list[PendingToolCall]:
    """Parse raw ToolCall objects into PendingToolCall with extracted name and args."""
    result = []
    for tc in tool_calls:
        error = None
        try:
            args = json.loads(tc.function.arguments) if tc.function.arguments else {}
        except json.JSONDecodeError as exc:
            _logger.warning("Malformed tool arguments: %.200s", tc.function.arguments)
            args = {}
            error = ToolArgumentError(
                code="invalid_tool_arguments",
                message=f"Malformed JSON at character {exc.pos}: {exc.msg}",
            )
        if not isinstance(args, dict):
            args = {}
            error = ToolArgumentError(
                code="invalid_tool_arguments",
                message="Tool arguments must be a JSON object.",
            )
        result.append(PendingToolCall(tool_call=tc, name=tc.function.name, args=args, argument_error=error))
    return result


def trailing_incomplete_tool_step(messages: list[dict]) -> IncompleteToolStep | None:
    """Return only missing calls from a trailing assistant/tool sequence."""
    index = len(messages) - 1
    completed_ids: set[str] = set()
    while index >= 0 and messages[index].get("role") == Role.TOOL:
        tool_call_id = messages[index].get("tool_call_id")
        if isinstance(tool_call_id, str):
            completed_ids.add(tool_call_id)
        index -= 1
    if index < 0:
        return None
    assistant = messages[index]
    if assistant.get("role") != Role.ASSISTANT or not isinstance(assistant.get("tool_calls"), list):
        return None

    raw_calls: list[ToolCall] = []
    for value in assistant["tool_calls"]:
        if not isinstance(value, dict) or not isinstance(value.get("function"), dict):
            return None
        function = value["function"]
        tool_id = value.get("id")
        name = function.get("name")
        arguments = function.get("arguments")
        if not all(isinstance(item, str) for item in (tool_id, name, arguments)):
            return None
        signature = value.get("thought_signature")
        try:
            decoded_signature = base64.b64decode(signature) if isinstance(signature, str) else None
        except ValueError:
            decoded_signature = None
        raw_calls.append(
            ToolCall(
                id=tool_id,
                type="function",
                function=FunctionCall(name=name, arguments=arguments),
                thought_signature=decoded_signature,
            )
        )

    pending_calls = [call for call in parse_tool_calls(raw_calls) if call.tool_call.id not in completed_ids]
    if not pending_calls:
        return None
    return IncompleteToolStep(raw_tool_calls=raw_calls, pending_calls=pending_calls)
