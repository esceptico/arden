CASE = {
    "description": "Load a deferred integration group through the canonical loader.",
    "expected_tools": ["load_tools"],
    "thresholds": {"max_wrong_tool_calls": 0, "max_errors": 0, "max_retries": 0},
}


async def test_deferred_tools(t):
    result = await t.send("Load Slack tools.")
    result.called_tool("load_tools")
    result.loaded_tool_group("slack")
    result.completed()
