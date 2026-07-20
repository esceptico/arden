from dataclasses import dataclass

from evals.metrics import AgentErgonomicsMetrics


@dataclass(frozen=True)
class ErgonomicsThresholds:
    max_wrong_tool_calls: int = 0
    max_errors: int = 0
    max_retries: int = 1
    max_latency_ms: int = 120_000


def assert_ergonomics_thresholds(metrics: AgentErgonomicsMetrics, thresholds: ErgonomicsThresholds) -> None:
    checks = {
        "wrong_tool_calls": (metrics.wrong_tool_calls, thresholds.max_wrong_tool_calls),
        "errors": (metrics.errors, thresholds.max_errors),
        "retries": (metrics.retries, thresholds.max_retries),
        "latency_ms": (metrics.latency_ms, thresholds.max_latency_ms),
    }
    failures = [f"{name}={actual} > {limit}" for name, (actual, limit) in checks.items() if actual > limit]
    if failures:
        raise AssertionError("Ergonomics thresholds failed: " + ", ".join(failures))


class EventAssertions:
    def __init__(self, events: list[dict]):
        self.events = events

    def completed(self) -> None:
        if not self._has_type("RUN_FINISHED"):
            raise AssertionError("Expected completed run (RUN_FINISHED)")

    def failed(self) -> None:
        if not self._has_type("RUN_ERROR"):
            raise AssertionError("Expected failed run (RUN_ERROR)")

    def waiting_for_approval(self) -> None:
        if not self._has_state_or_type("waiting_for_approval", "approval_needed"):
            raise AssertionError("Expected waiting_for_approval state")

    def waiting_for_input(self) -> None:
        if not self._has_state_or_type("waiting_for_input", "input_needed"):
            raise AssertionError("Expected waiting_for_input state")

    def called_tool(self, tool_name: str) -> None:
        for event in self.events:
            if event.get("tool_call_name") == tool_name or event.get("tool_name") == tool_name:
                return
        raise AssertionError(f"Expected tool call: {tool_name}")

    def loaded_tool_group(self, group: str) -> None:
        for event in self.events:
            if event.get("group") == group or event.get("tool_group") == group:
                return
        raise AssertionError(f"Expected loaded tool group: {group}")

    def event_type(self, event_type: str) -> None:
        if not self._has_type(event_type):
            raise AssertionError(f"Expected event type: {event_type}")

    def no_failed_actions(self) -> None:
        failures = [e for e in self.events if e.get("type") in {"RUN_ERROR", "task_finished"} and e.get("status") == "failed"]
        if failures:
            raise AssertionError(f"Expected no failed actions, got {len(failures)}")

    def reply_includes(self, text: str) -> None:
        for event in self.events:
            content = event.get("content") or event.get("delta") or event.get("message") or ""
            if text in content:
                return
        raise AssertionError(f"Expected reply to include: {text}")

    def _has_type(self, event_type: str) -> bool:
        return any(event.get("type") == event_type for event in self.events)

    def _has_state_or_type(self, state: str, event_type: str) -> bool:
        return any(event.get("workflow_state") == state or event.get("type") == event_type for event in self.events)
