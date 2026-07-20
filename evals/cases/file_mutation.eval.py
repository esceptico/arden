CASE = {
    "description": "Inspect a file revision, preview an edit, and stop at approval.",
    "expected_tools": ["find_files", "read_file", "edit_file"],
    "thresholds": {"max_wrong_tool_calls": 0, "max_errors": 0, "max_retries": 1},
}


async def test_file_mutation(t):
    result = await t.send(
        "Find the repository README, inspect it, and propose appending an 'Eval marker' line. Stop for approval."
    )
    result.called_tool("read_file")
    result.waiting_for_approval()
    result.no_failed_actions()
