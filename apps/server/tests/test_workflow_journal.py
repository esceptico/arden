from types import SimpleNamespace

import pytest
import pytest_asyncio
from pydantic import BaseModel

import arden.database as database
from arden.agent import RunBudget
from arden.execution import InvocationStatus, InvocationStore
from arden.orchestra.engine import WORKFLOW_AGENT_PROMPT, Orchestra, WorkflowAgentFailed
from arden.orchestra.journal import WorkflowJournal, spawn_hash

_KEY = "sess-1:call-1:abc123"


class _Schema(BaseModel):
    value: int


@pytest_asyncio.fixture
async def store(tmp_path):
    conn = await database.connect(tmp_path / "sessions.db")
    store = InvocationStore(conn)
    await store.init_schema()
    yield store
    await conn.close()


def _journal(store: InvocationStore) -> WorkflowJournal:
    return WorkflowJournal(store, key=_KEY, run_id="run-1", session_id="sess-1", tool_call_id="call-1")


def _ctx(responses: list[str], budget: RunBudget | None = None, structured: dict | None = None, spend: int = 0):
    """Fake spawn_fn mirroring test_orchestra's: sequential canned responses,
    optional per-spawn budget spend, optional direct formatter."""
    calls = {"i": 0, "format_i": 0}

    async def spawn_fn(ctx, *, task, **kwargs):
        i = calls["i"]
        calls["i"] += 1
        if budget is not None and spend:
            budget.output_tokens += spend
        text = responses[i] if i < len(responses) else responses[-1]
        return SimpleNamespace(text=text, status="completed")

    async def format_structured(*, response_format, **kwargs):
        calls["format_i"] += 1
        return response_format(**structured).model_dump_json()

    ctx = SimpleNamespace(spawn_fn=spawn_fn)
    if structured is not None:
        ctx.format_structured = format_structured
    if budget is not None:
        ctx.run = SimpleNamespace(budget=budget)
    return ctx, calls


def _worker_id(seq: int, task: str, retry: int = 0) -> str:
    h = spawn_hash(task, WORKFLOW_AGENT_PROMPT, None, None)[:16]
    suffix = f":r{retry}" if retry else ""
    return f"wf:{_KEY}:{seq}:{h}{suffix}"


async def test_live_run_journals_each_spawn_and_result(store):
    ctx, calls = _ctx(["alpha", "beta"])
    o = Orchestra.for_ctx(ctx, journal=_journal(store))
    assert await o.agent("t1") == "alpha"
    assert await o.agent("t2") == "beta"
    assert calls["i"] == 2

    first = await store.get(_worker_id(1, "t1"))
    second = await store.get(_worker_id(2, "t2"))
    assert first is not None and first.status is InvocationStatus.SUCCEEDED
    assert first.result_payload == '{"text": "alpha"}'
    assert first.placement == "workflow"
    assert first.tool_name == "workflow.agent"
    assert second is not None and second.result_payload == '{"text": "beta"}'


async def test_reexec_replays_full_prefix_then_runs_live(store):
    ctx1, _ = _ctx(["alpha", "beta"])
    o1 = Orchestra.for_ctx(ctx1, journal=_journal(store))
    await o1.agent("t1")
    await o1.agent("t2")

    # Identical re-execution: everything served from the journal, no spawns.
    ctx2, calls2 = _ctx(["never"])
    o2 = Orchestra.for_ctx(ctx2, journal=_journal(store))
    assert await o2.agent("t1") == "alpha"
    assert await o2.agent("t2") == "beta"
    assert calls2["i"] == 0
    assert o2.spawn_count == 2  # seq stays aligned even for cached calls

    # Re-execution that diverges at seq 2: prefix replays, tail runs live.
    ctx3, calls3 = _ctx(["gamma"])
    o3 = Orchestra.for_ctx(ctx3, journal=_journal(store))
    assert await o3.agent("t1") == "alpha"
    assert await o3.agent("t2-changed") == "gamma"
    assert calls3["i"] == 1


