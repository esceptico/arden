import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio
from pydantic import BaseModel, ValidationError

import arden.database as database
import arden.tools.research as research_module
import arden.tools.research_artifacts as research_artifacts_module
from arden.agent import AgentHooks, Result, SharedLedger, StopReason, ToolCompleted, ToolStarted, Usage
from arden.agent.types.tools import ToolSourceRef
from arden.context.models import SessionState
from arden.context.store import SessionStore
from arden.core.agent_types import apply_profile
from arden.core.public_refs import public_ref
from arden.core.spawner import SpawnResult, create_spawn_fn
from arden.tools.core import ToolAction, ToolPolicy, ToolResult, ToolScope, tool
from arden.tools.core.background_tasks import BackgroundTaskRegistry
from arden.tools.core.context import IOBridge, RunContext, ToolContext, ToolExecution
from arden.tools.core.registry import ToolRegistry
from arden.tools.core.scope import tools
from arden.tools.executor import ToolExecutor


@pytest.fixture(autouse=True)
def _isolate_research_artifacts(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(research_artifacts_module, "ARDEN_DIR", tmp_path / ".arden")


@pytest_asyncio.fixture
async def session_store(tmp_path: Path):
    conn = await database.connect(tmp_path / "sessions.db")
    read_conn = await database.connect(tmp_path / "sessions.db", readonly=True)
    store = SessionStore(conn, read_conn)
    await store.init_schema()
    yield store
    await read_conn.close()
    await conn.close()


SCRATCHPAD_TOOL_NAMES = {
    "write_research_artifact",
    "append_research_artifact",
    "read_research_artifact",
    "list_research_artifacts",
}


def _agent_ref(task: str, child_run_id: str) -> str:
    return public_ref(task, child_run_id, empty_slug="agent")


HARNESS_TOOL_NAMES = {
    "research_track_source",
    "research_curate",
    "research_verify_claim",
    "research_question",
    "research_track_search",
}


def test_scratchpad_tools_are_research_only():
    from arden.integrations.core import CORE_INTEGRATIONS

    assert set(research_module.RESEARCH_AGENT_TOOLS) >= SCRATCHPAD_TOOL_NAMES | HARNESS_TOOL_NAMES
    main_tool_names = {name for integ in CORE_INTEGRATIONS for name in integ.tools}
    assert not ((SCRATCHPAD_TOOL_NAMES | HARNESS_TOOL_NAMES) & main_tool_names)


def test_spawn_result_requires_public_refs_for_a_spawned_child():
    with pytest.raises(ValueError, match="valid agent_ref"):
        SpawnResult(text="started", child_run_id="internal-child")
    with pytest.raises(ValueError, match="both be present"):
        SpawnResult(
            text="started",
            child_run_id="internal-child",
            child_session_id="internal-session",
            agent_ref=_agent_ref("task", "internal-child"),
        )


@pytest.mark.asyncio
async def test_research_offers_scratchpad_and_writes_spawn_provenance(session_store: SessionStore, monkeypatch):
    monkeypatch.setattr(research_module, "generate_slug", lambda _: "fun-panda")
    captured = {}
    registry = ToolExecutor().registry

    async def spawn_fn(ctx, task, **kwargs):
        captured.update(kwargs)
        agent_ref = _agent_ref("x", "child-1")
        return SpawnResult(
            text="Started a background agent to: x",
            child_run_id="child-1",
            child_session_id="test::c1",
            agent_ref=agent_ref,
            child_session_ref=agent_ref,
            wait=False,
            status="running",
        )

    ctx = _context(SharedLedger(), registry=registry, spawn_fn=spawn_fn)
    ctx.services["store"] = session_store
    execution = ToolExecution(tool_id="research-1", tool_name="research", ctx=ctx)

    result = await research_module.research(execution, research_module.ResearchInput(task="x", depth="normal"))

    # The scratchpad tools reach the child via the research AgentType profile's
    # extra_tools (the spawner builds the actual toolset from the profile).
    assert set(captured["extra_tools"]) >= SCRATCHPAD_TOOL_NAMES
    assert result.data is not None
    assert result.data["research_scope_id"] == "research-fun-panda"
    assert "research_tool_call_id" not in result.data
    assert "research-fun-panda" in result.data["artifact_dir"]
    # Spawn-time provenance only — no usage, workspace, artifacts, or child
    # tool_call_ids: the agent has not run yet.
    assert result.data["provenance"]["query"] == "x"
    assert result.data["provenance"]["derivation"] == {
        "agent_ref": _agent_ref("x", "child-1"),
    }
    assert result.data["provenance"]["workspace_ref"] == "research-fun-panda:_provenance.json"
    assert "tool_call_id" not in json.dumps(result.data["provenance"])
    assert "usage" not in result.data
    assert "artifacts" not in result.data
    assert "research_workspace" not in result.data
    persisted = await research_artifacts_module._get_fs_artifact("research-fun-panda", "_provenance.json")
    assert persisted is not None
    assert json.loads(persisted)["derivation"]["agent_ref"] == _agent_ref("x", "child-1")


def _context(
    ledger: SharedLedger | None = None,
    *,
    registry: ToolRegistry | None = None,
    spawn_fn=None,
    research_scope_id: str | None = None,
) -> ToolContext:
    registry = registry or ToolRegistry()
    ctx = ToolContext(
        session_state=SessionState(session_id="test", started_at=datetime.now(UTC)),
        registry=registry,
        run=RunContext(run_id="run-1", current_depth=0, max_depth=3, research_scope_id=research_scope_id),
        io=IOBridge(),
        background_tasks=BackgroundTaskRegistry(session_id="test"),
        ledger=ledger,
    )
    ctx.spawn_fn = spawn_fn
    return ctx


def test_default_agent_tool_schemas_hide_research_ledger_helpers():
    tool_names = {schema["function"]["name"] for schema in ToolExecutor().get_tools()}

    assert "research" in tool_names
    assert "research_note" not in tool_names
    assert "research_outline" not in tool_names
    assert "research_cover" not in tool_names
    assert not (HARNESS_TOOL_NAMES & tool_names)


@pytest.mark.asyncio
async def test_research_spawns_child_with_research_ledger_helpers(monkeypatch):
    monkeypatch.setattr(research_module, "generate_slug", lambda _: "fun-panda")
    captured = {}
    registry = ToolExecutor().registry
    ledger = SharedLedger()

    async def spawn_fn(ctx, task, **kwargs):
        captured.update(kwargs)
        agent_ref = _agent_ref("inspect research behavior", "agent-research-1")
        return SpawnResult(
            text="Started a background agent to: inspect research behavior",
            child_run_id="agent-research-1",
            child_session_id="test::r1",
            agent_ref=agent_ref,
            child_session_ref=agent_ref,
            parent_tool_call_id="research-1",
            agent_type="research",
            wait=False,
            status="running",
        )

    execution = ToolExecution(
        tool_id="research-1",
        tool_name="research",
        ctx=_context(ledger, registry=registry, spawn_fn=spawn_fn),
    )

    result = await research_module.research(
        execution,
        research_module.ResearchInput(task="inspect research behavior", depth="normal"),
    )

    # Detached contract: the tool returns a receipt with the child session
    # address; the report is delivered later as a hidden completion message.
    assert _agent_ref("inspect research behavior", "agent-research-1") in result.content
    assert "test::r1" not in result.content
    assert "delivered here automatically" in result.content
    # research now hands the spawner a PROFILE (capability + ledger tools + spawn-tool
    # excludes), not a pre-built tool list — the spawner builds the toolset from it.
    assert "tools" not in captured
    assert captured["scope"] == tools.read
    assert {"agent_cancel", "workflow"} <= captured["exclude_tools"]
    assert captured["agent_type"] == "research"
    assert captured["wait"] is False
    assert result.data is not None
    assert result.data["child_agent"] == {
        "agent_ref": _agent_ref("inspect research behavior", "agent-research-1"),
        "session_ref": _agent_ref("inspect research behavior", "agent-research-1"),
        "agent_type": "research",
        "wait": False,
        "status": "running",
    }
    assert (
        set(captured["extra_tools"])
        == {"research_outline", "research_cover"} | SCRATCHPAD_TOOL_NAMES | HARNESS_TOOL_NAMES
    )
    assert captured["research_scope_id"] == "research-fun-panda"
    assert result.data["research_scope_id"] == "research-fun-panda"
    assert "research_tool_call_id" not in result.data


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("depth", "max_depth", "expect_research_excluded"),
    [("quick", 3, True), ("normal", 3, False), ("normal", 2, True)],
)
async def test_research_depth_gate_excludes_nested_research(depth, max_depth, expect_research_excluded):
    # Runtime-only logic that stays in research(): forbid nested research when the
    # request is shallow (quick) or the remaining nesting depth is exhausted.
    captured = {}

    async def spawn_fn(ctx, task, **kwargs):
        captured.update(kwargs)
        agent_ref = _agent_ref(task, "agent-depth")
        return SpawnResult(
            text="done",
            child_run_id="agent-depth",
            child_session_id="test::depth",
            agent_ref=agent_ref,
            child_session_ref=agent_ref,
            wait=False,
            status="running",
        )

    ctx = _context(SharedLedger(), spawn_fn=spawn_fn)
    ctx.run.max_depth = max_depth
    execution = ToolExecution(tool_id="research-1", tool_name="research", ctx=ctx)

    await research_module.research(execution, research_module.ResearchInput(task="x", depth=depth))

    assert ("research" in captured["exclude_tools"]) is expect_research_excluded


