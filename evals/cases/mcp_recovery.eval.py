CASE = {
    "description": "Recover a hidden MCP tool through discovery without guessing its schema.",
    "expected_tools": ["tool_search", "load_tools"],
    "thresholds": {"max_wrong_tool_calls": 0, "max_errors": 1, "max_retries": 1},
}


async def test_mcp_recovery(t):
    result = await t.send(
        "Find an available MCP tool for reading a document. Discover or load its exact schema; do not mutate anything."
    )
    result.completed()
    result.no_failed_actions()
