import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

from arden.agent import Result, StopReason, Usage
from arden.context.models import SessionState
from arden.core.factory import AgentConfig
from arden.events.internal import RunCompleted
from arden.operator import runner
from arden.operator.runner import OperatorDeps, RunRequest
from arden.server.bus import SessionBus
from arden.skills.registry import SkillRegistry


def _write_skill(root: Path, name: str, description: str) -> None:
    skill_dir = root / name
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {description}\n---\n\n# Body\n")


class FakeExecutor:
    def get_tools(
        self,
        read_only: bool | None = None,
        actions: frozenset | None = None,
        extra_names: frozenset[str] = frozenset(),
        scope: tuple[str, ...] | None = None,
    ) -> list[dict]:
        return []


class RecordingExecutor:
    """Captures the get_tools() call so tests can assert on the resolved
    toolset request without needing a real ToolRegistry."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def get_tools(
        self,
        read_only: bool | None = None,
        actions: frozenset | None = None,
        extra_names: frozenset[str] = frozenset(),
        scope: tuple[str, ...] | None = None,
    ) -> list[dict]:
        self.calls.append(
            {
                "read_only": read_only,
                "actions": actions,
                "extra_names": extra_names,
                "scope": scope,
            }
        )
        return []


def _deps(skill_registry: SkillRegistry | None, executor=None, enqueue_run_completed=None) -> OperatorDeps:
    return OperatorDeps(
        executor=executor if executor is not None else FakeExecutor(),
        config=AgentConfig(model="test-model", research_model=None, max_depth=1, deferred_tools=False),
        source_details={},
        create_session=lambda: SessionState(
            session_id="s1",
            started_at=datetime.now(UTC),
            name="test",
        ),
        notifiers=[],
        enqueue_run_completed=enqueue_run_completed,
        skill_registry=skill_registry,
    )


@pytest.mark.asyncio
async def test_streaming_completion_preserves_automation_identity(monkeypatch):
    events: list[RunCompleted] = []

    async def enqueue(event: RunCompleted) -> bool:
        events.append(event)
        return True

    class Agent:
        async def stream(self, messages):
            yield Result(text="done", stop_reason=StopReason.END_TURN, steps=1, usage=Usage())

    monkeypatch.setattr(runner, "create_agent", lambda **kwargs: Agent())
    request = RunRequest(
        prompt="do it",
        auto_approve=True,
        source_id="automation-1",
        automation_id="automation-1",
    )

    result = await runner.run_agent_streaming(
        _deps(None, enqueue_run_completed=enqueue),
        request,
        SessionBus(session_id="automation:events"),
        "automation-1",
    )

    assert result.output == "done"
    assert len(events) == 1
    assert events[0].automation_task_id == "automation-1"


@pytest.mark.asyncio
async def test_streaming_cancellation_is_not_published_as_completion(monkeypatch):
    events: list[RunCompleted] = []

    async def enqueue(event: RunCompleted) -> bool:
        events.append(event)
        return True

    class Agent:
        async def stream(self, messages):
            raise asyncio.CancelledError
            yield

    monkeypatch.setattr(runner, "create_agent", lambda **kwargs: Agent())
    request = RunRequest(
        prompt="do it",
        auto_approve=True,
        source_id="automation-1",
        automation_id="automation-1",
    )

    with pytest.raises(asyncio.CancelledError):
        await runner.run_agent_streaming(
            _deps(None, enqueue_run_completed=enqueue),
            request,
            SessionBus(session_id="automation:events"),
            "automation-1",
        )

    assert events == []


@pytest.mark.asyncio
async def test_prepare_includes_skill_inventory_in_system_prompt(tmp_path, monkeypatch):
    _write_skill(tmp_path, "operator-helper", "Helps operator runs use skills")
    registry = SkillRegistry()
    registry.load([(tmp_path, "area")])
    monkeypatch.setattr(runner, "create_agent", lambda **kwargs: object())

    _, messages, _, _ = await runner._prepare(
        _deps(registry),
        RunRequest(prompt="do it", auto_approve=True, source_id="test"),
    )

    system_prompt = messages[0]["content"]
    assert "<available_skills>" in system_prompt
    assert "operator-helper" in system_prompt
    assert "Helps operator runs use skills" in system_prompt


@pytest.mark.asyncio
async def test_prepare_allows_missing_skill_registry(monkeypatch):
    monkeypatch.setattr(runner, "create_agent", lambda **kwargs: object())

    _, messages, _, _ = await runner._prepare(
        _deps(None),
        RunRequest(prompt="do it", auto_approve=True, source_id="test"),
    )

    assert "<available_skills>" not in messages[0]["content"]


@pytest.mark.asyncio
async def test_prepare_stamps_server_owned_automation_identity(monkeypatch):
    captured = {}

    def create_agent(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(runner, "create_agent", create_agent)
    await runner._prepare(
        _deps(None),
        RunRequest(
            prompt="review due facts",
            auto_approve=True,
            source_id="builtin-memory-retention",
            automation_id="builtin-memory-retention",
        ),
    )

    assert captured["session_state"].origin_automation_id == "builtin-memory-retention"
    assert captured["automation_id"] == "builtin-memory-retention"


@pytest.mark.asyncio
async def test_prepare_auto_approve_ignores_extra_tool_names(monkeypatch):
    """extra_tool_names takes precedence over auto_approve for the TOOLSET:
    naming extras always means the narrow read+extras set, with auto_approve
    deciding only whether approval gates apply within it."""
    monkeypatch.setattr(runner, "create_agent", lambda **kwargs: object())
    executor = RecordingExecutor()

    await runner._prepare(
        _deps(None, executor=executor),
        RunRequest(
            prompt="do it",
            auto_approve=True,
            source_id="test",
            extra_tool_names=frozenset({"remember"}),
        ),
    )

    assert executor.calls == [
        {
            "read_only": True,
            "actions": None,
            "extra_names": frozenset({"remember"}),
            "scope": None,
        }
    ]


@pytest.mark.asyncio
async def test_prepare_non_auto_approve_threads_extra_tool_names(monkeypatch):
    """The observe-mode decoupling: non-auto-approve still means read_only,
    but named extras (e.g. memory-write tools) ride along on top of it —
    this is the fix for observe-mode area agents being unable to write
    memory despite their contract promising it."""
    monkeypatch.setattr(runner, "create_agent", lambda **kwargs: object())
    executor = RecordingExecutor()

    await runner._prepare(
        _deps(None, executor=executor),
        RunRequest(
            prompt="do it",
            auto_approve=False,
            source_id="test",
            extra_tool_names=frozenset({"remember", "memory_patch"}),
        ),
    )

    assert executor.calls == [
        {
            "read_only": True,
            "actions": None,
            "extra_names": frozenset({"remember", "memory_patch"}),
            "scope": None,
        }
    ]


@pytest.mark.asyncio
async def test_prepare_auto_approve_without_scope_stays_read_only(monkeypatch):
    """Approval bypass is not a capability grant."""
    monkeypatch.setattr(runner, "create_agent", lambda **kwargs: object())
    executor = RecordingExecutor()

    await runner._prepare(
        _deps(None, executor=executor),
        RunRequest(prompt="do it", auto_approve=True, skip_approvals=True, source_id="test"),
    )

    assert executor.calls == [{"read_only": True, "actions": None, "extra_names": frozenset(), "scope": None}]


@pytest.mark.asyncio
async def test_prepare_explicit_scope_selects_writes_without_global_widening(monkeypatch):
    """A scope is the sole capability grant for detached write tools."""
    monkeypatch.setattr(runner, "create_agent", lambda **kwargs: object())
    executor = RecordingExecutor()

    await runner._prepare(
        _deps(None, executor=executor),
        RunRequest(
            prompt="do it",
            auto_approve=True,
            skip_approvals=True,
            source_id="test",
            tool_scope=("slack_post_message", "current_time"),
        ),
    )

    assert executor.calls == [
        {
            "read_only": None,
            "actions": None,
            "extra_names": frozenset(),
            "scope": ("slack_post_message", "current_time"),
        }
    ]