async def test_miss_ends_replay_even_for_later_matching_seqs(store):
    ctx1, _ = _ctx(["alpha", "beta", "delta"])
    o1 = Orchestra.for_ctx(ctx1, journal=_journal(store))
    await o1.agent("t1")
    await o1.agent("t2")
    await o1.agent("t3")

    # Hash mismatch at seq 2 ends replay permanently: seq 3 matches the
    # journal but must run live (per-call independent matching is unsound).
    ctx2, calls2 = _ctx(["beta-2", "delta-2"])
    o2 = Orchestra.for_ctx(ctx2, journal=_journal(store))
    assert await o2.agent("t1") == "alpha"
    assert await o2.agent("t2-changed") == "beta-2"
    assert await o2.agent("t3") == "delta-2"
    assert calls2["i"] == 2
    # The live re-run of seq 3 is journaled in the next retry slot, and a
    # subsequent execution replays the LATEST attempt, not the stale one.
    retried = await store.get(_worker_id(3, "t3", retry=1))
    assert retried is not None and retried.result_payload == '{"text": "delta-2"}'

    ctx3, calls3 = _ctx(["never"])
    o3 = Orchestra.for_ctx(ctx3, journal=_journal(store))
    assert await o3.agent("t1") == "alpha"
    assert await o3.agent("t2-changed") == "beta-2"
    assert await o3.agent("t3") == "delta-2"
    assert calls3["i"] == 0


async def test_failed_journal_record_reruns(store):
    boom = {"raise": True}

    async def failing_spawn(ctx, *, task, **kwargs):
        if boom["raise"]:
            raise RuntimeError("worker crashed")
        return SimpleNamespace(text="recovered", status="completed")

    o1 = Orchestra.for_ctx(SimpleNamespace(spawn_fn=failing_spawn), journal=_journal(store))
    try:
        await o1.agent("t1")
    except RuntimeError:
        pass
    failed = await store.get(_worker_id(1, "t1"))
    assert failed is not None and failed.status is InvocationStatus.FAILED

    # A failed record is a miss, never a replayable hole: the call re-runs
    # live and the successful retry is journaled.
    boom["raise"] = False
    o2 = Orchestra.for_ctx(SimpleNamespace(spawn_fn=failing_spawn), journal=_journal(store))
    assert await o2.agent("t1") == "recovered"
    retried = await store.get(_worker_id(1, "t1", retry=1))
    assert retried is not None and retried.status is InvocationStatus.SUCCEEDED

    ctx3, calls3 = _ctx(["never"])
    o3 = Orchestra.for_ctx(ctx3, journal=_journal(store))
    assert await o3.agent("t1") == "recovered"
    assert calls3["i"] == 0


async def test_parallel_worker_exception_never_returns_none_or_caches_a_hole(store):
    attempts = {"count": 0}

    async def spawn(ctx, *, task, **kwargs):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise RuntimeError("worker crashed")
        return SimpleNamespace(text="recovered", status="completed")

    first = Orchestra.for_ctx(SimpleNamespace(spawn_fn=spawn), journal=_journal(store))
    with pytest.raises(BaseException) as raised:
        await first.parallel([lambda: first.agent("t1")])
    errors = raised.value.exceptions if isinstance(raised.value, BaseExceptionGroup) else [raised.value]
    assert any(isinstance(error, RuntimeError) and str(error) == "worker crashed" for error in errors)

    failed = await store.get(_worker_id(1, "t1"))
    assert failed is not None and failed.status is InvocationStatus.FAILED

    retry = Orchestra.for_ctx(SimpleNamespace(spawn_fn=spawn), journal=_journal(store))
    assert await retry.parallel([lambda: retry.agent("t1")]) == ["recovered"]
    recovered = await store.get(_worker_id(1, "t1", retry=1))
    assert recovered is not None and recovered.status is InvocationStatus.SUCCEEDED

    replay, calls = _ctx(["never"])
    cached = Orchestra.for_ctx(replay, journal=_journal(store))
    assert await cached.parallel([lambda: cached.agent("t1")]) == ["recovered"]
    assert calls["i"] == 0


