from datetime import UTC, datetime

import pytest

from arden.database import connect
from arden.memory.facts import FactLedger, FactPlanStore, FactService, FactValidationError

NOW = datetime(2026, 7, 28, 12, tzinfo=UTC)


def _create(fact_id: str, *, subject: str, occurred_at: str | None = None) -> dict[str, object]:
    change: dict[str, object] = {
        "op": "create",
        "fact_id": fact_id,
        "text": f"Fact {fact_id}",
        "kind": "fact",
        "subjects": [subject],
        "scope": {"kind": "area", "key": "project"},
        "sources": [{"kind": "test", "ref": fact_id}],
    }
    if occurred_at is not None:
        change["occurred_at"] = occurred_at
    return change


def _commit(ledger: FactLedger, *changes: dict[str, object]) -> None:
    ledger.commit(ledger.plan(changes, actor="test", origin="test", reason="test feed"))


def test_change_feed_initial_delta_and_noop_preserve_append_history_order(tmp_path) -> None:
    ledger = FactLedger(tmp_path / "facts", clock=lambda: NOW)
    assert ledger.changes_since(None).through_revision is None

    _commit(ledger, _create("a", subject="alpha"))
    initial = ledger.changes_since(None)
    assert [event.fact_id for event in initial.events] == ["a"]
    assert initial.affected_routes == (("area", "project", "alpha"),)
    watermark = ledger.revision
    assert watermark is not None
    _commit(
        ledger,
        {
            "op": "amend",
            "fact_id": "a",
            "subjects": ["gamma"],
            "scope": {"kind": "area", "key": "other"},
        },
    )
    _commit(ledger, _create("b", subject="beta", occurred_at="2026-06-01T00:00:00Z"))

    delta = ledger.changes_since(watermark)

    assert [event.fact_id for event in delta.events] == ["a", "b"]
    assert [event.op for event in delta.events] == ["amend", "create"]
    assert delta.affected_fact_ids == ("a", "b")
    assert delta.affected_routes == (
        ("area", "other", "gamma"),
        ("area", "project", "alpha"),
        ("area", "project", "beta"),
    )
    assert delta.through_revision == ledger.revision

    noop = ledger.changes_since(delta.through_revision)
    assert noop.from_revision == noop.through_revision
    assert noop.events == ()
    assert noop.affected_fact_ids == ()
    assert noop.affected_routes == ()


def test_change_feed_includes_every_route_crossed_between_watermarks(tmp_path) -> None:
    ledger = FactLedger(tmp_path / "facts", clock=lambda: NOW)
    _commit(ledger, _create("a", subject="alpha"))
    watermark = ledger.revision
    assert watermark is not None
    _commit(
        ledger,
        {
            "op": "amend",
            "fact_id": "a",
            "subjects": ["beta"],
            "scope": {"kind": "area", "key": "middle"},
        },
    )
    _commit(
        ledger,
        {
            "op": "amend",
            "fact_id": "a",
            "subjects": ["gamma"],
            "scope": {"kind": "area", "key": "final"},
        },
    )

    feed = ledger.changes_since(watermark)

    assert feed.affected_routes == (
        ("area", "final", "gamma"),
        ("area", "middle", "beta"),
        ("area", "project", "alpha"),
    )


def test_change_feed_rejects_unreachable_watermarks(tmp_path) -> None:
    ledger = FactLedger(tmp_path / "facts", clock=lambda: NOW)
    _commit(ledger, _create("a", subject="alpha"))

    with pytest.raises(FactValidationError, match="reachable fact revision"):
        ledger.changes_since("not-a-revision")


def test_change_feed_keeps_one_multi_event_plan_atomic(tmp_path) -> None:
    ledger = FactLedger(tmp_path / "facts", clock=lambda: NOW)
    plan = ledger.plan(
        (_create("a", subject="alpha"), _create("b", subject="beta")),
        actor="test",
        origin="test",
        reason="atomic feed",
    )
    ledger.commit(plan)

    feed = ledger.changes_since(None)

    assert [event.fact_id for event in feed.events] == ["a", "b"]
    assert {event.plan_id for event in feed.events} == {plan.plan_id}


def test_change_feed_keeps_a_stable_head_when_a_new_commit_arrives(tmp_path, monkeypatch) -> None:
    ledger = FactLedger(tmp_path / "facts", clock=lambda: NOW)
    _commit(ledger, _create("a", subject="alpha"))
    head = ledger.revision
    original_events_added = ledger._events_added_by_commit
    injected = False

    def events_added(commit):
        nonlocal injected
        result = original_events_added(commit)
        if not injected:
            injected = True
            _commit(ledger, _create("b", subject="beta"))
        return result

    monkeypatch.setattr(ledger, "_events_added_by_commit", events_added)
    feed = ledger.changes_since(None)

    assert feed.through_revision == head
    assert [event.fact_id for event in feed.events] == ["a"]
    assert [event.fact_id for event in ledger.changes_since(feed.through_revision).events] == ["b"]


@pytest.mark.asyncio
async def test_fact_service_exposes_the_canonical_change_feed(tmp_path) -> None:
    connection = await connect(tmp_path / "facts.db")
    plans = FactPlanStore(connection)
    await plans.init_schema()
    ledger = FactLedger(tmp_path / "facts", clock=lambda: NOW)
    _commit(ledger, _create("a", subject="alpha"))
    try:
        feed = await FactService(ledger, plans).changes_since(None)
        assert [event.fact_id for event in feed.events] == ["a"]
    finally:
        await connection.close()
