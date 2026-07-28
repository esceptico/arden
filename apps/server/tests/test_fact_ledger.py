"""Focused contracts for the standalone canonical fact ledger."""

import hashlib
import json
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from arden.memory.facts import (
    FactConflictError,
    FactLedger,
    FactLedgerCorruptionError,
    FactValidationError,
)
from arden.revisions import ChangeSet, Update

JULY = datetime(2026, 7, 28, 12, tzinfo=UTC)


def _clock(value: datetime) -> tuple[dict[str, datetime], object]:
    point = {"now": value}
    return point, lambda: point["now"]


def _ledger(tmp_path: Path, *, now: datetime = JULY) -> FactLedger:
    return FactLedger(tmp_path / "facts", clock=lambda: now)


def _source(fact_id: str) -> dict[str, object]:
    return {
        "kind": "chat_message",
        "ref": f"session:{fact_id}",
        "captured_at": "2026-07-28T12:00:00Z",
        "scope_kind": "area",
        "scope_key": "project",
        "occurred_at": "2026-07-28T11:59:00Z",
        "time_precision": "second",
        "role": "evidence",
        "excerpt_hash": "abc123",
    }


def _create(fact_id: str, text: str, **extra: object) -> dict[str, object]:
    return {
        "op": "create",
        "fact_id": fact_id,
        "text": text,
        "kind": "fact",
        "labels": ["memory"],
        "subjects": ["alpha"],
        "scope": {"kind": "area", "key": "project"},
        "sources": [_source(fact_id)],
        **extra,
    }


def _commit(ledger: FactLedger, *changes: dict[str, object]) -> str:
    plan = _plan(ledger, changes)
    ledger.commit(plan)
    return plan.plan_id


