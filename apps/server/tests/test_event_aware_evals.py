import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from evals.assertions import EventAssertions
from evals.client import parse_sse_events
from evals.report import EventEvalResult
from evals.run import run_event_case
from evals.runtime_case import RuntimeCase


def test_parse_sse_events_extracts_json_payloads():
    raw = 'id: 1\nevent: approval_needed\ndata: {"type":"approval_needed","tool_name":"x"}\n\n'

    assert parse_sse_events(raw) == [{"type": "approval_needed", "tool_name": "x"}]


def test_event_assertions_check_deterministic_runtime_events():
    t = EventAssertions(
        [
            {"type": "TOOL_CALL_START", "tool_call_name": "load_tools"},
            {"type": "tool_group_loaded", "group": "slack"},
            {"type": "RUN_FINISHED", "content": "Done"},
        ]
    )

    t.called_tool("load_tools")
    t.loaded_tool_group("slack")
    t.completed()
    t.no_failed_actions()
    t.reply_includes("Done")


def test_event_assertions_fail_with_useful_message():
    t = EventAssertions([{"type": "RUN_ERROR", "message": "boom"}])

    with pytest.raises(AssertionError, match="Expected completed run"):
        t.completed()


@pytest.mark.asyncio
async def test_runtime_case_sends_prompt_and_captures_events():
    async def send(prompt):
        assert prompt == "Find Eve thread"
        return [{"type": "RUN_FINISHED"}]

    case = RuntimeCase(send)

    assertions = await case.send("Find Eve thread")

    assertions.completed()


@pytest.mark.asyncio
async def test_run_event_case_executes_case_against_runtime_case():
    async def send(prompt):
        assert prompt == "Say hello."
        return [{"type": "RUN_FINISHED"}]

    async def case(t):
        result = await t.send("Say hello.")
        result.completed()

    result = await run_event_case("basic_chat", case, RuntimeCase(send))

    assert result.passed is True


def test_event_eval_result_serializes_summary():
    result = EventEvalResult(name="basic_chat", passed=True, events=[{"type": "RUN_FINISHED"}])

    assert result.to_dict() == {
        "name": "basic_chat",
        "passed": True,
        "events": [{"type": "RUN_FINISHED"}],
        "error": None,
    }