@pytest.mark.asyncio
async def test_research_at_agent_cap_returns_conflict():
    async def spawn_fn(ctx, task, **kwargs):
        return SpawnResult(
            text="Not started — already at the limit of 16 concurrent background agents in this session.",
            wait=False,
            status="failed",
        )

    ctx = _context(SharedLedger(), spawn_fn=spawn_fn)
    execution = ToolExecution(tool_id="research-1", tool_name="research", ctx=ctx)

    result = await research_module.research(execution, research_module.ResearchInput(task="x", depth="normal"))

    assert result.is_error
    assert result.outcome.error.code == "conflict"
    assert "already at the limit" in result.content


@pytest.mark.asyncio
async def test_research_rejects_a_success_receipt_without_public_refs():
    async def spawn_fn(ctx, task, **kwargs):
        return SpawnResult(text="started", wait=False, status="running")

    ctx = _context(SharedLedger(), spawn_fn=spawn_fn)
    execution = ToolExecution(tool_id="research-1", tool_name="research", ctx=ctx)

    with pytest.raises(RuntimeError, match="missing its agent/session ref"):
        await research_module.research(execution, research_module.ResearchInput(task="x", depth="normal"))


class _ToolInput(BaseModel):
    q: str = ""


async def _noop_tool(execution, args):
    return ToolResult(content="", preview="")


