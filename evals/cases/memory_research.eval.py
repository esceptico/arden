CASE = {
    "description": "Recall durable memory, then use research without losing child provenance.",
    "expected_tools": ["recall", "research"],
    "thresholds": {"max_wrong_tool_calls": 0, "max_errors": 0, "max_retries": 1},
}


async def test_memory_research(t):
    result = await t.send("Recall what I prefer for tool audits, then research one supporting implementation detail.")
    result.called_tool("recall")
    result.called_tool("research")
    result.completed()
    result.no_failed_actions()
