CASE = {
    "description": "Search Slack, inspect an exact message ref, and preserve its provenance.",
    "expected_tools": ["load_tools", "slack_search", "slack_thread"],
    "thresholds": {"max_wrong_tool_calls": 0, "max_errors": 0, "max_retries": 1},
}


async def test_slack_read(t):
    result = await t.send("Find the latest Slack message mentioning roadmap and read its exact thread. Summarize only.")
    result.called_tool("slack_search")
    result.called_tool("slack_thread")
    result.completed()
    result.no_failed_actions()
