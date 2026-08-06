import pytest

from arden.agent.tools.dispatch import dispatch_tools
from arden.agent.types.events import ToolCompleted
from arden.agent.types.llm import Role
from arden.agent.types.tool_call import FunctionCall, PendingToolCall, ToolCall
from arden.core.content import ImageContent


class FakeRunner:
    async def execute_all(self, _calls):
        yield ToolCompleted(
            tool_id="call_1",
            name="slack_thread",
            result="thread text",
            preview="thread",
            duration_ms=1,
            is_error=False,
            data=None,
            display_name="SlackThread",
            model_content=(ImageContent(media_type="image/png", data="ZmFrZXBuZw=="),),
        )


class DataRunner:
    async def execute_all(self, _calls):
        yield ToolCompleted(
            tool_id="call_data",
            name="background",
            result="Started background agent.",
            preview="Started",
            duration_ms=1,
            is_error=False,
            data={
                "child_agent": {
                    "agent_ref": "research~abc123",
                    "agent_type": "research",
                    "wait": False,
                    "status": "running",
                }
            },
            display_name="Background",
        )


class NonDurableDataRunner:
    async def execute_all(self, _calls):
        yield ToolCompleted(
            tool_id="call_large",
            name="file_search_text",
            result="many matches",
            preview="Matches",
            duration_ms=1,
            is_error=False,
            data={"matches": [{"path": "a.py", "text": "x" * 1000}]},
            display_name="SearchText",
        )


@pytest.mark.asyncio
async def test_dispatch_tools_appends_meta_user_message_for_model_visible_images():
    raw = ToolCall(
        id="call_1",
        type="function",
        function=FunctionCall(name="slack_thread", arguments='{"message_id":"C1:1"}'),
    )
    pending = PendingToolCall(tool_call=raw, name="slack_thread", args={"message_id": "C1:1"})
    messages = []

    async for _event in dispatch_tools(FakeRunner(), messages, [pending], [raw]):
        pass

    assert messages[0] == {"role": Role.TOOL, "tool_call_id": "call_1", "content": "thread text"}
    assert messages[1]["role"] == Role.USER
    assert messages[1]["is_meta"] is True
    assert messages[1]["client_id"] == "tool-media:call_1"
    assert messages[1]["content"][-1] == {"type": "image", "media_type": "image/png", "data": "ZmFrZXBuZw=="}


@pytest.mark.asyncio
async def test_dispatch_tools_persists_tool_result_data():
    raw = ToolCall(
        id="call_data",
        type="function",
        function=FunctionCall(name="background", arguments='{"task":"research"}'),
    )
    pending = PendingToolCall(tool_call=raw, name="background", args={"task": "research"})
    messages = []

    async for _event in dispatch_tools(DataRunner(), messages, [pending], [raw]):
        pass

    assert messages[0] == {
        "role": Role.TOOL,
        "tool_call_id": "call_data",
        "content": "Started background agent.",
        "data": {
            "child_agent": {
                "agent_ref": "research~abc123",
                "agent_type": "research",
                "wait": False,
                "status": "running",
            }
        },
    }


@pytest.mark.asyncio
async def test_dispatch_tools_omits_non_durable_tool_result_data():
    raw = ToolCall(
        id="call_large",
        type="function",
        function=FunctionCall(name="file_search_text", arguments='{"query":"x"}'),
    )
    pending = PendingToolCall(tool_call=raw, name="file_search_text", args={"query": "x"})
    messages = []

    async for _event in dispatch_tools(NonDurableDataRunner(), messages, [pending], [raw]):
        pass

    assert messages[0] == {
        "role": Role.TOOL,
        "tool_call_id": "call_large",
        "content": "many matches",
    }
