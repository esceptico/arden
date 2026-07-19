import base64
import json
import logging

from ntrp.agent.types.llm import Message, Role
from ntrp.agent.types.tool_call import PendingToolCall, ToolArgumentError, ToolCall

_logger = logging.getLogger(__name__)


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
