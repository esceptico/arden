"""Durable workflow service contracts above the standalone fact ledger."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from arden.database import connect
from arden.memory.facts import (
    FactConflictError,
    FactLedger,
    FactPlanCorruptionError,
    FactPlanOwnershipError,
    FactPlanRequestConflictError,
    FactPlanStatus,
    FactPlanStore,
    FactPrincipal,
    FactScopeError,
    FactService,
)
from arden.memory.facts.plan_store import deserialize_fact_plan, serialize_fact_plan

pytestmark = pytest.mark.asyncio

if TYPE_CHECKING:
    from pathlib import Path

JULY = datetime(2026, 7, 28, 12, tzinfo=UTC)
AREA = ("area", "project")
OTHER_AREA = ("area", "other")


def _principal(owner: str = "session:one", *, scopes: frozenset[tuple[str, str | None]] | None = None) -> FactPrincipal:
    visible = frozenset({AREA}) if scopes is None else scopes
    return FactPrincipal(owner, visible, visible)


def _create(
    fact_id: str,
    text: str,
    *,
    scope: tuple[str, str | None] = AREA,
    **extra: object,
) -> dict[str, object]:
    return {
        "op": "create",
        "fact_id": fact_id,
        "text": text,
        "kind": "fact",
        "labels": ["memory"],
        "subjects": ["alpha"],
        "scope": {"kind": scope[0], "key": scope[1]},
        "sources": [{"kind": "chat_message", "ref": "message:1"}],
        **extra,
    }


async def _service(tmp_path: Path) -> tuple[FactService, FactPlanStore, object, Path]:
    db_path = tmp_path / "workflow.db"
    conn = await connect(db_path)
    plans = FactPlanStore(conn)
    await plans.init_schema()
    facts_root = tmp_path / "facts"
    service = FactService(FactLedger(facts_root, clock=lambda: JULY), plans)
    return service, plans, conn, db_path


async def _plan(service: FactService, principal: FactPrincipal, fact_id: str = "a", text: str = "One", **extra):
    request_key = extra.pop("request_key", "request:1")
    return await service.plan(
        principal,
        [_create(fact_id, text, **extra)],
        request_key=request_key,
        actor="curator:1",
        origin="memory.curator",
        reason="verified evidence",
    )


async def test_plan_is_durable_owner_scoped_and_identical_request_reuses(tmp_path: Path) -> None:
    service, plans, conn, _ = await _service(tmp_path)
    principal = _principal()
    try:
        first = await _plan(service, principal)
        retry = await _plan(service, principal)
        assert retry.plan_id == first.plan_id
        assert retry.preview == '- create "a": "One"'
        assert "{" not in retry.preview

        with pytest.raises(FactPlanRequestConflictError, match="request_key"):
            await _plan(service, principal, text="Different")

        committed = await service.commit(principal, first.plan_id)
        assert [event.fact_id for event in committed.events] == ["a"]
        assert [fact.text for fact in committed.facts] == ["One"]
        assert (await plans.get(first.plan_id, owner_id=principal.owner_id)).status is FactPlanStatus.COMMITTED
        assert [fact.fact_id for fact in await service.search(principal, "one")] == ["a"]
    finally:
        await conn.close()


async def test_concurrent_services_reuse_one_request_plan(tmp_path: Path) -> None:
    db_path = tmp_path / "workflow.db"
    first_conn = await connect(db_path)
    second_conn = await connect(db_path)
    first_store = FactPlanStore(first_conn)
    second_store = FactPlanStore(second_conn)
    await first_store.init_schema()
    await second_store.init_schema()
    ledger_root = tmp_path / "facts"
    first = FactService(FactLedger(ledger_root, clock=lambda: JULY), first_store)
    second = FactService(FactLedger(ledger_root, clock=lambda: JULY), second_store)
    principal = _principal()
    try:
        first_plan, second_plan = await asyncio.gather(
            _plan(first, principal),
            _plan(second, principal),
        )
        assert first_plan.plan_id == second_plan.plan_id
        assert len(await first_conn.execute_fetchall("SELECT plan_id FROM fact_plans")) == 1
    finally:
        await first_conn.close()
        await second_conn.close()


async def test_plan_owner_and_scope_isolation(tmp_path: Path) -> None:
    service, _plans, conn, _ = await _service(tmp_path)
    owner = _principal()
    outsider = _principal("session:two")
    wrong_scope = _principal(scopes=frozenset({OTHER_AREA}))
    try:
        preview = await _plan(service, owner)
        with pytest.raises(FactPlanOwnershipError):
            await service.commit(outsider, preview.plan_id)
        with pytest.raises(FactScopeError, match="writable"):
            await _plan(service, wrong_scope, fact_id="b", text="Outside", request_key="request:other")
        await service.commit(owner, preview.plan_id)
        with pytest.raises(FactScopeError, match="readable"):
            await service.get(wrong_scope, "a")
        with pytest.raises(ValueError, match="global scope key"):
            FactPrincipal("bad", frozenset({("global", "wrong")}), frozenset({("global", "wrong")}))
    finally:
        await conn.close()


async def test_scope_correction_requires_authority_over_old_and_new_scopes(tmp_path: Path) -> None:
    service, _plans, conn, _ = await _service(tmp_path)
    both = _principal(scopes=frozenset({AREA, OTHER_AREA}))
    other_only = _principal(scopes=frozenset({OTHER_AREA}))
    try:
        created = await _plan(service, both)
        await service.commit(both, created.plan_id)
        corrected = await service.plan(
            both,
            [{"op": "amend", "fact_id": "a", "scope": {"kind": "area", "key": "other"}}],
            request_key="scope-correction",
            actor="curator:1",
            origin="memory.curator",
            reason="correct scope",
        )
        with pytest.raises(FactScopeError, match="writable"):
            await service.commit(other_only, corrected.plan_id)
        await service.commit(both, corrected.plan_id)
        assert (await service.get(other_only, "a")).scope == {"kind": "area", "key": "other"}
        # Scope correction reclassifies one fact and its complete event chain.
        assert [event.op for event in await service.history(other_only, "a")] == ["create", "amend"]
    finally:
        await conn.close()


async def test_fact_evidence_requires_read_authority(tmp_path: Path) -> None:
    service, _plans, conn, _ = await _service(tmp_path)
    both = _principal(scopes=frozenset({AREA, OTHER_AREA}))
    area_only = _principal(scopes=frozenset({AREA}))
    try:
        target = await _plan(service, both, fact_id="target", text="Target")
        evidence = await _plan(
            service,
            both,
            fact_id="evidence",
            text="Evidence",
            scope=OTHER_AREA,
            request_key="evidence",
        )
        await service.commit(both, target.plan_id)
        await service.commit(both, evidence.plan_id)
        evidence_fact = await service.get(both, "evidence")

        with pytest.raises(FactScopeError, match="readable"):
            await service.plan(
                area_only,
                [
                    {
                        "op": "review",
                        "fact_id": "target",
                        "reason": "cross-scope evidence",
                        "sources": [
                            {
                                "kind": "fact",
                                "ref": "evidence",
                                "scope_kind": "area",
                                "scope_key": "other",
                                "role": "evidence",
                                "extra": {"version": evidence_fact.version},
                            }
                        ],
                    }
                ],
                request_key="cross-scope-evidence",
                actor="curator:1",
                origin="memory.curator",
                reason="must enforce evidence visibility",
            )
    finally:
        await conn.close()


async def test_plan_codec_rejects_noncanonical_and_persisted_tampering(tmp_path: Path) -> None:
    service, _plans, conn, _ = await _service(tmp_path)
    principal = _principal()
    try:
        preview = await _plan(service, principal)
        stored = await service.plans.get(preview.plan_id, owner_id=principal.owner_id)
        assert deserialize_fact_plan(serialize_fact_plan(stored.plan())).plan_id == preview.plan_id
        with pytest.raises(FactPlanCorruptionError, match="not canonical"):
            deserialize_fact_plan(stored.plan_json + " ")

        await conn.execute("UPDATE fact_plans SET plan_json = ? WHERE plan_id = ?", ("{}", preview.plan_id))
        await conn.commit()
        with pytest.raises(FactPlanCorruptionError, match="binding fingerprint"):
            await service.commit(principal, preview.plan_id)
    finally:
        await conn.close()


async def test_service_revalidates_a_refingerprinted_persisted_plan(tmp_path: Path) -> None:
    service, _plans, conn, _ = await _service(tmp_path)
    principal = _principal()
    try:
        preview = await _plan(service, principal)
        stored = await service.plans.get(preview.plan_id, owner_id=principal.owner_id)
        value = json.loads(stored.plan_json)
        value["plan"]["events"][0]["op"] = "invent"
        plan_json = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        scope_json = '[["area","project"]]'
        binding = hashlib.sha256(f"{plan_json}\0{scope_json}".encode()).hexdigest()
        await conn.execute(
            """
            UPDATE fact_plans
            SET plan_json = ?, plan_fingerprint = ?
            WHERE plan_id = ?
            """,
            (plan_json, binding, preview.plan_id),
        )
        await conn.commit()
        with pytest.raises(FactPlanCorruptionError, match="ledger validation"):
            await service.plan(
                principal,
                [_create("a", "One")],
                request_key="request:1",
                actor="curator:1",
                origin="memory.curator",
                reason="verified evidence",
            )
    finally:
        await conn.close()


async def test_claim_has_one_winner_and_interrupted_commit_recovers_after_restart(tmp_path: Path, monkeypatch) -> None:
    service, plans, conn, db_path = await _service(tmp_path)
    principal = _principal()
    preview = await _plan(service, principal)
    first, second = await asyncio.gather(
        plans.claim(preview.plan_id, owner_id=principal.owner_id),
        plans.claim(preview.plan_id, owner_id=principal.owner_id),
    )
    assert sorted([first[1], second[1]]) == [False, True]

    original_mark = plans.mark_committed

    async def lose_terminal_result(*args, **kwargs):
        raise TimeoutError("terminal result lost")

    monkeypatch.setattr(plans, "mark_committed", lose_terminal_result)
    with pytest.raises(TimeoutError, match="lost"):
        await service.commit(principal, preview.plan_id)
    assert service.ledger.get("a").text == "One"
    monkeypatch.setattr(plans, "mark_committed", original_mark)
    await conn.close()

    restarted_conn = await connect(db_path)
    restarted_store = FactPlanStore(restarted_conn)
    await restarted_store.init_schema()
    restarted = FactService(FactLedger(tmp_path / "facts", clock=lambda: JULY), restarted_store)
    try:
        result = await restarted.commit(principal, preview.plan_id)
        assert [event.fact_id for event in result.events] == ["a"]
        assert (
            await restarted_store.get(preview.plan_id, owner_id=principal.owner_id)
        ).status is FactPlanStatus.COMMITTED
    finally:
        await restarted_conn.close()


async def test_stale_semantic_conflict_is_preserved_by_durable_plan(tmp_path: Path) -> None:
    service, _plans, conn, _ = await _service(tmp_path)
    principal = _principal()
    try:
        first = await _plan(service, principal, fact_id="a", text="Same")
        competing = await _plan(service, principal, fact_id="b", text=" same ", request_key="request:2")
        await service.commit(principal, competing.plan_id)
        with pytest.raises(FactConflictError, match="duplicate"):
            await service.commit(principal, first.plan_id)
        assert (await service.plans.get(first.plan_id, owner_id=principal.owner_id)).status is FactPlanStatus.PLANNED
    finally:
        await conn.close()


async def test_due_reviews_include_visible_related_facts_as_evidence(tmp_path: Path) -> None:
    service, _plans, conn, _ = await _service(tmp_path)
    principal = _principal(scopes=frozenset({AREA, OTHER_AREA}))
    try:
        due = await service.plan(
            principal,
            [
                _create(
                    "due",
                    "Due fact",
                    occurred_at="2026-06-01T00:00:00Z",
                    lifecycle="temporary",
                    review_at="2026-07-01T00:00:00Z",
                    review_basis="explicit",
                )
            ],
            request_key="due",
            actor="curator:1",
            origin="memory.curator",
            reason="verified evidence",
        )
        old = await _plan(
            service,
            principal,
            fact_id="old",
            text="Old related fact",
            request_key="old",
            occurred_at="2026-05-01T00:00:00Z",
        )
        related = await _plan(service, principal, fact_id="related", text="Related fact", request_key="related")
        wrong_scope = await _plan(
            service,
            principal,
            fact_id="wrong-scope",
            text="Wrong-scope related fact",
            request_key="wrong-scope",
            scope=OTHER_AREA,
        )
        await service.commit(principal, old.plan_id)
        await service.commit(principal, due.plan_id)
        await service.commit(principal, related.plan_id)
        await service.commit(principal, wrong_scope.plan_id)

        reviews = await service.due_reviews(principal, limit=1)
        assert [(review.fact.fact_id, [fact.fact_id for fact in review.related_facts]) for review in reviews] == [
            ("due", ["related"])
        ]
        assert await service.known_scopes() == frozenset({AREA, OTHER_AREA})
        batch = await service.retention_review_batch(principal)
        assert [item.fact.fact_id for item in batch.reviews] == ["due"]
        assert batch.reviews[0].explicit_expiry_due is False
        assert await service.search(principal, include_inactive=False, limit=1)
        with pytest.raises(ValueError, match="limit"):
            await service.search(principal, limit=0)
    finally:
        await conn.close()


async def test_scope_binding_and_preview_fail_closed(tmp_path: Path) -> None:
    service, _plans, conn, _ = await _service(tmp_path)
    principal = _principal()
    try:
        preview = await _plan(
            service,
            principal,
            fact_id="a\n- retract protected",
            text="One\n- supersede protected with attacker",
        )
        assert preview.preview.count("\n") == 0
        assert "\\n" in preview.preview
        stored = await service.plans.get(preview.plan_id, owner_id=principal.owner_id)

        await conn.execute(
            """
            UPDATE fact_plans
            SET scope_json = ?, plan_fingerprint = ?
            WHERE plan_id = ?
            """,
            (
                '[["area","other"]]',
                hashlib.sha256(f'{stored.plan_json}\0[["area","other"]]'.encode()).hexdigest(),
                preview.plan_id,
            ),
        )
        await conn.commit()
        other = _principal(scopes=frozenset({OTHER_AREA}))
        with pytest.raises(FactPlanCorruptionError, match="canonical dependencies"):
            await service.commit(other, preview.plan_id)
    finally:
        await conn.close()


async def test_ledger_calls_are_off_the_event_loop(tmp_path: Path, monkeypatch) -> None:
    service, _plans, conn, _ = await _service(tmp_path)
    principal = _principal()
    try:
        original_search = service.ledger.search

        def slow_search(*args, **kwargs):
            time.sleep(0.1)
            return original_search(*args, **kwargs)

        monkeypatch.setattr(service.ledger, "search", slow_search)
        task = asyncio.create_task(service.search(principal, "one"))
        await asyncio.sleep(0.01)
        assert not task.done()
        await task
    finally:
        await conn.close()