def _action_tool(action: ToolAction):
    return tool(
        description="t",
        input_model=_ToolInput,
        policy=ToolPolicy(action=action, scope=ToolScope.INTERNAL),
        execute=_noop_tool,
    )


class _CapExecutor:
    def __init__(self, registry: ToolRegistry):
        self.registry = registry

    @property
    def tool_services(self):
        return {}

    def get_tools(self, **kwargs):
        return self.registry.get_schemas(**kwargs)

    def with_registry(self, registry: ToolRegistry):
        return _CapExecutor(registry)


def _base_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register("read_tool", _action_tool(ToolAction.READ), source="_system")
    registry.register("write_tool", _action_tool(ToolAction.WRITE), source="_system")
    registry.register("workflow", _action_tool(ToolAction.READ), source="_system")
    return registry


def _spawn_parent_ctx(registry: ToolRegistry) -> ToolContext:
    class SessionService:
        async def provision_state(self, _state, _messages):
            return None

        async def save(self, _state, _messages):
            return None

    ctx = ToolContext(
        session_state=SessionState(session_id="parent", started_at=datetime.now(UTC)),
        registry=registry,
        run=RunContext(run_id="run", current_depth=0, max_depth=3),
        io=IOBridge(),
        services={"session": SessionService()},
        background_tasks=BackgroundTaskRegistry(session_id="parent"),
    )
    ctx.spawn_fn = create_spawn_fn(executor=_CapExecutor(registry), model="test-model", max_depth=3, current_depth=0)
    return ctx


@pytest.mark.asyncio
async def test_research_profile_builds_read_only_child_toolset(monkeypatch):
    # End to end through the REAL spawner: the research AgentType profile yields a
    # read-only child toolset that includes the ledger helpers and excludes the
    # write tool (by capability) and the spawn tools (by name). This is the behavior
    # that used to be assembled inline in research().
    captured = {}

    class FakeAgent:
        def __init__(self, **kwargs):
            self.hooks = AgentHooks()
            captured.update(kwargs)

        async def stream(self, messages):
            yield Result(text="done", stop_reason=StopReason.END_TURN, steps=1, usage=Usage())

    monkeypatch.setattr("arden.core.spawner.Agent", FakeAgent)

    parent_ctx = _spawn_parent_ctx(_base_registry())
    profile = apply_profile(research_module.RESEARCH_AGENT_TYPE, system_prompt="prompt")
    await parent_ctx.spawn_fn(parent_ctx, task="research it", agent_type="research", **profile)

    names = {schema["function"]["name"] for schema in captured["tools"]}
    assert "read_tool" in names
    assert names >= SCRATCHPAD_TOOL_NAMES
    assert {"research_outline", "research_cover"} <= names
    assert names >= HARNESS_TOOL_NAMES
    assert "write_tool" not in names  # WRITE filtered by scope=tools.read
    assert "workflow" not in names


