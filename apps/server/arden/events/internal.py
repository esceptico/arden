from dataclasses import dataclass

from arden.agent import Usage

# --- Run lifecycle ---


class RunCompletionRejected(RuntimeError):
    """A deterministic postcondition rejected an otherwise completed run."""


@dataclass(frozen=True)
class RunCompleted:
    run_id: str
    session_id: str
    messages: tuple[dict, ...]
    usage: Usage
    result: str | None
    source_refs: tuple[dict, ...] = ()
    # Trusted scheduler identity for iteration-mode automation turns. Ordinary
    # user and post-mode runs leave this unset.
    automation_task_id: str | None = None


@dataclass(frozen=True)
class RunFailed:
    run_id: str
    session_id: str
    error: str
    automation_task_id: str | None = None
