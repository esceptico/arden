from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio
from pydantic import BaseModel

import ntrp.database as database
import ntrp.tools.research as research_module
import ntrp.tools.research_artifacts as research_artifacts_module
from ntrp.agent import Result, SharedLedger, StopReason, Usage
from ntrp.context.models import SessionState
from ntrp.context.store import SessionStore
from ntrp.core.agent_types import apply_profile
from ntrp.core.spawner import SpawnResult, create_spawn_fn
from ntrp.tools.core import ToolAction, ToolPolicy, ToolResult, ToolScope, tool
from ntrp.tools.core.context import BackgroundTaskRegistry, IOBridge, RunContext, ToolContext, ToolExecution
from ntrp.tools.core.registry import ToolRegistry
from ntrp.tools.executor import ToolExecutor


@pytest.fixture(autouse=True)
def _isolate_research_artifacts(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(research_artifacts_module, "NTRP_DIR", tmp_path / ".ntrp")


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

HARNESS_TOOL_NAMES = {
    "research_track_source",
    "research_curate",
    "research_verify_claim",
    "research_question",
    "research_track_search",
}


def test_scratchpad_tools_are_research_only():
    from ntrp.integrations.core import CORE_INTEGRATIONS

    assert set(research_module.RESEARCH_AGENT_TOOLS) >= SCRATCHPAD_TOOL_NAMES | HARNESS_TOOL_NAMES
    main_tool_names = {name for integ in CORE_INTEGRATIONS for name in integ.tools}
    assert not ((SCRATCHPAD_TOOL_NAMES | HARNESS_TOOL_NAMES) & main_tool_names)


@pytest.mark.asyncio
async def test_research_offers_scratchpad_and_returns_artifact_manifest(session_store: SessionStore, monkeypatch):
    monkeypatch.setattr(research_module, "generate_slug", lambda _: "fun-panda")
    captured = {}
    registry = ToolExecutor().registry

    async def spawn_fn(ctx, task, **kwargs):
        captured.update(kwargs)
        scope = kwargs["research_scope_id"]
        await session_store.put_research_artifact(scope_id=scope, path="inv.md", content="big inventory")
        assert ctx.ledger is not None
        ctx.ledger.add_workspace_evidence(
            research_module.CuratedEvidence(claim="important finding", source="inv.md", importance="high"),
            scope=scope,
        )
        return SpawnResult(text="done")

    ctx = _context(SharedLedger(), registry=registry, spawn_fn=spawn_fn)
    ctx.services["store"] = session_store
    execution = ToolExecution(tool_id="research-1", tool_name="research", ctx=ctx)

    result = await research_module.research(execution, research_module.ResearchInput(task="x", depth="normal"))

    # The scratchpad tools reach the child via the research AgentType profile's
    # extra_tools (the spawner builds the actual toolset from the profile).
    assert set(captured["extra_tools"]) >= SCRATCHPAD_TOOL_NAMES
    assert result.data is not None
    assert result.data["artifacts"][0]["path"] == "inv.md"
    assert result.data["artifacts"][0]["bytes"] == len(b"big inventory")
    assert result.data["artifacts"][0]["preview"] == "big inventory"
    assert result.data["research_scope_id"] == "research-fun-panda"
    assert result.data["research_tool_call_id"] == "research-1"
    assert result.data["artifacts"][0]["scope_id"] == "research-fun-panda"
    assert "research-fun-panda" in result.data["artifact_dir"]
    assert result.data["research_workspace"]["evidence"][0]["claim"] == "important finding"


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
        return SpawnResult(
            text="done",
            child_run_id="agent-research-1",
            parent_tool_call_id="research-1",
            agent_type="research",
            wait=True,
            status="completed",
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

    assert result.content == "done"
    # research now hands the spawner a PROFILE (capability + ledger tools + spawn-tool
    # excludes), not a pre-built tool list — the spawner builds the toolset from it.
    assert "tools" not in captured
    assert captured["actions"] == frozenset({ToolAction.READ})
    assert {"background", "workflow"} <= captured["exclude_tools"]
    assert captured["agent_type"] == "research"
    assert captured["wait"] is True
    assert result.data is not None
    assert result.data["child_agent"] == {
        "child_run_id": "agent-research-1",
        "parent_tool_call_id": "research-1",
        "agent_type": "research",
        "wait": True,
        "status": "completed",
    }
    assert (
        set(captured["extra_tools"])
        == {
            "research_note",
            "research_outline",
            "research_cover",
        }
        | SCRATCHPAD_TOOL_NAMES
        | HARNESS_TOOL_NAMES
    )
    assert captured["research_scope_id"] == "research-fun-panda"
    assert result.data["research_scope_id"] == "research-fun-panda"
    assert result.data["research_tool_call_id"] == "research-1"


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
        return SpawnResult(text="done")

    ctx = _context(SharedLedger(), spawn_fn=spawn_fn)
    ctx.run.max_depth = max_depth
    execution = ToolExecution(tool_id="research-1", tool_name="research", ctx=ctx)

    await research_module.research(execution, research_module.ResearchInput(task="x", depth=depth))

    assert ("research" in captured["exclude_tools"]) is expect_research_excluded


class _ToolInput(BaseModel):
    q: str = ""


async def _noop_tool(execution, args):
    return ToolResult(content="", preview="")


def _action_tool(action: ToolAction):
    return tool(description="t", input_model=_ToolInput, policy=ToolPolicy(action=action, scope=ToolScope.INTERNAL), execute=_noop_tool)


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
    registry.register("background", _action_tool(ToolAction.READ), source="_system")
    registry.register("workflow", _action_tool(ToolAction.READ), source="_system")
    return registry


def _spawn_parent_ctx(registry: ToolRegistry) -> ToolContext:
    ctx = ToolContext(
        session_state=SessionState(session_id="parent", started_at=datetime.now(UTC)),
        registry=registry,
        run=RunContext(run_id="run", current_depth=0, max_depth=3),
        io=IOBridge(),
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
            captured.update(kwargs)

        async def stream(self, messages):
            yield Result(text="done", stop_reason=StopReason.END_TURN, steps=1, usage=Usage())

    monkeypatch.setattr("ntrp.core.spawner.Agent", FakeAgent)

    parent_ctx = _spawn_parent_ctx(_base_registry())
    profile = apply_profile(research_module.RESEARCH_AGENT_TYPE, system_prompt="prompt")
    await parent_ctx.spawn_fn(parent_ctx, task="research it", agent_type="research", **profile)

    names = {schema["function"]["name"] for schema in captured["tools"]}
    assert "read_tool" in names
    assert names >= SCRATCHPAD_TOOL_NAMES
    assert {"research_note", "research_outline", "research_cover"} <= names
    assert names >= HARNESS_TOOL_NAMES
    assert "write_tool" not in names  # WRITE filtered by actions={READ}
    assert "background" not in names  # excluded by the research spawn-tool set
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
            captured.update(kwargs)

        async def stream(self, messages):
            yield Result(text="done", stop_reason=StopReason.END_TURN, steps=1, usage=Usage())

    monkeypatch.setattr("ntrp.core.spawner.Agent", FakeAgent)

    registry = _base_registry().copy_with(dict(research_module.RESEARCH_AGENT_TOOLS))
    parent_ctx = _spawn_parent_ctx(registry)
    profile = apply_profile(research_module.RESEARCH_AGENT_TYPE, system_prompt="prompt")
    result = await parent_ctx.spawn_fn(parent_ctx, task="nested research", agent_type="research", **profile)

    assert result.text == "done"
    names = {schema["function"]["name"] for schema in captured["tools"]}
    assert {"research_note", "research_outline", "research_cover"} <= names
    assert names >= SCRATCHPAD_TOOL_NAMES
    assert names >= HARNESS_TOOL_NAMES
    assert "read_tool" in names
    assert "write_tool" not in names


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
        research_module.ResearchSourceInput(id="paper", title="Harness paper", locator="https://example.test/paper", status="read"),
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
async def test_research_note_records_fact_in_shared_ledger():
    assert hasattr(research_module, "research_note")
    ledger = SharedLedger()
    execution = ToolExecution(tool_id="note-1", tool_name="research_note", ctx=_context(ledger))

    result = await research_module.research_note(
        execution,
        research_module.ResearchNoteInput(
            kind="fact",
            claim="ntrp research agents can spawn child agents.",
            source="apps/server/ntrp/tools/research.py",
            quote="Spawn a research agent",
        ),
    )

    assert result.preview == "Recorded fact"
    assert [note.kind for note in ledger.notes] == ["fact"]
    assert ledger.notes[0].source == "apps/server/ntrp/tools/research.py"


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
            source="apps/server/ntrp/tools/research.py",
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
        research_module.ResearchCoverInput(section="Repo state", source="apps/server/ntrp/tools/research.py"),
    )

    report_a = ledger.coverage_report(scope="research-a")
    report_b = ledger.coverage_report(scope="research-b")
    assert report_a is not None
    assert report_b is not None
    assert report_a.gaps == ["Prompt behavior"]
    assert report_b.gaps == ["Vault notes"]


@pytest.mark.asyncio
async def test_research_prompt_names_note_and_coverage_tools():
    ledger = SharedLedger()
    await ledger.register("other-research", "inspect existing behavior", depth="normal")
    ctx = _context(ledger)

    prompt = await research_module._build_research_prompt(ctx, "normal", 2, "research-1")

    assert "research_note" in prompt
    assert "research_outline" in prompt
    assert "research_cover" in prompt
    assert "research_curate" in prompt
    assert "research_verify_claim" in prompt
    assert "research_track_source" in prompt
    assert "facts/dead ends/contradictions/gaps" in prompt
    assert "Do not hide unsupported claims" in prompt
    assert "TL;DR plus artifact manifest" in prompt
