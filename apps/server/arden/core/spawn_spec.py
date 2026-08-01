"""Serializable description of a subagent spawn.

The spec records what a spawn IS, not how it was negotiated: override
resolution (research/workflow model fallbacks), agent-type profile
application, and tool-scope resolution all happen before construction, so the
spec holds only outputs. It is the durable payload that makes a spawn
re-dispatchable (outbox recovery), replayable (workflow journal), and
attributable (approvals, cancellation) — live objects never cross this
boundary.
"""

import json
from dataclasses import dataclass

from arden.core.isolation import IsolationLevel


@dataclass(frozen=True)
class ParentRef:
    """Identity of the spawning side — lineage, not recipe. A parent's own
    spec, when it exists, is reachable via its invocation record."""

    session_id: str
    run_id: str
    tool_call_id: str | None


@dataclass(frozen=True)
class WorkflowRef:
    workflow_id: str
    phase: str | None
    lifecycle_id: str | None


@dataclass(frozen=True)
class SpawnSpec:
    # The child's whole initial history: FULL isolation = [system, task];
    # SHARED = the caller's context followed by the task.
    context: tuple[dict, ...]
    # Pure label ("research", "background_research", a workflow phase) — the
    # profile it once named is already resolved into `context` and `tools`.
    agent_type: str
    # Resolved effective allowlist, frozen at spawn: scope ∩ parent allowlist
    # plus extra tool names. Never re-derived at dispatch — re-resolution
    # against a changed registry could silently widen the child.
    tools: tuple[str, ...]
    model: str
    reasoning_effort: str | None
    timeout_seconds: int
    isolation: IsolationLevel
    parent: ParentRef
    workflow: WorkflowRef | None

    def to_json(self) -> str:
        payload = {
            "context": [dict(message) for message in self.context],
            "agent_type": self.agent_type,
            "tools": list(self.tools),
            "model": self.model,
            "reasoning_effort": self.reasoning_effort,
            "timeout_seconds": self.timeout_seconds,
            "isolation": self.isolation.value,
            "parent": {
                "session_id": self.parent.session_id,
                "run_id": self.parent.run_id,
                "tool_call_id": self.parent.tool_call_id,
            },
            "workflow": (
                {
                    "workflow_id": self.workflow.workflow_id,
                    "phase": self.workflow.phase,
                    "lifecycle_id": self.workflow.lifecycle_id,
                }
                if self.workflow
                else None
            ),
        }
        return json.dumps(payload)

    @classmethod
    def from_json(cls, raw: str) -> "SpawnSpec":
        payload = json.loads(raw)
        workflow = payload.get("workflow")
        return cls(
            context=tuple(dict(message) for message in payload["context"]),
            agent_type=payload["agent_type"],
            tools=tuple(payload["tools"]),
            model=payload["model"],
            reasoning_effort=payload.get("reasoning_effort"),
            timeout_seconds=payload["timeout_seconds"],
            isolation=IsolationLevel(payload["isolation"]),
            parent=ParentRef(
                session_id=payload["parent"]["session_id"],
                run_id=payload["parent"]["run_id"],
                tool_call_id=payload["parent"].get("tool_call_id"),
            ),
            workflow=WorkflowRef(
                workflow_id=workflow["workflow_id"],
                phase=workflow.get("phase"),
                lifecycle_id=workflow.get("lifecycle_id"),
            )
            if workflow
            else None,
        )
