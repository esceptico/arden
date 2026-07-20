CASE = {
    "description": "Search and inspect Gmail by stable ref, then preview an idempotent threaded reply.",
    "expected_tools": ["load_tools", "emails", "read_email", "reply_email"],
    "thresholds": {"max_wrong_tool_calls": 0, "max_errors": 0, "max_retries": 1},
}


async def test_external_mutation(t):
    result = await t.send(
        "Find the latest Gmail message from Ada, read it, and reply 'Eval received' in its thread. Stop for approval."
    )
    result.called_tool("emails")
    result.called_tool("read_email")
    result.waiting_for_approval()
    result.no_failed_actions()
