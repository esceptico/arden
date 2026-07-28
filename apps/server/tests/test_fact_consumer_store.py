import asyncio
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from arden.database import connect
from arden.memory.facts import (
    FactConsumerStore,
    FactConsumerWatermarkConflictError,
    FactLedger,
    FactValidationError,
)

pytestmark = pytest.mark.asyncio

NOW = datetime(2026, 7, 28, 12, tzinfo=UTC)


def _create(fact_id: str) -> dict[str, object]:
    return {
        "op": "create",
        "fact_id": fact_id,
        "text": f"Fact {fact_id}",
        "kind": "fact",
        "subjects": [fact_id],
        "scope": {"kind": "area", "key": "project"},
        "sources": [{"kind": "test", "ref": fact_id}],
    }


def _commit(ledger: FactLedger, fact_id: str) -> str:
    plan = ledger.plan((_create(fact_id),), actor="test", origin="test", reason="consumer test")
    ledger.commit(plan)
    assert ledger.revision is not None
    return ledger.revision


async def test_fact_consumer_watermark_accepts_only_a_canonical_processed_feed(tmp_path) -> None:
    path = tmp_path / "facts.db"
    ledger = FactLedger(tmp_path / "facts", clock=lambda: NOW)
    revision_1 = _commit(ledger, "a")
    initial = ledger.changes_since(None)
    first = await FactConsumerStore.open(path)
    try:
        assert await first.get("synthesis") is None
        fabricated = replace(initial, through_revision="f" * 64)
        with pytest.raises(FactValidationError, match="reachable"):
            await first.advance("synthesis", feed=fabricated, ledger=ledger)
        tampered = replace(initial, affected_routes=())
        with pytest.raises(FactValidationError, match="canonical history"):
            await first.advance("synthesis", feed=tampered, ledger=ledger)

        inserted = await first.advance("synthesis", feed=initial, ledger=ledger)
        assert inserted.revision == revision_1
        with pytest.raises(FactConsumerWatermarkConflictError):
            await first.advance("synthesis", feed=initial, ledger=ledger)

        _commit(ledger, "b")
        delta = ledger.changes_since(revision_1)
        assert [event.fact_id for event in delta.events] == ["b"]
    finally:
        await first.close()

    second = await FactConsumerStore.open(path)
    try:
        restored = await second.get("synthesis")
        assert restored is not None and restored.revision == revision_1
        advanced = await second.advance("synthesis", feed=delta, ledger=ledger)
        assert advanced.revision == delta.through_revision
        with pytest.raises(FactConsumerWatermarkConflictError):
            await second.advance("synthesis", feed=delta, ledger=ledger)
        assert (await second.get("synthesis")).revision == delta.through_revision
    finally:
        await second.close()


async def test_fact_consumer_watermark_has_one_concurrent_cas_winner(tmp_path) -> None:
    path = tmp_path / "facts.db"
    ledger = FactLedger(tmp_path / "facts", clock=lambda: NOW)
    revision_1 = _commit(ledger, "a")
    initial = ledger.changes_since(None)
    first = await FactConsumerStore.open(path)
    second = await FactConsumerStore.open(path)
    try:
        await first.advance("synthesis", feed=initial, ledger=ledger)
        revision_2 = _commit(ledger, "b")
        short_feed = ledger.changes_since(revision_1)
        _commit(ledger, "c")
        long_feed = ledger.changes_since(revision_1)

        results = await asyncio.gather(
            first.advance("synthesis", feed=short_feed, ledger=ledger),
            second.advance("synthesis", feed=long_feed, ledger=ledger),
            return_exceptions=True,
        )

        assert sum(isinstance(result, FactConsumerWatermarkConflictError) for result in results) == 1
        assert (await first.get("synthesis")).revision in {revision_2, long_feed.through_revision}
    finally:
        await first.close()
        await second.close()


async def test_cancelled_advance_cannot_leave_a_transaction_for_another_store(
    tmp_path,
    monkeypatch,
) -> None:
    path = tmp_path / "facts.db"
    ledger = FactLedger(tmp_path / "facts", clock=lambda: NOW)
    revision_1 = _commit(ledger, "a")
    seed = await FactConsumerStore.open(path)
    try:
        await seed.advance("synthesis", feed=ledger.changes_since(None), ledger=ledger)
    finally:
        await seed.close()

    _commit(ledger, "b")
    delta = ledger.changes_since(revision_1)
    connection = await connect(path, autocommit=True)
    delayed = FactConsumerStore(connection)
    observer = await FactConsumerStore.open(path)
    entered = asyncio.Event()
    blocker = asyncio.Event()
    original_execute = connection.execute

    async def execute_then_pause(sql, parameters):
        cursor = await original_execute(sql, parameters)
        if "UPDATE fact_consumer_watermarks" in sql:
            entered.set()
            await blocker.wait()
        return cursor

    monkeypatch.setattr(connection, "execute", execute_then_pause)
    task = asyncio.create_task(delayed.advance("synthesis", feed=delta, ledger=ledger))
    try:
        await asyncio.wait_for(entered.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        persisted = await observer.get("synthesis")
        assert persisted is not None and persisted.revision == delta.through_revision
    finally:
        blocker.set()
        await delayed.close()
        await observer.close()


async def test_fact_consumer_store_rejects_transactional_connections(tmp_path) -> None:
    connection = await connect(tmp_path / "facts.db")
    try:
        with pytest.raises(ValueError, match="autocommit"):
            FactConsumerStore(connection)
    finally:
        await connection.close()


async def test_retention_checkpoint_survives_restart_and_allows_an_empty_ledger(tmp_path) -> None:
    path = tmp_path / "facts.db"
    first = await FactConsumerStore.open(path)
    try:
        assert await first.get_retention_checkpoint("builtin-memory-retention") is None
        checkpoint = await first.record_retention_checkpoint(
            "builtin-memory-retention",
            revision=None,
            evaluated_at=NOW,
        )
        assert checkpoint.revision is None
        assert checkpoint.evaluated_at == NOW
    finally:
        await first.close()

    second = await FactConsumerStore.open(path)
    try:
        restored = await second.get_retention_checkpoint("builtin-memory-retention")
        assert restored == checkpoint
    finally:
        await second.close()
