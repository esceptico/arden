from dataclasses import dataclass

from arden.agent import Usage
from arden.agent.types.tools import ToolSourceRef

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
    source_refs: tuple[ToolSourceRef, ...] = ()
    # Trusted scheduler identity for iteration-mode automation turns. Ordinary
    # user and post-mode runs leave this unset.
    automation_task_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.source_refs, tuple) or any(
            not isinstance(source_ref, ToolSourceRef) for source_ref in self.source_refs
        ):
            raise TypeError("completed run source references must be ToolSourceRef values")


@dataclass(frozen=True)
class RunFailed:
    run_id: str
    session_id: str
    error: str
    automation_task_id: str | None = None