@pytest.mark.asyncio
async def test_nested_research_profile_does_not_double_register_ledger_tools(monkeypatch):
    # Nested research: the ledger tools are already in the registry. research still
    # passes its full extra_tools, and the spawner injects only the missing ones —
    # so copy_with never re-registers a duplicate (which would raise), and the child
    # still sees the ledger helpers exactly once.
    captured = {}

    class FakeAgent:
        def __init__(self, **kwargs):
            self.hooks = AgentHooks()
            captured.update(kwargs)

        async def stream(self, messages):
            yield Result(text="done", stop_reason=StopReason.END_TURN, steps=1, usage=Usage())

    monkeypatch.setattr("arden.core.spawner.Agent", FakeAgent)

    registry = _base_registry().copy_with(dict(research_module.RESEARCH_AGENT_TOOLS))
    parent_ctx = _spawn_parent_ctx(registry)
    profile = apply_profile(research_module.RESEARCH_AGENT_TYPE, system_prompt="prompt")
    result = await parent_ctx.spawn_fn(parent_ctx, task="nested research", agent_type="research", **profile)

    assert result.text == "done"
    names = {schema["function"]["name"] for schema in captured["tools"]}
    assert {"research_outline", "research_cover"} <= names
    assert names >= SCRATCHPAD_TOOL_NAMES
    assert names >= HARNESS_TOOL_NAMES
    assert "read_tool" in names
    assert "write_tool" not in names


@pytest.mark.asyncio
async def test_spawner_carries_child_tool_calls_and_source_refs(monkeypatch):
    source = ToolSourceRef(provider="slack", kind="message", ref="C1:1", title="Evidence")

    class FakeAgent:
        def __init__(self, **kwargs):
            self.hooks = AgentHooks()

        async def stream(self, messages):
            yield ToolStarted(
                depth=1,
                parent_id="parent-call",
                tool_id="child-call-1",
                name="read_tool",
                args={},
                display_name="Read",
            )
            yield ToolCompleted(
                depth=1,
                parent_id="parent-call",
                tool_id="child-call-1",
                name="read_tool",
                result="evidence",
                preview="evidence",
                duration_ms=1,
                is_error=False,
                data=None,
                display_name="Read",
                source_refs=(source,),
            )
            yield Result(text="done", stop_reason=StopReason.END_TURN, steps=1, usage=Usage())

    monkeypatch.setattr("arden.core.spawner.Agent", FakeAgent)
    parent_ctx = _spawn_parent_ctx(_base_registry())

    result = await parent_ctx.spawn_fn(
        parent_ctx,
        task="trace evidence",
        parent_id="parent-call",
        system_prompt="Read evidence.",
    )

    assert result.tool_call_ids == ("child-call-1",)
    assert result.source_refs == (source,)
    assert result.child_agent_data()["source_refs"] == [source.to_dict()]