def _plan(
    ledger: FactLedger,
    changes: object,
    *,
    actor: str = "test-agent",
    origin: str = "fact-ledger-test",
    reason: str = "test fact change",
):
    return ledger.plan(changes, actor=actor, origin=origin, reason=reason)


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def test_facts_at_replays_exact_reachable_state_without_live_head_mixing(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    assert dict(ledger.facts_at(None)) == {}

    source = _source("a")
    source["extra"] = {"nested": {"value": "original"}, "items": [{"value": "first"}]}
    _commit(ledger, _create("a", "First", sources=[source]))
    first_revision = ledger.revision
    assert first_revision is not None
    first = ledger.facts_at(first_revision)

    _commit(ledger, {"op": "amend", "fact_id": "a", "labels": ["changed"]}, _create("b", "Second"))

    assert tuple(first) == ("a",)
    assert first["a"].labels == ("memory",)
    assert tuple(ledger.facts_at(first_revision)) == ("a",)
    with pytest.raises(TypeError):
        first["b"] = ledger.get("b")  # type: ignore[index]
    with pytest.raises(TypeError):
        first["a"].scope["key"] = "other"  # type: ignore[index]
    with pytest.raises(TypeError):
        first["a"].sources[0]["ref"] = "changed"  # type: ignore[index]
    extra = first["a"].sources[0]["extra"]
    assert isinstance(extra, Mapping)
    nested = extra["nested"]
    assert isinstance(nested, Mapping)
    with pytest.raises(TypeError):
        nested["value"] = "changed"  # type: ignore[index]
    items = extra["items"]
    assert isinstance(items, tuple)
    with pytest.raises(TypeError):
        items[0]["value"] = "changed"  # type: ignore[index]


def test_facts_at_rejects_unreachable_revision(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    _commit(ledger, _create("a", "First"))

    with pytest.raises(FactValidationError, match="reachable"):
        ledger.facts_at("0" * 64)
    with pytest.raises(FactValidationError, match="revision"):
        ledger.facts_at("")


def test_schema_roundtrip_multiple_subjects_expiry_and_fallback(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    expires = "2026-08-01T00:00:00Z"
    _commit(
        ledger,
        _create(
            "a",
            "Hello",
            kind="preference",
            labels=["personal", "stable"],
            subjects=["alpha", "beta"],
            scope={"kind": "project", "key": "arden"},
            lifecycle="temporary",
            evidence_class="inferred",
            certainty="uncertain",
            expires_at=expires,
            sources=[
                {
                    "kind": "calendar",
                    "ref": "event:1",
                    "occurred_at": "2026-07-28",
                    "time_precision": "day",
                }
            ],
        ),
    )
    fact = FactLedger(ledger.root).get("a")
    assert fact.kind == "preference"
    assert fact.labels == ("personal", "stable")
    assert fact.subjects == ("alpha", "beta")
    assert fact.scope == {"kind": "project", "key": "arden"}
    assert fact.expires_at == datetime(2026, 8, 1, tzinfo=UTC)
    assert fact.sources[0]["occurred_at"] == "2026-07-28"
    assert fact.review_basis is None
    assert fact.review_at is None
    assert ledger.due_reviews(now=datetime(2026, 7, 31, tzinfo=UTC)) == ()
    due = ledger.due_reviews(now=datetime(2026, 8, 1, tzinfo=UTC))
    assert [(item.fact.fact_id, item.due_at) for item in due] == [("a", datetime(2026, 8, 1, tzinfo=UTC))]
    _commit(ledger, _create("b", "Fallback", lifecycle="temporary"))
    fallback = ledger.get("b")
    assert fallback.review_basis == "fallback"
    assert fallback.review_at == JULY + timedelta(days=90)
    assert (ledger.records_root / "2026-07.jsonl").is_file()


def test_scopes_and_source_provenance_roundtrip(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    _commit(
        ledger,
        _create(
            "global",
            "Global fact",
            scope={"kind": "global", "key": None},
            sources=[
                {
                    "kind": "chat_message",
                    "ref": "",
                    "scope_kind": "user",
                    "scope_key": None,
                    "extra": {"session_id": "session:1", "seq": 2, "flags": [True, None]},
                }
            ],
        ),
        _create(
            "user",
            "User fact",
            scope={"kind": "user", "key": None},
            sources=[{"kind": "email", "ref": "message:1", "scope_kind": "global", "scope_key": None}],
        ),
        _create(
            "integration",
            "Integration fact",
            scope={"kind": "integration", "key": "slack:C123"},
            sources=[
                {
                    "kind": "slack",
                    "ref": "thread:1",
                    "scope_kind": "integration",
                    "scope_key": "slack:C123",
                }
            ],
        ),
    )
    restarted = FactLedger(ledger.root)
    assert restarted.get("global").scope == {"kind": "global", "key": None}
    assert restarted.get("user").scope == {"kind": "user", "key": None}
    assert restarted.get("integration").scope == {"kind": "integration", "key": "slack:C123"}
    assert restarted.get("global").sources[0]["extra"] == {
        "session_id": "session:1",
        "seq": 2,
        "flags": [True, None],
    }
    assert restarted.get("global").sources[0]["ref"] == ""


@pytest.mark.parametrize(
    ("precision", "occurred_at"),
    [
        ("unknown", None),
        ("day", "2026-07-28"),
        ("minute", "2026-07-28T11:59:00Z"),
        ("second", "2026-07-28T11:59:01Z"),
        ("millisecond", "2026-07-28T11:59:01.123Z"),
    ],
)
def test_source_timestamp_precision_roundtrips(
    tmp_path: Path,
    precision: str,
    occurred_at: str | None,
) -> None:
    source: dict[str, object] = {
        "kind": "chat",
        "ref": "message:1",
        "time_precision": precision,
    }
    if occurred_at is not None:
        source["occurred_at"] = occurred_at
    ledger = _ledger(tmp_path)
    _commit(ledger, _create("precise", "Precise source", sources=[source]))
    assert ledger.get("precise").sources[0] == source


@pytest.mark.parametrize(
    ("precision", "occurred_at"),
    [
        (None, "2026-07-28T11:59:01Z"),
        ("unknown", "2026-07-28T11:59:01Z"),
        ("day", None),
        ("day", "2026-07-28T00:00:00Z"),
        ("minute", None),
        ("minute", "2026-07-28T11:59:01Z"),
        ("minute", "2026-07-28T11:59:00.000Z"),
        ("second", "2026-07-28T11:59:01.000Z"),
        ("millisecond", "2026-07-28T11:59:01Z"),
        ("millisecond", "2026-07-28T11:59:01.12Z"),
        ("millisecond", "2026-07-28T11:59:01.1234Z"),
    ],
)
def test_source_timestamp_precision_rejects_false_precision(
    tmp_path: Path,
    precision: str | None,
    occurred_at: str | None,
) -> None:
    source: dict[str, object] = {"kind": "chat", "ref": "message:1"}
    if precision is not None:
        source["time_precision"] = precision
    if occurred_at is not None:
        source["occurred_at"] = occurred_at
    with pytest.raises(FactValidationError, match="time_precision"):
        _plan(_ledger(tmp_path), (_create("invalid", "Invalid source", sources=[source]),))


@pytest.mark.parametrize(
    "scope,source",
    [
        ({"kind": "area", "key": None}, _source("invalid")),
        ({"kind": "global", "key": "all"}, _source("invalid")),
        ({"kind": "unknown", "key": "x"}, _source("invalid")),
        ({"kind": "user", "key": None}, {"kind": "chat_message", "ref": "x", "scope_kind": "area", "scope_key": None}),
        (
            {"kind": "user", "key": None},
            {"kind": "chat_message", "ref": "x", "scope_kind": "global", "scope_key": "all"},
        ),
        ({"kind": "user", "key": None}, {"kind": "chat_message", "ref": "x", "extra": {"bad": float("nan")}}),
        ({"kind": "user", "key": None}, {"kind": "chat_message", "ref": "x", "extra": {1: "bad"}}),
        ({"kind": "user", "key": None}, {"kind": "chat_message", "ref": None}),
        ({"kind": "user", "key": None}, {"kind": "chat_message", "ref": "bad\0ref"}),
    ],
)
def test_scope_and_extra_validation(
    tmp_path: Path,
    scope: dict[str, object],
    source: dict[object, object],
) -> None:
    ledger = _ledger(tmp_path)
    with pytest.raises(FactValidationError):
        _plan(ledger, (_create("invalid", "Invalid", scope=scope, sources=[source]),))


@pytest.mark.parametrize(
    "field,value",
    [
        ("kind", ""),
        ("labels", [""]),
        ("subjects", []),
        ("scope", {"kind": "folder", "key": "x"}),
        ("sources", [{"kind": "chat_message", "ref": "x", "unknown": "no"}]),
        ("review_basis", "sometimes"),
    ],
)
def test_strict_schema_validation(tmp_path: Path, field: str, value: object) -> None:
    ledger = _ledger(tmp_path)
    change = _create("a", "Hello", review_at="2026-08-01T00:00:00Z")
    change[field] = value
    with pytest.raises(FactValidationError):
        _plan(ledger, (change,))


def test_lifecycle_expiry_and_metadata_corrections_are_strict(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    with pytest.raises(FactValidationError, match="durable facts"):
        _plan(ledger, (_create("bad", "Durable expiry", expires_at="2026-08-01T00:00:00Z"),))
    _commit(ledger, _create("a", "Authority"))
    _commit(
        ledger,
        {
            "op": "amend",
            "fact_id": "a",
            "scope": {"kind": "global", "key": None},
            "lifecycle": "temporary",
            "evidence_class": "inferred",
        },
    )
    corrected = ledger.get("a")
    assert corrected.scope == {"kind": "global", "key": None}
    assert corrected.lifecycle == "temporary"
    assert corrected.evidence_class == "inferred"
    assert corrected.review_at == JULY + timedelta(days=90)
    assert corrected.review_basis == "fallback"
    first_version = ledger.history("a")[0].record
    created = ledger.get_version("a", hashlib.sha256(("\0" + _canonical(first_version)).encode()).hexdigest())
    assert created.scope == {"kind": "area", "key": "project"}
    _commit(
        ledger,
        {
            "op": "amend",
            "fact_id": "a",
            "lifecycle": "temporary",
            "labels": ["reviewed-metadata"],
        },
    )
    assert ledger.get("a").review_at == corrected.review_at
    _commit(
        ledger,
        {
            "op": "amend",
            "fact_id": "a",
            "scope": {"kind": "user", "key": None},
            "lifecycle": "durable",
            "evidence_class": "direct",
        },
    )
    durable = ledger.get("a")
    assert durable.lifecycle == "durable"
    assert durable.review_at is None
    assert durable.review_basis is None
    assert durable.expires_at is None
    for field, value in (
        ("evidence_class", "reported"),
        ("lifecycle", "forever"),
        ("certainty", "uncertain"),
        ("expires_at", "2026-08-01T00:00:00Z"),
        ("scope", {"kind": "global", "key": "global"}),
    ):
        with pytest.raises(FactValidationError):
            _plan(ledger, ({"op": "amend", "fact_id": "a", field: value},))


def test_scope_correction_pins_old_and_new_duplicate_windows(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    _commit(ledger, _create("a", "Same"))
    correction = _plan(
        ledger,
        (
            {
                "op": "amend",
                "fact_id": "a",
                "scope": {"kind": "global", "key": None},
            },
        ),
    )
    _commit(
        ledger,
        _create(
            "b",
            " Same ",
            scope={"kind": "global", "key": None},
        ),
    )
    with pytest.raises(FactConflictError, match="duplicate"):
        ledger.commit(correction)


def test_reasons_transitions_and_full_record_chain_hash(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    _commit(ledger, _create("a", "One"))
    _commit(
        ledger,
        {
            "op": "amend",
            "fact_id": "a",
            "labels": ["checked"],
        },
    )
    assert ledger.get("a").lifecycle == "durable"
    _commit(
        ledger,
        {
            "op": "review",
            "fact_id": "a",
            "reason": "verified against source",
            "review_at": "2026-08-15T00:00:00Z",
            "review_basis": "inferred",
            "certainty": "uncertain",
        },
    )
    assert ledger.get("a").review_basis == "inferred"
    assert ledger.get("a").certainty == "uncertain"
    history = ledger.history("a")
    version = ""
    for event in history:
        version = hashlib.sha256((version + "\0" + _canonical(event.record)).encode()).hexdigest()
    assert ledger.get("a").version == version
    assert history[-1].record["payload"]["reason"] == "verified against source"
    with pytest.raises(FactValidationError, match="reason"):
        _plan(ledger, ({"op": "expire", "fact_id": "a"},))
    _commit(ledger, {"op": "expire", "fact_id": "a", "reason": "explicit expiry reached"})
    assert ledger.get("a").status == "expired"
    assert ledger.history("a")[-1].record["payload"]["reason"] == "explicit expiry reached"
    _commit(ledger, _create("b", "Two"))
    _commit(ledger, {"op": "retract", "fact_id": "b", "reason": "source was wrong"})
    assert ledger.get("b").status == "retracted"


def test_transition_event_sources_roundtrip_and_implicit_supersession(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    direct_sources = [
        {
            "kind": "chat_message",
            "ref": "session:direct",
            "extra": {"session_id": "session:direct", "seq": 9},
        }
    ]
    retraction_sources = [
        {
            "kind": "email",
            "ref": "message:retraction",
            "extra": {"reason_code": "source_deleted"},
        }
    ]
    _commit(
        ledger,
        _create("old", "Old"),
        _create("successor", "Successor"),
        _create("expires", "Expires"),
        _create("retracts", "Retracts"),
        _create("implicit-old", "Implicit old"),
    )
    _commit(
        ledger,
        {
            "op": "supersede",
            "fact_id": "old",
            "successor_id": "successor",
            "reason": "corrected",
            "sources": direct_sources,
        },
    )
    _commit(
        ledger,
        {
            "op": "expire",
            "fact_id": "expires",
            "reason": "expired explicitly",
            "sources": direct_sources,
        },
    )
    _commit(
        ledger,
        {
            "op": "retract",
            "fact_id": "retracts",
            "reason": "source was deleted",
            "sources": retraction_sources,
        },
    )
    implicit = _create(
        "implicit",
        "Implicit successor",
        supersedes=["implicit-old"],
        reason="replacement",
        sources=[
            {
                "kind": "calendar",
                "ref": "event:implicit",
                "extra": {"calendar_id": "primary"},
            }
        ],
    )
    _commit(ledger, implicit)

    restarted = FactLedger(ledger.root, clock=lambda: JULY)
    assert restarted.history("old")[-1].record["payload"]["sources"] == direct_sources
    assert restarted.history("expires")[-1].record["payload"]["sources"] == direct_sources
    assert restarted.history("retracts")[-1].record["payload"]["sources"] == retraction_sources
    assert restarted.history("implicit-old")[-1].record["payload"]["sources"] == implicit["sources"]


@pytest.mark.parametrize(
    "change",
    [
        {"op": "supersede", "fact_id": "old", "successor_id": "successor", "reason": "bad", "sources": []},
        {"op": "expire", "fact_id": "old", "reason": "bad", "sources": [{"kind": "chat", "ref": "x", "bad": True}]},
        {"op": "retract", "fact_id": "old", "reason": "bad", "sources": [{"kind": "", "ref": "x"}]},
    ],
)
def test_terminal_transition_sources_are_strict(tmp_path: Path, change: dict[str, object]) -> None:
    ledger = _ledger(tmp_path)
    _commit(ledger, _create("old", "Old"), _create("successor", "Successor"))
    with pytest.raises(FactValidationError):
        _plan(ledger, (change,))


def test_atomic_create_supersede_and_direct_inferred_safety(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    _commit(ledger, _create("direct", "One"))
    with pytest.raises(FactValidationError, match="inferred"):
        _plan(
            ledger,
            (
                _create(
                    "inferred",
                    "Two",
                    evidence_class="inferred",
                    supersedes=["direct"],
                    reason="weak replacement",
                ),
            ),
        )
    plan = _plan(ledger, (_create("next", "Corrected", supersedes=["direct"], reason="corrected text"),))
    ledger.commit(plan)
    assert ledger.get("direct").status == "superseded"
    assert ledger.get("direct").successor_id == "next"
    assert {event.op for event in plan.events} == {"create", "supersede"}
    with pytest.raises(FactValidationError, match="text amendments"):
        _plan(ledger, ({"op": "amend", "fact_id": "next", "text": "mutation"},))
    _commit(ledger, _create("leaf", "Weak leaf", evidence_class="inferred"))
    with pytest.raises(FactValidationError, match="inferred"):
        _plan(
            ledger,
            (
                {
                    "op": "supersede",
                    "fact_id": "next",
                    "successor_id": "leaf",
                    "reason": "invalid authority chain",
                },
            ),
        )


def test_plan_is_pure_and_retained_plan_retries_after_restart(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    assert ledger._repository.head is None
    assert ledger._repository.list_resources() == ()
    plan = _plan(
        ledger,
        (_create("a", "Survives restart"),),
        actor="curator:42",
        origin="memory.curator",
        reason="promote verified memory",
    )
    assert ledger._repository.head is None
    assert ledger._repository.list_resources() == ()
    assert list(ledger.records_root.iterdir()) == []
    restarted = FactLedger(ledger.root, clock=lambda: JULY)
    first = restarted.commit(plan)
    retried = FactLedger(ledger.root, clock=lambda: JULY).commit(plan)
    assert [_canonical(event.record) for event in retried] == [_canonical(event.record) for event in first]
    assert retried == first
    active_paths = {resource.path for resource in restarted._repository.list_resources()}
    assert active_paths == {".ledger.json", "2026-07.jsonl"}
    assert (first[0].actor, first[0].origin, first[0].plan_reason) == (
        "curator:42",
        "memory.curator",
        "promote verified memory",
    )
    publication = restarted._repository.history(resource_id="2026-07.jsonl", limit=1)[0]
    assert (publication.actor, publication.origin, publication.reason) == (
        "curator:42",
        "memory.curator",
        "promote verified memory",
    )


def test_commit_rejects_inconsistent_plan_values(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    plan = _plan(
        ledger,
        (_create("a", "Immutable plan"),),
        actor="agent-a",
        origin="test-origin",
        reason="keep attribution",
    )
    inconsistent = replace(
        plan,
        events=(replace(plan.events[0], actor="agent-b"),),
    )
    with pytest.raises(FactValidationError, match="canonical record"):
        ledger.commit(inconsistent)
    with pytest.raises(FactValidationError, match="duplicate windows"):
        ledger.commit(replace(plan, duplicate_windows={}))

    _commit(ledger, _create("existing", "Existing"))
    dependent = _plan(
        ledger,
        ({"op": "amend", "fact_id": "existing", "labels": ["changed"]},),
    )
    with pytest.raises(FactValidationError, match="dependencies"):
        ledger.commit(replace(dependent, dependencies={}))


def test_fact_evidence_and_expected_target_versions_are_cas_dependencies(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    _commit(ledger, _create("target", "Target"), _create("evidence", "Evidence"))
    target_version = ledger.get("target").version
    evidence_version = ledger.get("evidence").version
    plan = _plan(
        ledger,
        (
            {
                "op": "review",
                "fact_id": "target",
                "expected_version": target_version,
                "reason": "newer evidence",
                "sources": [
                    {
                        "kind": "fact",
                        "ref": "evidence",
                        "scope_kind": "area",
                        "scope_key": "project",
                        "role": "evidence",
                        "extra": {"version": evidence_version},
                    }
                ],
            },
        ),
    )
    assert plan.dependencies == {"target": target_version, "evidence": evidence_version}

    altered_dependencies = {**plan.dependencies, "evidence": target_version}
    with pytest.raises(FactValidationError, match="cited evidence"):
        ledger.validate_plan(replace(plan, dependencies=altered_dependencies))

    _commit(ledger, {"op": "amend", "fact_id": "evidence", "labels": ["changed"]})
    with pytest.raises(FactConflictError, match="evidence"):
        ledger.commit(plan)

    _commit(ledger, {"op": "amend", "fact_id": "target", "labels": ["changed"]})
    with pytest.raises(FactConflictError, match="due review"):
        _plan(
            ledger,
            (
                {
                    "op": "review",
                    "fact_id": "target",
                    "expected_version": target_version,
                    "reason": "stale due snapshot",
                },
            ),
        )

    with pytest.raises(FactValidationError, match="exact version"):
        _plan(
            ledger,
            (
                {
                    "op": "review",
                    "fact_id": "target",
                    "reason": "unversioned evidence",
                    "sources": [{"kind": "fact", "ref": "evidence"}],
                },
            ),
        )


def test_retention_review_window_is_bound_to_retention_plans(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    _commit(
        ledger,
        _create(
            "due",
            "Due fact",
            lifecycle="temporary",
            review_at="2026-07-01T00:00:00Z",
            review_basis="explicit",
        ),
    )
    due = ledger.due_reviews()[0]
    plan = _plan(
        ledger,
        (
            {
                "op": "review",
                "fact_id": "due",
                "expected_version": due.fact.version,
                "expected_review_window": due.review_window,
                "review_at": "2026-08-01T00:00:00Z",
                "review_basis": "explicit",
                "certainty": "uncertain",
                "reason": "No newer evidence",
            },
        ),
        actor="automation:builtin-memory-retention",
        origin="memory.retention",
        reason="review due fact",
    )
    assert plan.events[0].record["payload"]["review_window"] == due.review_window

    record = dict(plan.events[0].record)
    payload = dict(record["payload"])
    payload.pop("review_window")
    record["payload"] = payload
    missing_window = replace(plan, events=(replace(plan.events[0], record=record),))
    with pytest.raises(FactValidationError, match="require"):
        ledger.validate_plan(missing_window)

    ordinary = _plan(
        ledger,
        (
            {
                "op": "review",
                "fact_id": "due",
                "review_at": "2026-08-01T00:00:00Z",
                "review_basis": "explicit",
                "reason": "ordinary review",
            },
        ),
    )
    record = dict(ordinary.events[0].record)
    record["payload"] = {**record["payload"], "review_window": "0" * 64}
    forged_window = replace(ordinary, events=(replace(ordinary.events[0], record=record),))
    with pytest.raises(FactValidationError, match="reserved"):
        ledger.validate_plan(forged_window)


@pytest.mark.parametrize(
    ("field", "value"),
    [("actor", ""), ("origin", ""), ("reason", ""), ("actor", 7)],
)
def test_plan_attribution_is_required_and_strict(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    ledger = _ledger(tmp_path)
    attribution: dict[str, object] = {
        "actor": "agent",
        "origin": "memory.test",
        "reason": "test",
    }
    attribution[field] = value
    with pytest.raises(FactValidationError, match=field):
        ledger.plan((_create("a", "Attributed"),), **attribution)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="reason"):
        ledger.plan(  # type: ignore[call-arg]
            (_create("a", "Missing attribution"),),
            actor="agent",
            origin="memory.test",
        )


def test_idempotency_rejects_same_event_ids_with_altered_records(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    plan = _plan(ledger, (_create("a", "Original"),))
    ledger.commit(plan)
    record = dict(plan.events[0].record)
    record["payload"] = {**record["payload"], "text": "Altered", "normalized_text": "altered"}
    altered = replace(plan, events=(replace(plan.events[0], record=record),))
    with pytest.raises(FactLedgerCorruptionError, match="altered canonical events"):
        ledger.commit(altered)


def test_uncertain_post_publish_retry_after_restart(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ledger = _ledger(tmp_path)
    plan = _plan(ledger, (_create("a", "Published before the timeout"),))
    publish = ledger._repository.commit

    def uncertain(change_set: ChangeSet):
        result = publish(change_set)
        if change_set.idempotency_key.startswith("fact-commit:"):
            raise TimeoutError("result lost after durable publication")
        return result

    monkeypatch.setattr(ledger._repository, "commit", uncertain)
    with pytest.raises(TimeoutError):
        ledger.commit(plan)
    events = FactLedger(ledger.root, clock=lambda: JULY).commit(plan)
    assert [_canonical(event.record) for event in events] == [_canonical(event.record) for event in plan.events]
    assert FactLedger(ledger.root).get("a").text == "Published before the timeout"


def test_repository_crash_recovers_atomic_batch_with_retained_plan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = _ledger(tmp_path)
    plan = _plan(ledger, (_create("a", "Crash-safe"),))

    def interrupt(point: str) -> None:
        if point == "after_ref":
            raise RuntimeError("simulated crash after ref")

    monkeypatch.setattr(ledger._repository, "_checkpoint", interrupt)
    with pytest.raises(RuntimeError, match="simulated crash"):
        ledger.commit(plan)
    recovered = FactLedger(ledger.root, clock=lambda: JULY)
    events = recovered.commit(plan)
    assert [_canonical(event.record) for event in events] == [_canonical(event.record) for event in plan.events]
    assert recovered.get("a").text == "Crash-safe"


def test_cross_month_same_fact_interleaving_conflicts_atomically(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    point, clock = _clock(JULY)
    root = tmp_path / "facts"
    seed = FactLedger(root, clock=clock)
    _commit(seed, _create("a", "One"))
    point["now"] = datetime(2026, 8, 1, tzinfo=UTC)
    august = FactLedger(root, clock=clock)
    old = _plan(
        august,
        (
            {
                "op": "review",
                "fact_id": "a",
                "reason": "August review",
                "review_at": "2026-10-01T00:00:00Z",
                "review_basis": "explicit",
            },
        ),
    )
    point["now"] = datetime(2026, 9, 1, tzinfo=UTC)
    september = FactLedger(root, clock=clock)
    newer = _plan(
        september,
        (
            {
                "op": "review",
                "fact_id": "a",
                "reason": "September review",
                "review_at": "2026-11-01T00:00:00Z",
                "review_basis": "explicit",
            },
        ),
    )
    publication = august._publication_operations
    interleaved = False

    def publish_after_competitor(plan, snapshot):
        nonlocal interleaved
        if not interleaved:
            interleaved = True
            september.commit(newer)
        return publication(plan, snapshot)

    monkeypatch.setattr(august, "_publication_operations", publish_after_competitor)
    with pytest.raises(FactConflictError, match="fact changed"):
        august.commit(old)
    assert (august.records_root / "2026-08.jsonl").exists() is False
    assert (august.records_root / "2026-09.jsonl").is_file()


def test_ordinary_fact_changes_still_use_the_plan_clock(tmp_path: Path) -> None:
    point, clock = _clock(datetime(2026, 8, 1, 8, 30, tzinfo=UTC))
    ledger = FactLedger(tmp_path / "facts", clock=clock)
    _commit(ledger, _create("a", "Clock-created"))
    point["now"] = datetime(2026, 8, 2, 9, 45, tzinfo=UTC)
    _commit(
        ledger,
        {
            "op": "review",
            "fact_id": "a",
            "reason": "ordinary review",
            "review_at": "2026-09-01T00:00:00Z",
            "review_basis": "explicit",
        },
    )
    assert ledger.get("a").created_at == datetime(2026, 8, 1, 8, 30, tzinfo=UTC)
    assert ledger.get("a").reviewed_at == datetime(2026, 8, 2, 9, 45, tzinfo=UTC)


def test_empty_head_cross_month_exact_duplicate_conflicts_on_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    point, clock = _clock(datetime(2026, 8, 1, tzinfo=UTC))
    root = tmp_path / "facts"
    august = FactLedger(root, clock=clock)
    first = _plan(august, (_create("a", "Same fact"),))
    point["now"] = datetime(2026, 9, 1, tzinfo=UTC)
    september = FactLedger(root, clock=clock)
    second = _plan(september, (_create("b", " same  fact "),))
    publication = august._publication_operations
    interleaved = False

    def publish_after_duplicate(plan, snapshot):
        nonlocal interleaved
        if not interleaved:
            interleaved = True
            september.commit(second)
        return publication(plan, snapshot)

    monkeypatch.setattr(august, "_publication_operations", publish_after_duplicate)
    with pytest.raises(FactConflictError, match="duplicate window"):
        august.commit(first)
    assert august.get("b").text == " same  fact "
    assert (august.records_root / "2026-08.jsonl").exists() is False
    assert (august.records_root / "2026-09.jsonl").is_file()


def test_concurrent_create_of_same_fact_id_conflicts(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    first = _plan(ledger, (_create("a", "First"),))
    contender = FactLedger(ledger.root, clock=lambda: JULY)
    second = _plan(contender, (_create("a", "Second"),))
    ledger.commit(first)
    with pytest.raises(FactConflictError, match="created since plan"):
        contender.commit(second)
    assert ledger.get("a").text == "First"


def test_same_plan_from_two_ledger_instances_returns_exact_events(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "facts"
    first = FactLedger(root, clock=lambda: JULY)
    plan = _plan(first, (_create("a", "One"),))
    second = FactLedger(root, clock=lambda: JULY)
    publication = first._publication_operations
    second_result = ()

    def publish_after_other_instance(plan, snapshot):
        nonlocal second_result
        if not second_result:
            second_result = second.commit(plan)
        return publication(plan, snapshot)

    monkeypatch.setattr(first, "_publication_operations", publish_after_other_instance)
    first_result = first.commit(plan)
    expected = [_canonical(event.record) for event in plan.events]
    assert [_canonical(event.record) for event in first_result] == expected
    assert [_canonical(event.record) for event in second_result] == expected


def test_unrelated_same_month_interleaving_rebases(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ledger = _ledger(tmp_path)
    _commit(ledger, _create("a", "One"), _create("b", "Two"))
    first = _plan(
        ledger,
        (
            {
                "op": "review",
                "fact_id": "a",
                "reason": "review a",
                "review_at": "2026-10-01T00:00:00Z",
                "review_basis": "explicit",
            },
        ),
    )
    other = FactLedger(ledger.root, clock=lambda: JULY)
    second = _plan(
        other,
        (
            {
                "op": "review",
                "fact_id": "b",
                "reason": "review b",
                "review_at": "2026-10-02T00:00:00Z",
                "review_basis": "explicit",
            },
        ),
    )
    publish = ledger._repository.commit
    interleaved = False

    def commit_after_other(change_set: ChangeSet):
        nonlocal interleaved
        if change_set.idempotency_key.startswith("fact-commit:") and not interleaved:
            interleaved = True
            other.commit(second)
        return publish(change_set)

    monkeypatch.setattr(ledger._repository, "commit", commit_after_other)
    ledger.commit(first)
    assert ledger.get("a").reviewed_at == JULY
    assert ledger.get("b").reviewed_at == JULY


def test_snapshot_retries_when_head_changes_between_pinned_read_and_materialization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = _ledger(tmp_path)
    _commit(ledger, _create("a", "One"))
    writer = FactLedger(ledger.root, clock=lambda: JULY)
    plan = _plan(
        writer,
        (
            {
                "op": "review",
                "fact_id": "a",
                "reason": "concurrent review",
                "review_at": "2026-10-01T00:00:00Z",
                "review_basis": "explicit",
            },
        ),
    )
    read = ledger._repository.read
    interleaved = False

    def read_then_commit(resource_id: str, *, at: str | None = None):
        nonlocal interleaved
        result = read(resource_id, at=at)
        if at is not None and not interleaved:
            interleaved = True
            writer.commit(plan)
        return result

    monkeypatch.setattr(ledger._repository, "read", read_then_commit)
    assert ledger.get("a").reviewed_at == JULY
    assert interleaved


def test_snapshot_retries_transient_materialization_mismatch_at_stable_head(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = _ledger(tmp_path)
    _commit(ledger, _create("a", "One"))
    read_materialized = ledger._read_materialized
    mismatches = 0

    def transient_mismatch(resource_id: str, path: str, *, at: str | None = None) -> bytes:
        nonlocal mismatches
        content = read_materialized(resource_id, path, at=at)
        if mismatches == 0:
            mismatches += 1
            raise FactLedgerCorruptionError("transient materialization mismatch")
        return content

    monkeypatch.setattr(ledger, "_read_materialized", transient_mismatch)
    assert ledger.get("a").text == "One"
    assert mismatches == 1


def test_snapshot_fails_closed_after_persistent_materialization_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = _ledger(tmp_path)
    _commit(ledger, _create("a", "One"))

    def persistent_mismatch(_resource_id: str, _path: str, *, at: str | None = None) -> bytes:
        raise FactLedgerCorruptionError("persistent materialization mismatch")

    monkeypatch.setattr(ledger, "_read_materialized", persistent_mismatch)
    with pytest.raises(FactLedgerCorruptionError, match="persistent materialization mismatch"):
        ledger.search()


def test_duplicate_window_count_and_external_corruption(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    _commit(ledger, _create("a", "One", subjects=["alpha", "beta"]))
    duplicate = _plan(ledger, (_create("b", " Same ", subjects=["beta"]),))
    _commit(ledger, _create("c", "same", subjects=["beta"]))
    with pytest.raises(FactConflictError, match="duplicate"):
        ledger.commit(duplicate)
    assert ledger.active_subject_count("beta", scope_kind="area", scope_key="project") == 2
    path = ledger.records_root / "2026-07.jsonl"
    path.write_text("{}\n", encoding="utf-8")
    with pytest.raises(FactLedgerCorruptionError, match="drifted"):
        ledger.search()


def test_committed_malformed_event_fails_closed(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    _commit(ledger, _create("a", "One"))
    resource = ledger._repository.get("2026-07.jsonl")
    ledger._repository.commit(
        ChangeSet(
            (Update(resource.resource_id, resource.version_id, b"{}\n"),),
            "test",
            "test",
            "corrupt canonical event",
            "external-corruption",
        )
    )
    with pytest.raises(FactLedgerCorruptionError, match="unknown event fields"):
        ledger.search()


def test_managed_month_rewrite_violates_append_only_prefix(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    _commit(ledger, _create("a", "Original"))
    resource = ledger._repository.get("2026-07.jsonl")
    event = json.loads(ledger._repository.read(resource.resource_id))
    event["payload"]["text"] = "Rewritten"
    event["payload"]["normalized_text"] = "rewritten"
    ledger._repository.commit(
        ChangeSet(
            (
                Update(
                    resource.resource_id,
                    resource.version_id,
                    (_canonical(event) + "\n").encode(),
                ),
            ),
            "test",
            "test",
            "rewrite historical prefix",
            "rewrite-prefix",
        )
    )
    with pytest.raises(FactLedgerCorruptionError, match="append-only prefix"):
        ledger.search()
