import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from evals.assertions import ErgonomicsThresholds, assert_ergonomics_thresholds
from evals.metrics import compute_agent_ergonomics_metrics
from evals.run import discover_event_cases, main


def test_discovers_executable_agent_ergonomics_cases():
    cases = discover_event_cases()

    assert [case.name for case in cases] == [
        "approval_wait",
        "basic_chat",
        "deferred_tools",
        "external_mutation",
        "file_mutation",
        "mcp_recovery",
        "memory_research",
        "schedule_dispatch",
        "slack_read",
    ]
    assert all(case.description for case in cases)
    assert all(case.path.name.endswith(".eval.py") for case in cases)


def test_trace_metrics_count_tools_failures_retries_tokens_and_offloads():
    events = [
        {"type": "TOOL_CALL_START", "tool_call_name": "emails"},
        {
            "type": "TOOL_CALL_RESULT",
            "tool_name": "emails",
            "is_error": True,
            "outcome": {"status": "failed"},
        },
        {"type": "TOOL_CALL_START", "tool_call_name": "emails"},
        {
            "type": "TOOL_CALL_RESULT",
            "tool_name": "emails",
            "outcome": {"status": "succeeded"},
            "data": {"truncated": True, "raw_ref": "tr_1"},
        },
        {"type": "token_usage", "input_tokens": 120, "output_tokens": 30},
    ]

    metrics = compute_agent_ergonomics_metrics(events, expected_tools={"emails"}, latency_ms=42)

    assert metrics.tool_calls == 2
    assert metrics.errors == 1
    assert metrics.retries == 1
    assert metrics.recoveries == 1
    assert metrics.input_tokens == 120
    assert metrics.output_tokens == 30
    assert metrics.truncations == 1
    assert metrics.offloads == 1
    assert metrics.latency_ms == 42


def test_metrics_detect_wrong_tool_and_enforce_thresholds():
    metrics = compute_agent_ergonomics_metrics(
        [{"type": "TOOL_CALL_START", "tool_call_name": "send_email"}],
        expected_tools={"emails"},
    )

    assert metrics.wrong_tool_calls == 1
    with pytest.raises(AssertionError, match="wrong_tool_calls=1"):
        assert_ergonomics_thresholds(metrics, ErgonomicsThresholds())


def test_agent_ergonomics_dry_run_reports_contracts_without_provider(capsys):
    assert main(["--suite", "agent-ergonomics", "--dry-run", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert {row["name"] for row in payload} >= {"file_mutation", "external_mutation", "mcp_recovery"}
    assert all("thresholds" in row and "expected_tools" in row for row in payload)
