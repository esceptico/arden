from dataclasses import dataclass


@dataclass
class EventEvalResult:
    name: str
    passed: bool
    events: list[dict]
    error: str | None = None
    metrics: dict[str, int] | None = None

    def to_dict(self) -> dict:
        payload = {
            "name": self.name,
            "passed": self.passed,
            "events": self.events,
            "error": self.error,
        }
        if self.metrics is not None:
            payload["metrics"] = self.metrics
        return payload
