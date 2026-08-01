from dataclasses import asdict, dataclass

from arden.agent import Usage
from arden.events.internal import RunCompleted, RunFailed

OUTBOX_RUN_COMPLETED = "run.completed"
OUTBOX_RUN_FAILED = "run.failed"
OUTBOX_AUTOMATION_SETTLED = "automation.settled"
OUTBOX_WIKI_PROJECTION_REQUESTED = "wiki.projection.requested"
OUTBOX_AGENT_RUN_REQUESTED = "agent.run.requested"


@dataclass(frozen=True)
class AutomationSettled:
    automation_run_id: int
    task_id: str
    success: bool


@dataclass(frozen=True)
class AgentRunRequested:
    session_id: str
    task_id: str


def run_completed_payload(event: RunCompleted) -> dict:
    return {
        "run_id": event.run_id,
        "session_id": event.session_id,
        "messages": list(event.messages),
        "usage": asdict(event.usage),
        "result": event.result,
        "source_refs": list(event.source_refs),
        "automation_task_id": event.automation_task_id,
    }


def run_completed_from_payload(payload: dict) -> RunCompleted:
    usage = payload.get("usage") or {}
    return RunCompleted(
        run_id=payload["run_id"],
        session_id=payload["session_id"],
        messages=tuple(payload.get("messages") or ()),
        usage=Usage(
            prompt_tokens=int(usage.get("prompt_tokens", 0)),
            completion_tokens=int(usage.get("completion_tokens", 0)),
            cache_read_tokens=int(usage.get("cache_read_tokens", 0)),
            cache_write_tokens=int(usage.get("cache_write_tokens", 0)),
        ),
        result=payload.get("result"),
        source_refs=tuple(payload.get("source_refs") or ()),
        automation_task_id=payload.get("automation_task_id"),
    )


def run_failed_payload(event: RunFailed) -> dict:
    return {
        "run_id": event.run_id,
        "session_id": event.session_id,
        "error": event.error,
        "automation_task_id": event.automation_task_id,
    }


def run_failed_from_payload(payload: dict) -> RunFailed:
    return RunFailed(
        run_id=payload["run_id"],
        session_id=payload["session_id"],
        error=payload["error"],
        automation_task_id=payload.get("automation_task_id"),
    )


def automation_settled_payload(event: AutomationSettled) -> dict:
    return {
        "automation_run_id": event.automation_run_id,
        "task_id": event.task_id,
        "success": event.success,
    }


def automation_settled_from_payload(payload: dict) -> AutomationSettled:
    return AutomationSettled(
        automation_run_id=payload["automation_run_id"],
        task_id=payload["task_id"],
        success=payload["success"],
    )


def agent_run_requested_payload(event: AgentRunRequested) -> dict:
    return {
        "session_id": event.session_id,
        "task_id": event.task_id,
    }


def agent_run_requested_from_payload(payload: dict) -> AgentRunRequested:
    return AgentRunRequested(
        session_id=payload["session_id"],
        task_id=payload["task_id"],
    )