@pytest.mark.asyncio
async def test_research_harness_tools_populate_scoped_workspace():
    ledger = SharedLedger()
    ctx = _context(ledger, research_scope_id="research-a")

    await research_module.research_track_search(
        ToolExecution(tool_id="s", tool_name="research_track_search", ctx=ctx),
        research_module.ResearchSearchInput(query="research agent harness"),
    )
    await research_module.research_track_source(
        ToolExecution(tool_id="src", tool_name="research_track_source", ctx=ctx),
        research_module.ResearchSourceInput(
            id="paper", title="Harness paper", locator="https://example.test/paper", status="read"
        ),
    )
    await research_module.research_curate(
        ToolExecution(tool_id="cur", tool_name="research_curate", ctx=ctx),
        research_module.ResearchCurateInput(
            claim="Harnesses externalize research state.",
            source="paper",
            quote="stateful harness",
            importance="high",
            confidence="high",
        ),
    )
    await research_module.research_verify_claim(
        ToolExecution(tool_id="ver", tool_name="research_verify_claim", ctx=ctx),
        research_module.ResearchVerifyClaimInput(
            claim="Harnesses externalize research state.",
            verdict="supported",
            sources=["paper"],
            rationale="Directly described by the source.",
        ),
    )
    await research_module.research_question(
        ToolExecution(tool_id="q", tool_name="research_question", ctx=ctx),
        research_module.ResearchQuestionInput(question="How much UI work is needed?", status="open"),
    )

    summary = ledger.workspace_summary(scope="research-a")
    assert summary is not None
    assert summary["search_history"] == ["research agent harness"]
    assert summary["sources"][0]["id"] == "paper"
    assert summary["evidence"][0]["importance"] == "high"
    assert summary["verifications"][0]["verdict"] == "supported"
    assert summary["questions"][0]["status"] == "open"


@pytest.mark.asyncio
async def test_research_outline_and_cover_track_gap_notes():
    assert hasattr(research_module, "research_outline")
    assert hasattr(research_module, "research_cover")
    ledger = SharedLedger()

    await research_module.research_outline(
        ToolExecution(tool_id="outline-1", tool_name="research_outline", ctx=_context(ledger)),
        research_module.ResearchOutlineInput(sections=["Repo state", "Prompt behavior"]),
    )
    result = await research_module.research_cover(
        ToolExecution(tool_id="cover-1", tool_name="research_cover", ctx=_context(ledger)),
        research_module.ResearchCoverInput(
            section="Repo state",
            source="apps/server/arden/tools/research.py",
        ),
    )

    report = ledger.coverage_report()
    gaps = ledger.add_coverage_gap_notes()

    assert result.preview == "Coverage 50%"
    assert report is not None
    assert report.gaps == ["Prompt behavior"]
    assert [note.what_missing for note in gaps] == ["No source covered outline section: Prompt behavior"]


@pytest.mark.asyncio
async def test_research_outline_coverage_is_scoped_per_research_agent():
    ledger = SharedLedger()

    await research_module.research_outline(
        ToolExecution(
            tool_id="outline-1",
            tool_name="research_outline",
            ctx=_context(ledger, research_scope_id="research-a"),
        ),
        research_module.ResearchOutlineInput(sections=["Repo state", "Prompt behavior"]),
    )
    await research_module.research_outline(
        ToolExecution(
            tool_id="outline-2",
            tool_name="research_outline",
            ctx=_context(ledger, research_scope_id="research-b"),
        ),
        research_module.ResearchOutlineInput(sections=["Vault notes"]),
    )
    await research_module.research_cover(
        ToolExecution(
            tool_id="cover-1",
            tool_name="research_cover",
            ctx=_context(ledger, research_scope_id="research-a"),
        ),
        research_module.ResearchCoverInput(section="Repo state", source="apps/server/arden/tools/research.py"),
    )

    report_a = ledger.coverage_report(scope="research-a")
    report_b = ledger.coverage_report(scope="research-b")
    assert report_a is not None
    assert report_b is not None
    assert report_a.gaps == ["Prompt behavior"]
    assert report_b.gaps == ["Vault notes"]


@pytest.mark.asyncio
async def test_research_prompt_names_canonical_harness_tools():
    ledger = SharedLedger()
    await ledger.register("other-research", "inspect existing behavior", depth="normal")
    ctx = _context(ledger)

    prompt = await research_module._build_research_prompt(ctx, "normal", 2, "research-1")

    assert "research_note" not in prompt
    assert "research_outline" in prompt
    assert "research_cover" in prompt
    assert "research_curate" in prompt
    assert "research_verify_claim" in prompt
    assert "research_track_source" in prompt
    assert "open questions, gaps, and dead ends" in prompt
    assert "Do not hide unsupported claims" in prompt
    assert "TL;DR plus artifact manifest" in prompt


def test_research_depth_rejects_unknown_value():
    with pytest.raises(ValidationError):
        research_module.ResearchInput(task="x", depth="maximum")


def test_research_state_mutations_have_honest_policies():
    assert research_module.research_tool.policy.action == ToolAction.EXECUTE
    for name, registered in research_module.RESEARCH_AGENT_TOOLS.items():
        expected = ToolAction.READ if name.startswith(("read_", "list_")) else ToolAction.WRITE
        assert registered.policy.action == expected
