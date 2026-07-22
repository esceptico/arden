from types import SimpleNamespace

import pytest
from pydantic import BaseModel, ValidationError

from arden.agent import RunBudget
from arden.orchestra.dynamic import format_script_traceback, run_script
from arden.orchestra.engine import (
    Orchestra,
    TokenBudget,
    WorkflowBudgetExceeded,
    WorkflowSpawnLimit,
    WorkflowStructuredOutputMissing,
)
from arden.orchestra.schema import model_from_schema
from arden.tools.core.types import ToolAction


class _Schema(BaseModel):
    value: int


def _ctx_with(responses: list[str], budget: RunBudget | None = None, structured=None):
    """Fake spawn_fn plus direct formatter. `structured` is formatter JSON:
    dict, per-formatter list, or None."""
    calls = {"i": 0, "format_i": 0}

    async def spawn_fn(ctx, *, task, **kwargs):
        i = calls["i"]
        calls["i"] += 1
        text = responses[i] if i < len(responses) else responses[-1]
        return SimpleNamespace(text=text)

    async def format_structured(*, response_format, **kwargs):
        i = calls["format_i"]
        calls["format_i"] += 1
        arg = structured[i] if isinstance(structured, list) else structured
        return response_format(**arg).model_dump_json() if arg is not None else "not json"

    ctx = SimpleNamespace(spawn_fn=spawn_fn, format_structured=format_structured)
    if budget is not None:
        ctx.run = SimpleNamespace(budget=budget)
    return ctx, calls


def test_model_from_schema_dict_roundtrip():
    Model = model_from_schema({"clusters": [{"title": "str", "n": "int"}]})
    m = Model(clusters=[{"title": "a", "n": 2}])
    assert m.model_dump() == {"clusters": [{"title": "a", "n": 2}]}
    with pytest.raises(ValidationError):
        Model(clusters="not a list")


def test_model_from_schema_rejects_unknown_leaf():
    with pytest.raises(ValueError, match="unknown leaf type"):
        model_from_schema({"x": "datetime"})


async def test_agent_returns_text_without_schema():
    ctx, _ = _ctx_with(["hello world"])
    o = Orchestra.for_ctx(ctx)
    assert await o.agent("say hi") == "hello world"


async def test_agent_returns_structured_output_model():
    # A pydantic schema -> the validated formatter model instance.
    ctx, _ = _ctx_with(["done"], structured={"value": 42})
    o = Orchestra.for_ctx(ctx)
    result = await o.agent("give me value", schema=_Schema)
    assert isinstance(result, _Schema)
    assert result.value == 42


async def test_agent_returns_structured_output_dict():
    # A dict schema -> a plain dict (existing contract preserved).
    ctx, _ = _ctx_with(["done"], structured={"facts": ["a", "b"]})
    o = Orchestra.for_ctx(ctx)
    result = await o.agent("research", schema={"facts": ["str"]})
    assert result == {"facts": ["a", "b"]}


async def test_agent_repairs_once_when_formatter_output_is_invalid():
    ctx, calls = _ctx_with(["worker"], structured=[None, {"value": 7}])
    o = Orchestra.for_ctx(ctx)
    result = await o.agent("give me value", schema=_Schema)
    assert result.value == 7
    assert calls["i"] == 1
    assert calls["format_i"] == 2  # formatter + repair


async def test_agent_raises_when_formatter_never_returns_valid_json():
    ctx, calls = _ctx_with(["worker"], structured=None)
    o = Orchestra.for_ctx(ctx)
    with pytest.raises(WorkflowStructuredOutputMissing):
        await o.agent("give me value", schema=_Schema)
    assert calls["i"] == 1
    assert calls["format_i"] == 2  # formatter + repair
    assert o.spawn_count == 1


async def test_schema_worker_tool_allowlist_is_not_polluted_by_formatter_step():
    captured = {}

    async def spawn_fn(ctx, *, task, tools=None, **kwargs):
        captured["worker_tools"] = tools
        return SimpleNamespace(text="worker answer")

    async def format_structured(*, response_format, **kwargs):
        captured["formatted"] = True
        return response_format(value=1).model_dump_json()

    ctx = SimpleNamespace(spawn_fn=spawn_fn, format_structured=format_structured)
    o = Orchestra.for_ctx(ctx)
    await o.agent("x", schema=_Schema, tools=["slack_search"])
    assert captured["worker_tools"] == ["slack_search"]
    assert captured["formatted"] is True


