CASE = {
    "description": "Inspect and dispatch an existing automation by stable reference.",
    "expected_tools": ["load_tools", "list_automations", "run_automation"],
    "thresholds": {"max_wrong_tool_calls": 0, "max_errors": 1, "max_retries": 1},
}


async def test_schedule_dispatch(t):
    result = await t.send("Find the automation named daily digest and run it now; stop if it does not exist.")
    result.completed()
    result.no_failed_actions()