@pytest.mark.parametrize("status", ["failed", "cancelled"])
async def test_terminal_worker_failure_is_journaled_as_failed_and_reruns(store, status):
    attempts = {"count": 0}

    async def spawn(ctx, *, task, **kwargs):
        attempts["count"] += 1
        if attempts["count"] == 1:
            return SimpleNamespace(text="worker did not complete", status=status)
        return SimpleNamespace(text="recovered", status="completed")

    first = Orchestra.for_ctx(SimpleNamespace(spawn_fn=spawn), journal=_journal(store))
    with pytest.raises(WorkflowAgentFailed) as raised:
        await first.agent("t1")
    assert raised.value.status == status

    failed = await store.get(_worker_id(1, "t1"))
    assert failed is not None and failed.status is InvocationStatus.FAILED
    assert failed.result_payload == (
        f'{{"error": "workflow agent ended with status {status}: worker did not complete"}}'
    )

    retry = Orchestra.for_ctx(SimpleNamespace(spawn_fn=spawn), journal=_journal(store))
    assert await retry.agent("t1") == "recovered"
    retried = await store.get(_worker_id(1, "t1", retry=1))
    assert retried is not None and retried.status is InvocationStatus.SUCCEEDED


async def test_formatter_output_journaled_and_replayed(store):
    ctx1, calls1 = _ctx(["worker answer"], structured={"value": 7})
    o1 = Orchestra.for_ctx(ctx1, journal=_journal(store))
    result = await o1.agent("t1", schema=_Schema)
    assert result.value == 7
    assert calls1["format_i"] == 1

    # Re-execution: worker replays from the journal AND the formatter output
    # replays without a second formatter pass.
    ctx2, calls2 = _ctx(["never"], structured={"value": 999})
    o2 = Orchestra.for_ctx(ctx2, journal=_journal(store))
    result = await o2.agent("t1", schema=_Schema)
    assert result.value == 7
    assert calls2["i"] == 0
    assert calls2["format_i"] == 0


async def test_cached_results_do_not_consume_budget(store):
    budget1 = RunBudget(total=1000)
    ctx1, _ = _ctx(["alpha", "beta"], budget=budget1, spend=100)
    o1 = Orchestra.for_ctx(ctx1, journal=_journal(store))
    await o1.agent("t1")
    await o1.agent("t2")
    assert budget1.output_tokens == 200

    budget2 = RunBudget(total=1000)
    ctx2, calls2 = _ctx(["never"], budget=budget2, spend=100)
    o2 = Orchestra.for_ctx(ctx2, journal=_journal(store))
    assert await o2.agent("t1") == "alpha"
    assert await o2.agent("t2") == "beta"
    assert calls2["i"] == 0
    assert budget2.output_tokens == 0


async def test_parallel_replay_serves_whole_cached_prefix(store):
    ctx1, _ = _ctx(["a", "b", "c"])
    o1 = Orchestra.for_ctx(ctx1, journal=_journal(store))
    first = await o1.parallel([o1.agent("t1"), o1.agent("t2"), o1.agent("t3")])
    assert first == ["a", "b", "c"]

    ctx2, calls2 = _ctx(["never"])
    o2 = Orchestra.for_ctx(ctx2, journal=_journal(store))
    assert await o2.parallel([o2.agent("t1"), o2.agent("t2"), o2.agent("t3")]) == first
    assert calls2["i"] == 0


async def test_no_journal_means_no_replay_and_no_records(store):
    ctx, calls = _ctx(["alpha"])
    o = Orchestra.for_ctx(ctx)
    assert await o.agent("t1") == "alpha"
    assert calls["i"] == 1
    assert await store.get(_worker_id(1, "t1")) is None