async def test_parallel_reraises_missing_structured_output():
    # A missing structured formatter result is a contract violation — it aborts the fan-out,
    # not swallowed to None.
    ctx, _ = _ctx_with(["worker"], structured=None)
    o = Orchestra.for_ctx(ctx)
    with pytest.raises(BaseException) as ei:
        await o.parallel([(lambda: o.agent("x", schema=_Schema))])
    exc = ei.value
    flat = list(exc.exceptions) if isinstance(exc, BaseExceptionGroup) else [exc]
    assert any(isinstance(e, WorkflowStructuredOutputMissing) for e in flat)


async def test_parallel_preserves_order_and_isolates_failures():
    async def ok(v):
        return v

    async def boom():
        raise RuntimeError("nope")

    ctx, _ = _ctx_with(["x"])
    o = Orchestra.for_ctx(ctx)
    results = await o.parallel([(lambda: ok(1)), (lambda: boom()), (lambda: ok(3))])
    assert results == [1, None, 3]


async def test_pipeline_runs_all_stages_per_item():
    ctx, _ = _ctx_with(["x"])
    o = Orchestra.for_ctx(ctx)

    async def stage1(prev, item, i):
        return item * 2

    async def stage2(prev, item, i):
        return prev + 1

    results = await o.pipeline([1, 2, 3], stage1, stage2)
    assert results == [3, 5, 7]


async def test_pipeline_drops_item_on_none_stage():
    ctx, _ = _ctx_with(["x"])
    o = Orchestra.for_ctx(ctx)

    async def stage1(prev, item, i):
        return None if item == 2 else item

    async def stage2(prev, item, i):
        return prev * 10

    results = await o.pipeline([1, 2, 3], stage1, stage2)
    assert results == [10, None, 30]


async def test_pipeline_isolates_stage_exceptions():
    ctx, _ = _ctx_with(["x"])
    o = Orchestra.for_ctx(ctx)

    async def stage1(prev, item, i):
        if item == 2:
            raise RuntimeError("boom")
        return item

    results = await o.pipeline([1, 2, 3], stage1)
    assert results == [1, None, 3]


async def test_parallel_spawns_get_unique_lifecycle_ids():
    seen = []

    async def spawn_fn(ctx, *, task, lifecycle_id=None, **kwargs):
        seen.append(lifecycle_id)
        return SimpleNamespace(text="ok")

    ctx = SimpleNamespace(spawn_fn=spawn_fn)
    o = Orchestra.for_ctx(ctx, parent_id="tool-1", workflow_id="wf-1")
    await o.parallel([(lambda: o.agent("a")), (lambda: o.agent("b")), (lambda: o.agent("c"))])
    assert len(seen) == 3
    assert len(set(seen)) == 3
    assert all(s and s.startswith("tool-1:") for s in seen)


async def test_agent_forwards_tool_name_allowlist():
    # The script writes tool NAMES; the engine forwards them verbatim and the
    # spawner resolves them against the full toolset (see core/spawner.py).
    captured = {}

    async def spawn_fn(ctx, *, task, tools=None, **kwargs):
        captured["tools"] = tools
        return SimpleNamespace(text="ok")

    ctx = SimpleNamespace(spawn_fn=spawn_fn)
    o = Orchestra.for_ctx(ctx)
    await o.agent("do x", tools=["slack_search", "read_file"])
    assert captured["tools"] == ["slack_search", "read_file"]


async def test_workflow_agents_exclude_spawn_tools():
    captured = {}

    async def spawn_fn(ctx, *, task, exclude_tools=None, **kwargs):
        captured["exclude"] = exclude_tools
        return SimpleNamespace(text="ok")

    ctx = SimpleNamespace(spawn_fn=spawn_fn)
    o = Orchestra.for_ctx(ctx, parent_id="t", workflow_id="w")
    await o.agent("do thing")
    assert captured["exclude"] is not None
    assert {"workflow", "research", "background"} <= set(captured["exclude"])


async def test_agent_type_threads_capability_and_persona():
    # A read-only persona resolves to actions={READ} + the persona prompt + the
    # agent_type label, none of which the script wrote.
    captured = {}

    async def spawn_fn(ctx, *, task, actions=None, system_prompt=None, agent_type=None, **kwargs):
        captured.update(actions=actions, system_prompt=system_prompt, agent_type=agent_type)
        return SimpleNamespace(text="ok")

    ctx = SimpleNamespace(spawn_fn=spawn_fn)
    o = Orchestra.for_ctx(ctx)
    await o.agent("review this", agent_type="reviewer")
    assert captured["actions"] == frozenset({ToolAction.READ})
    assert "code reviewer" in captured["system_prompt"]
    assert captured["agent_type"] == "reviewer"


