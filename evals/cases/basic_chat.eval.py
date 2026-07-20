CASE = {
    "description": "Answer a basic prompt without unnecessary tools.",
    "expected_tools": [],
    "thresholds": {"max_wrong_tool_calls": 0, "max_errors": 0, "max_retries": 0},
}


async def test_basic_chat(t):
    result = await t.send("Say hello.")
    result.completed()
    result.no_failed_actions()
