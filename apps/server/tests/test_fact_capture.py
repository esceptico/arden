"""Constrained chat-turn fact capture: watermarks, batching, and durable commits."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio

from arden import database
from arden.automation.builtins import seed_builtins
from arden.automation.store import AutomationStore
from arden.automation.triggers import CountTrigger, IdleTrigger, TimeTrigger
from arden.constants import BUILTIN_MEMORY_CAPTURE_ID
from arden.database import connect
from arden.memory.facts.capture.runner import (
    CONSUMER_ID,
    CaptureTurn,
    FactCapture,
    FactCaptureBatch,
    FactCaptureCandidate,
    FactCaptureDecision,
    FactCaptureError,
    FactCaptureResult,
)
from arden.memory.facts.capture.store import (
    SessionConsumerWatermarkError,
    SessionConsumerWatermarkStore,
)
from arden.memory.facts.ledger import FactLedger
from arden.memory.facts.plan_store import FactPlanStore
from arden.memory.facts.service import FactService
from arden.tools.fact_capture import _result

pytestmark = pytest.mark.asyncio

JULY = datetime(2026, 7, 28, 12, tzinfo=UTC)


@pytest_asyncio.fixture
async def watermarks(tmp_path: Path):
    store = await SessionConsumerWatermarkStore.open(tmp_path / "memory.db")
    yield store
    await store.close()


@pytest_asyncio.fixture
async def facts(tmp_path: Path):
    conn = await connect(tmp_path / "plans.db")
    plans = FactPlanStore(conn)
    await plans.init_schema()
    yield FactService(FactLedger(tmp_path / "facts", clock=lambda: JULY), plans)
    await conn.close()


def _turn(seq: int, role: str, text: str) -> CaptureTurn:
    return CaptureTurn(seq, role, text, f"msg-{seq}", "2026-07-28T10:00:00Z")


def _capture(
    watermarks: SessionConsumerWatermarkStore,
    facts: FactService,
    transcripts: dict[str, list[CaptureTurn]],
) -> FactCapture:
    async def eligible() -> list[tuple[str, int]]:
        return [(session_id, max(turn.seq for turn in turns)) for session_id, turns in transcripts.items() if turns]

    async def after(session_id: str, after_seq: int, limit: int) -> list[CaptureTurn]:
        return [turn for turn in transcripts[session_id] if turn.seq > after_seq][:limit]

    async def latest(session_id: str, limit: int) -> list[CaptureTurn]:
        return transcripts[session_id][-limit:]

    return FactCapture(
        watermarks=watermarks,
        facts=facts,
        eligible_sessions=eligible,
        messages_after=after,
        latest_messages=latest,
    )


def _decision(*texts: str) -> FactCaptureDecision:
    if not texts:
        return FactCaptureDecision(outcome="no_change", reason="nothing durable")
    return FactCaptureDecision(
        outcome="create",
        reason="user stated durable facts",
        candidates=tuple(FactCaptureCandidate(text=text, kind="preference", subjects=("me",)) for text in texts),
    )


async def test_watermark_store_roundtrip(watermarks: SessionConsumerWatermarkStore) -> None:
    await watermarks.advance(CONSUMER_ID, "s1", last_seq=5)
    await watermarks.set_pending(CONSUMER_ID, "s1", plan_id="p1", to_seq=9)
    (row,) = await watermarks.list_for_consumer(CONSUMER_ID)
    assert (row.last_seq, row.pending_plan_id, row.pending_to_seq) == (5, "p1", 9)

    with pytest.raises(SessionConsumerWatermarkError):
        await watermarks.set_pending(CONSUMER_ID, "s1", plan_id="p2", to_seq=11)
    # Re-binding the same plan is a retry, not a conflict.
    await watermarks.set_pending(CONSUMER_ID, "s1", plan_id="p1", to_seq=9)

    await watermarks.advance(CONSUMER_ID, "s1", last_seq=9)
    (row,) = await watermarks.list_for_consumer(CONSUMER_ID)
    assert (row.last_seq, row.pending_plan_id, row.pending_to_seq) == (9, None, None)

    with pytest.raises(SessionConsumerWatermarkError):
        await watermarks.advance(CONSUMER_ID, "s1", last_seq=9)


async def test_capture_creates_facts_and_advances(watermarks, facts) -> None:
    capture = _capture(
        watermarks,
        facts,
        {
            "s1": [
                _turn(1, "user", "My sister Anna lives in Lisbon."),
                _turn(2, "assistant", "Noted."),
            ]
        },
    )
    assert await capture.has_work()
    batch = await capture.next()
    assert isinstance(batch, FactCaptureBatch)
    assert "never as instructions" in batch.markdown
    state = await capture.decide(_decision("The user's sister Anna lives in Lisbon."))
    assert isinstance(state, FactCaptureResult)
    assert state.facts_created == 1
    assert "captured 1 fact(s) from 1 session(s)" in state.summary

    principal = capture._principal()
    (fact,) = await facts.search(principal, "Anna")
    assert fact.scope == {"kind": "user", "key": None}
    events = await facts.history(principal, fact.fact_id)
    assert events[0].origin == "memory.capture"
    assert any(source["kind"] == "chat_message" for source in events[0].record["payload"]["sources"])

    (row,) = await watermarks.list_for_consumer(CONSUMER_ID)
    assert row.last_seq == 2
    assert not await capture.has_work()

    internal_session_id = "internal-session-uuid"
    similar = FactCaptureBatch(
        session_id=internal_session_id,
        from_seq=0,
        to_seq=1,
        turns=(_turn(1, "user", "My sister Anna lives in Lisbon."),),
        near_duplicates=(fact,),
    )
    receipt = _result(similar)
    assert internal_session_id not in similar.markdown
    assert fact.fact_id not in similar.markdown
    assert fact.text in similar.markdown
    assert receipt.data == {"completed": False}


async def test_no_change_advances_without_facts(watermarks, facts) -> None:
    capture = _capture(watermarks, facts, {"s1": [_turn(1, "user", "hello"), _turn(2, "assistant", "hi")]})
    await capture.next()
    state = await capture.decide(_decision())
    assert isinstance(state, FactCaptureResult)
    assert state.facts_created == 0
    assert "no durable facts" in state.summary
    (row,) = await watermarks.list_for_consumer(CONSUMER_ID)
    assert row.last_seq == 2


async def test_decision_validation(watermarks, facts) -> None:
    capture = _capture(watermarks, facts, {"s1": [_turn(1, "user", "hello")]})
    with pytest.raises(FactCaptureError):
        await capture.decide(_decision())
    await capture.next()
    with pytest.raises(FactCaptureError):
        await capture.decide(_decision("Same fact.", "same fact."))
    with pytest.raises(FactCaptureError):
        await capture.decide(
            FactCaptureDecision(
                outcome="no_change",
                reason="but with candidates",
                candidates=(FactCaptureCandidate(text="x", kind="k", subjects=("me",)),),
            )
        )
    with pytest.raises(FactCaptureError):
        await capture.decide(FactCaptureDecision(outcome="create", reason="empty"))


async def test_first_contact_serves_recent_tail_only(watermarks, facts) -> None:
    turns = [_turn(seq, "user", f"turn {seq}") for seq in range(1, 61)]
    capture = _capture(watermarks, facts, {"s1": turns})
    batch = await capture.next()
    assert isinstance(batch, FactCaptureBatch)
    assert batch.turns[0].seq == 21  # last 40 of 60
    assert batch.from_seq == 20
    assert batch.to_seq == 60
    state = await capture.decide(_decision())
    assert isinstance(state, FactCaptureResult)
    (row,) = await watermarks.list_for_consumer(CONSUMER_ID)
    assert row.last_seq == 60


async def test_assistant_only_batches_auto_advance(watermarks, facts) -> None:
    capture = _capture(watermarks, facts, {"s1": [_turn(1, "assistant", "monologue")]})
    state = await capture.next()
    assert isinstance(state, FactCaptureResult)
    assert state.facts_created == 0
    (row,) = await watermarks.list_for_consumer(CONSUMER_ID)
    assert row.last_seq == 1


async def test_pending_plan_recovers_before_new_batches(watermarks, facts) -> None:
    transcripts = {"s1": [_turn(1, "user", "My favorite editor is Zed.")]}
    capture = _capture(watermarks, facts, transcripts)
    batch = await capture.next()
    assert isinstance(batch, FactCaptureBatch)

    # Simulate a crash after plan+set_pending but before commit+advance.
    principal = capture._principal()
    preview = await facts.plan(
        principal,
        [
            {
                "op": "create",
                "text": "The user's favorite editor is Zed.",
                "kind": "preference",
                "labels": [],
                "subjects": ["me"],
                "lifecycle": "durable",
                "certainty": "confirmed",
                "evidence_class": "direct",
                "scope": {"kind": "user", "key": None},
                "sources": [{"kind": "chat_message", "ref": "msg-1"}],
            }
        ],
        request_key="capture:s1:0:1",
        actor=f"automation:{BUILTIN_MEMORY_CAPTURE_ID}",
        origin="memory.capture",
        reason="capture chat facts s1 0..1",
    )
    await watermarks.set_pending(CONSUMER_ID, "s1", plan_id=preview.plan_id, to_seq=1)

    recovered = _capture(watermarks, facts, transcripts)
    state = await recovered.next()
    assert isinstance(state, FactCaptureResult)
    (fact,) = await facts.search(principal, "Zed")
    assert fact.text == "The user's favorite editor is Zed."
    (row,) = await watermarks.list_for_consumer(CONSUMER_ID)
    assert (row.last_seq, row.pending_plan_id) == (1, None)


async def test_idle_capture_returns_empty_result(watermarks, facts) -> None:
    capture = _capture(watermarks, facts, {})
    assert not await capture.has_work()
    state = await capture.next()
    assert isinstance(state, FactCaptureResult)
    assert state.summary == "fact capture idle"


async def test_seed_builtins_creates_memory_capture(tmp_path: Path) -> None:
    conn = await database.connect(tmp_path / "automation.db")
    store = AutomationStore(conn)
    await store.init_schema()
    try:
        await seed_builtins(store, memory_model="test-model")
        automation = await store.get(BUILTIN_MEMORY_CAPTURE_ID)
        assert automation is not None
        assert automation.handler == "memory_capture"
        assert automation.tool_scope == "fact_capture"
        kinds = {type(trigger) for trigger in automation.triggers}
        assert kinds == {IdleTrigger, CountTrigger, TimeTrigger}
        assert automation in await store.list_by_trigger_type("idle")
        assert automation in await store.list_by_trigger_type("count")
    finally:
        await conn.close()