async def test_builder_agent_type_is_full_capability():
    # The capability axis is a set of actions, so a write-capable builder is just
    # actions=None (full) — read-only is one value, not the axis.
    captured = {}

    async def spawn_fn(ctx, *, task, actions=None, **kwargs):
        captured["actions"] = actions
        return SimpleNamespace(text="ok")

    ctx = SimpleNamespace(spawn_fn=spawn_fn)
    o = Orchestra.for_ctx(ctx)
    await o.agent("build it", agent_type="builder")
    assert captured["actions"] is None


async def test_unknown_agent_type_raises_listing_options():
    ctx, _ = _ctx_with(["ok"])
    o = Orchestra.for_ctx(ctx)
    with pytest.raises(ValueError, match="unknown agent_type 'wizard'"):
        await o.agent("x", agent_type="wizard")


async def test_explicit_system_prompt_overrides_persona_but_keeps_capability():
    captured = {}

    async def spawn_fn(ctx, *, task, system_prompt=None, actions=None, **kwargs):
        captured.update(system_prompt=system_prompt, actions=actions)
        return SimpleNamespace(text="ok")

    ctx = SimpleNamespace(spawn_fn=spawn_fn)
    o = Orchestra.for_ctx(ctx)
    await o.agent("x", agent_type="reviewer", system_prompt="be a poet")
    assert captured["system_prompt"] == "be a poet"
    assert captured["actions"] == frozenset({ToolAction.READ})


async def test_agent_type_with_schema_keeps_capability_and_note():
    captured = {}

    async def spawn_fn(ctx, *, task, system_prompt=None, actions=None, **kwargs):
        captured.update(worker_prompt=system_prompt, worker_actions=actions)
        return SimpleNamespace(text="worker")

    async def format_structured(*, response_format, **kwargs):
        captured["formatted"] = True
        return response_format(value=1).model_dump_json()

    ctx = SimpleNamespace(spawn_fn=spawn_fn, format_structured=format_structured)
    o = Orchestra.for_ctx(ctx)
    out = await o.agent("review", agent_type="reviewer", schema=_Schema)
    assert out.value == 1
    assert captured["worker_actions"] == frozenset({ToolAction.READ})
    assert "code reviewer" in captured["worker_prompt"]
    assert captured["formatted"] is True


async def test_parallel_accepts_bare_coroutines():
    # parallel([agent(a), agent(b)]) — no lambdas needed.
    ctx, _ = _ctx_with(["a", "b"])
    o = Orchestra.for_ctx(ctx)
    results = await o.parallel([o.agent("x"), o.agent("y")])
    assert results == ["a", "b"]


async def test_run_script_fan_out_and_return():
    ctx, _ = _ctx_with(["hello", "world"])
    o = Orchestra.for_ctx(ctx)
    result = await run_script(o, "parts = await parallel([agent('a'), agent('b')])\nreturn parts", {})
    assert result == ["hello", "world"]


async def test_run_script_uses_args_and_dict_schema():
    ctx, _ = _ctx_with(["done"], structured={"facts": ["x"]})
    o = Orchestra.for_ctx(ctx)
    result = await run_script(o, "return await agent(args['q'], schema={'facts': ['str']})", {"q": "what?"})
    assert result == {"facts": ["x"]}


async def test_run_script_syntax_error_propagates():
    ctx, _ = _ctx_with(["x"])
    o = Orchestra.for_ctx(ctx)
    with pytest.raises(SyntaxError):
        await run_script(o, "this is not valid python !!!", {})


async def test_spawn_cap_aborts_parallel_instead_of_silent_none(monkeypatch):
    # The runaway guard must surface, not degrade to None the model misreads as a
    # partial failure.
    monkeypatch.setattr("arden.orchestra.engine._MAX_WORKFLOW_SPAWNS", 2)
    ctx, _ = _ctx_with(["ok"])
    o = Orchestra.for_ctx(ctx)
    with pytest.raises(BaseException) as ei:
        await o.parallel([o.agent("a"), o.agent("b"), o.agent("c"), o.agent("d")])
    exc = ei.value
    flat = list(exc.exceptions) if isinstance(exc, BaseExceptionGroup) else [exc]
    assert any(isinstance(e, WorkflowSpawnLimit) for e in flat)
    assert o.spawn_count == 2


async def test_run_script_runtime_error_points_at_script_line():
    ctx, _ = _ctx_with(["x"])
    o = Orchestra.for_ctx(ctx)
    script = "a = 1\nb = 2\nraise ValueError('boom')\nreturn a"
    with pytest.raises(ValueError) as ei:
        await run_script(o, script, {})
    tb = format_script_traceback(ei.value, script)
    assert "<workflow-script>" in tb
    assert "line 3" in tb  # the raise is on the model's line 3, not 4
    assert "raise ValueError('boom')" in tb  # source rendered from the passed script


