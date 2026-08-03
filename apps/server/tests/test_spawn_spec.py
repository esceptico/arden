from arden.core.isolation import IsolationLevel
from arden.core.spawn_spec import ParentRef, SpawnSpec, WorkflowRef


def _spec(**overrides) -> SpawnSpec:
    defaults = {
        "context": (
            {"role": "system", "content": "You are a worker."},
            {"role": "user", "content": "Scan the docs."},
        ),
        "agent_type": "research",
        "tools": ("file_read", "web_search"),
        "model": "claude-sonnet-5",
        "reasoning_effort": "high",
        "timeout_seconds": 1800,
        "isolation": IsolationLevel.FULL,
        "parent": ParentRef(session_id="sess-1", run_id="run-1", tool_call_id="call-1"),
        "workflow": WorkflowRef(workflow_id="wf-1", phase="Scan", lifecycle_id="call-1:ab12"),
    }
    defaults.update(overrides)
    return SpawnSpec(**defaults)


def test_round_trip_preserves_everything():
    spec = _spec()
    assert SpawnSpec.from_json(spec.to_json()) == spec


def test_round_trip_without_workflow_and_effort():
    spec = _spec(workflow=None, reasoning_effort=None)
    restored = SpawnSpec.from_json(spec.to_json())
    assert restored == spec
    assert restored.workflow is None
    assert restored.reasoning_effort is None


def test_isolation_survives_as_enum():
    restored = SpawnSpec.from_json(_spec(isolation=IsolationLevel.SHARED).to_json())
    assert restored.isolation is IsolationLevel.SHARED
