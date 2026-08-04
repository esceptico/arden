import asyncio
from collections.abc import AsyncGenerator

from arden.agent.tools.runner import ToolRunner
from arden.agent.types.events import ToolCompleted, ToolStarted
from arden.agent.types.llm import Role
from arden.agent.types.tool_call import PendingToolCall, ToolCall
from arden.agent.types.tools import ToolResult
from arden.core.content import ContentBlock, ContextContent
from arden.core.tool_result_data import persistable_tool_result_data


async def dispatch_tools(
    runner: ToolRunner,
    messages: list[dict],
    calls: list[PendingToolCall],
    raw_tool_calls: list[ToolCall],
    recovered_results: dict[str, ToolResult] | None = None,
    rejection: ToolResult | None = None,
) -> AsyncGenerator[ToolStarted | ToolCompleted]:
    recovered_results = recovered_results or {}
    results: dict[str, str] = {tool_id: result.content for tool_id, result in recovered_results.items()}
    result_data: dict[str, dict] = {}
    model_content: dict[str, tuple[ContentBlock, ...]] = {}
    for tool_id, result in recovered_results.items():
        if data := persistable_tool_result_data(result.data, result.source_refs):
            result_data[tool_id] = data
        if result.model_content:
            model_content[tool_id] = result.model_content

    try:
        event_stream = runner.reject_all(calls, rejection) if rejection is not None else runner.execute_all(calls)
        async for event in event_stream:
            if isinstance(event, ToolCompleted):
                results[event.tool_id] = event.result
                if data := persistable_tool_result_data(event.data, event.source_refs):
                    result_data[event.tool_id] = data
                if event.model_content:
                    model_content[event.tool_id] = event.model_content
            yield event
    except asyncio.CancelledError:
        _append_results(messages, raw_tool_calls, results, result_data, missing_content="Tool call cancelled.")
        _append_model_content(messages, raw_tool_calls, model_content)
        raise

    _append_results(messages, raw_tool_calls, results, result_data, missing_content="Tool call result missing.")
    _append_model_content(messages, raw_tool_calls, model_content)


def _append_results(
    messages: list[dict],
    tool_calls: list[ToolCall],
    results: dict[str, str],
    result_data: dict[str, dict] | None = None,
    *,
    missing_content: str,
) -> None:
    for tc in tool_calls:
        message = {
            "role": Role.TOOL,
            "tool_call_id": tc.id,
            "content": results.get(tc.id, missing_content),
        }
        if result_data and tc.id in result_data:
            message["data"] = result_data[tc.id]
        messages.append(message)


def _append_model_content(
    messages: list[dict],
    tool_calls: list[ToolCall],
    model_content: dict[str, tuple[ContentBlock, ...]],
) -> None:
    for tc in tool_calls:
        blocks = model_content.get(tc.id)
        if not blocks:
            continue
        messages.append(
            {
                "role": Role.USER,
                "client_id": f"tool-media:{tc.id}",
                "is_meta": True,
                "content": [
                    _content_block_dict(
                        ContextContent(
                            content_type="tool_result_media",
                            content=f"Media returned by tool {tc.function.name}.",
                            metadata={"tool_call_id": tc.id, "tool_name": tc.function.name},
                        )
                    ),
                    *(_content_block_dict(block) for block in blocks),
                ],
            }
        )


def _content_block_dict(block: ContentBlock) -> dict:
    return block.model_dump() if hasattr(block, "model_dump") else dict(block)