async def test_format_script_traceback_renders_the_passed_script_not_shared_state():
    # Source comes from the script argument, not a process-global linecache, so
    # concurrent workflows can't clobber each other's traceback source.
    ctx, _ = _ctx_with(["x"])
    o = Orchestra.for_ctx(ctx)
    with pytest.raises(ValueError) as ea:
        await run_script(o, "raise ValueError('A')", {})
    with pytest.raises(ValueError) as eb:
        await run_script(o, "x = 1\nraise ValueError('B')", {})
    tb_a = format_script_traceback(ea.value, "raise ValueError('A')")
    tb_b = format_script_traceback(eb.value, "x = 1\nraise ValueError('B')")
    assert "raise ValueError('A')" in tb_a and "line 1" in tb_a
    assert "raise ValueError('B')" in tb_b and "line 2" in tb_b


async def test_spawn_cap_traceback_surfaces_runaway_message(monkeypatch):
    # When the cap fires inside parallel(), the TaskGroup wraps it in an
    # ExceptionGroup — the leaf runaway-guard message must still surface.
    monkeypatch.setattr("arden.orchestra.engine._MAX_WORKFLOW_SPAWNS", 1)
    ctx, _ = _ctx_with(["ok"])
    o = Orchestra.for_ctx(ctx)
    script = "return await parallel([agent('a'), agent('b'), agent('c')])"
    with pytest.raises(BaseException) as ei:
        await run_script(o, script, {})
    tb = format_script_traceback(ei.value, script)
    assert "runaway guard" in tb


def test_token_budget_view_reads_live():
    b = RunBudget(total=100, output_tokens=30)
    v = TokenBudget(b)
    assert v.total == 100
    assert v.spent() == 30
    assert v.remaining() == 70
    b.output_tokens = 120  # live re-read, clamped at 0
    assert v.spent() == 120
    assert v.remaining() == 0
    # No ceiling / no budget -> unbounded.
    assert TokenBudget(RunBudget(total=None, output_tokens=50)).remaining() == float("inf")
    none_view = TokenBudget(None)
    assert none_view.total is None and none_view.spent() == 0 and none_view.remaining() == float("inf")


async def test_spawn_denied_when_budget_exhausted():
    ctx, _ = _ctx_with(["ok"], budget=RunBudget(total=100, output_tokens=100))
    o = Orchestra.for_ctx(ctx)
    with pytest.raises(WorkflowBudgetExceeded):
        await o.agent("x")


async def test_budget_guard_aborts_parallel_instead_of_silent_none():
    ctx, _ = _ctx_with(["ok"], budget=RunBudget(total=50, output_tokens=50))
    o = Orchestra.for_ctx(ctx)
    with pytest.raises(BaseException) as ei:
        await o.parallel([o.agent("a"), o.agent("b")])
    exc = ei.value
    flat = list(exc.exceptions) if isinstance(exc, BaseExceptionGroup) else [exc]
    assert any(isinstance(e, WorkflowBudgetExceeded) for e in flat)


async def test_budget_readable_in_script():
    ctx, _ = _ctx_with(["x"], budget=RunBudget(total=100_000, output_tokens=10_000))
    o = Orchestra.for_ctx(ctx)
    result = await run_script(o, "return [budget.total, budget.spent(), budget.remaining()]", {})
    assert result == [100_000, 10_000, 90_000]


async def test_budget_unbounded_when_ctx_has_no_run():
    # The orchestra ctx without a run (and thus no budget) is a valid "no ceiling"
    # state — the script sees total None and infinite remaining.
    ctx, _ = _ctx_with(["x"])
    o = Orchestra.for_ctx(ctx)
    result = await run_script(o, "return [budget.total, budget.remaining() == float('inf')]", {})
    assert result == [None, True]


async def test_run_script_syntax_error_line_is_script_relative():
    ctx, _ = _ctx_with(["x"])
    o = Orchestra.for_ctx(ctx)
    with pytest.raises(SyntaxError) as ei:
        await run_script(o, "x = 1\nfor in range(3):\n    pass", {})
    assert ei.value.lineno == 2  # the bad line, not 3 (wrapper offset removed)


@pytest.mark.asyncio
async def test_agent_inherits_workflow_model_unless_overridden():
    seen: list[str | None] = []

    async def spawn_fn(ctx, *, task, model_override=None, **kwargs):
        seen.append(model_override)
        return SimpleNamespace(text="ok")

    ctx = SimpleNamespace(
        spawn_fn=spawn_fn,
        run=SimpleNamespace(budget=None, workflow_model="cheap-model"),
    )
    orchestra = Orchestra.for_ctx(ctx)

    await orchestra.agent("task one")
    await orchestra.agent("task two", model="strong-model")

    assert seen == ["cheap-model", "strong-model"]
