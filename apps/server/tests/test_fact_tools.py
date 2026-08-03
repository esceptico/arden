"""Structured fact-tool contracts above FactService."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from arden.constants import BUILTIN_MEMORY_RETENTION_ID
from arden.context.models import AreaContext, SessionState
from arden.database import connect
from arden.integrations.core import FACTS
from arden.memory.facts.boundary import source_time
from arden.memory.facts.ledger import FactLedger
from arden.memory.facts.plan_store import FactPlanStore
from arden.memory.facts.service import FactPrincipal, FactService
from arden.tools.core.context import BackgroundTaskRegistry, IOBridge, RunContext, ToolContext, ToolExecution
from arden.tools.core.registry import ToolRegistry
from arden.tools.facts import (
    FACT_SERVICE,
    commit_fact_changes_tool,
    get_due_fact_reviews_tool,
    get_fact_history_tool,
    get_fact_tool,
    plan_fact_changes_tool,
    search_facts_tool,
)

pytestmark = pytest.mark.asyncio
JULY = datetime(2026, 7, 28, 12, tzinfo=UTC)
AREA = AreaContext(area_id="project", name="Project")


async def _service(tmp_path: Path) -> tuple[FactService, object]:
    connection = await connect(tmp_path / "plans.sqlite")
    plans = FactPlanStore(connection)
    await plans.init_schema()
    return FactService(FactLedger(tmp_path / "facts", clock=lambda: JULY), plans), connection


def _execution(
    service: FactService,
    *,
    tool_id: str,
    tool_name: str,
    area: AreaContext | None = AREA,
    run_id: str = "run-1",
    session_id: str = "session-1",
    origin_automation_id: str | None = None,
    run_automation_id: str | None = None,
) -> ToolExecution:
    ctx = ToolContext(
        session_state=SessionState(
            session_id=session_id,
            started_at=JULY,
            origin_automation_id=origin_automation_id,
        ),
        registry=ToolRegistry(),
        run=RunContext(run_id=run_id, automation_id=run_automation_id),
        io=IOBridge(),
        services={FACT_SERVICE: service},
        area=area,
        background_tasks=BackgroundTaskRegistry(session_id=session_id),
    )
    return ToolExecution(tool_id=tool_id, tool_name=tool_name, ctx=ctx)


def _create(*, fact_id: str, text: str, **extra: object) -> dict[str, object]:
    return {
        "op": "create",
        "fact_id": fact_id,
        "text": text,
        "kind": "fact",
        "subjects": ["alpha"],
        **extra,
    }


async def _commit_direct(
    service: FactService,
    fact_id: str,
    text: str,
    *,
    scope: tuple[str, str | None],
    **extra: object,
) -> None:
    principal = FactPrincipal("seed", frozenset({scope}), frozenset({scope}))
    occurred_at = extra.pop("occurred_at", None)
    original_clock = service.ledger._clock
    if occurred_at is not None:
        service.ledger._clock = lambda: datetime.fromisoformat(str(occurred_at).replace("Z", "+00:00"))
    try:
        preview = await service.plan(
            principal,
            [
                {
                    **_create(fact_id=fact_id, text=text, **extra),
                    "scope": {"kind": scope[0], "key": scope[1]},
                    "sources": [{"kind": "test", "ref": fact_id}],
                }
            ],
            request_key=f"seed:{fact_id}",
            actor="seed",
            origin="test",
            reason="seed",
        )
    finally:
        service.ledger._clock = original_clock
    await service.commit(principal, preview.plan_id)


async def test_plan_and_commit_use_server_derived_identity_scope_and_provenance(tmp_path: Path) -> None:
    service, connection = await _service(tmp_path)
    try:
        plan = await plan_fact_changes_tool.execute(
            _execution(service, tool_id="plan-1", tool_name="fact_plan_changes"),
            changes=[_create(fact_id="one", text="One fact")],
            reason="User stated this.",
        )
        assert not plan.is_error
        retry = await plan_fact_changes_tool.execute(
            _execution(
                service,
                tool_id="plan-1",
                tool_name="fact_plan_changes",
                run_id="rehydrated-run",
            ),
            changes=[_create(fact_id="one", text="One fact")],
            reason="User stated this.",
        )
        assert retry.data["plan_id"] == plan.data["plan_id"]
        json.dumps(plan.data)
        event = plan.data["events"][0]
        assert event["actor"] == "session:session-1"
        assert event["origin"] == "tool.fact_changes"
        assert event["payload"]["scope"] == {"kind": "area", "key": "project"}
        assert event["sources"]["items"][0]["kind"] == "tool_call"
        assert "dependencies" not in plan.data
        assert "duplicate_windows" not in plan.data

        plan_id = plan.content.splitlines()[0].removeprefix("Fact change plan ID: ")
        assert plan_id == plan.data["plan_id"]
        committed = await commit_fact_changes_tool.execute(
            _execution(service, tool_id="commit-1", tool_name="fact_commit_changes"),
            plan_id=plan_id,
        )
        assert not committed.is_error
        json.dumps(committed.data)
        assert committed.outcome is not None and committed.outcome.effect is not None
        assert committed.outcome.effect.operation == "commit"

        fact = await get_fact_tool.execute(_execution(service, tool_id="get-1", tool_name="fact_get"), fact_id="one")
        json.dumps(fact.data)
        assert fact.data["fact"]["scope"] == {"kind": "area", "key": "project"}
        assert fact.data["fact"]["sources"]["total"] == 1
        assert "version" not in fact.data["fact"]
    finally:
        await connection.close()


async def test_commit_reports_durable_fact_when_post_commit_refresh_fails(tmp_path: Path) -> None:
    service, connection = await _service(tmp_path)

    async def fail_refresh() -> None:
        raise RuntimeError("projection unavailable")

    service.post_commit = fail_refresh
    try:
        plan = await plan_fact_changes_tool.execute(
            _execution(service, tool_id="plan-1", tool_name="fact_plan_changes"),
            changes=[_create(fact_id="one", text="One fact")],
            reason="User stated this.",
        )

        result = await commit_fact_changes_tool.execute(
            _execution(service, tool_id="commit-1", tool_name="fact_commit_changes"),
            plan_id=plan.data["plan_id"],
        )

        assert result.is_error
        assert result.outcome is not None and result.outcome.error is not None
        assert result.outcome.error.code == "post_commit_failed"
        assert result.outcome.error.retryable is True
        assert result.data["committed"] is True
        assert result.data["facts"][0]["fact_id"] == "one"
        assert service.ledger.get("one").text == "One fact"
    finally:
        await connection.close()


async def test_planning_surfaces_an_available_session_service_failure(tmp_path: Path) -> None:
    service, connection = await _service(tmp_path)

    class _FailingSessionService:
        async def list_messages(self, _session_id: str, *, limit: int) -> dict[str, object]:
            raise RuntimeError("session store unavailable")

    try:
        execution = _execution(service, tool_id="plan-1", tool_name="fact_plan_changes")
        execution.ctx.services["session"] = _FailingSessionService()

        result = await plan_fact_changes_tool.execute(
            execution,
            changes=[_create(fact_id="one", text="One fact")],
            reason="User stated this.",
        )

        assert result.is_error
        assert result.outcome is not None and result.outcome.error is not None
        assert result.outcome.error.code == "fact_service_error"
        assert result.outcome.error.retryable is True
    finally:
        await connection.close()


async def test_fact_source_time_normalizes_utc_without_false_precision() -> None:
    assert source_time("2026-07-28") == ("2026-07-28", "day")
    assert source_time("2026-07-28T12:00:00+04:00") == ("2026-07-28T12:00:00+04:00", "second")
    assert source_time("2026-07-28T12:00:00.1234+04:00") == ("2026-07-28T12:00:00.123+04:00", "millisecond")
    assert source_time("not a timestamp") == (None, "unknown")


async def test_read_tools_page_stably_and_enforce_server_derived_area_scope(tmp_path: Path) -> None:
    service, connection = await _service(tmp_path)
    try:
        await _commit_direct(service, "other", "Hidden", scope=("area", "other"))
        for fact_id in ("a", "b", "c"):
            await _commit_direct(service, fact_id, fact_id.upper(), scope=("area", "project"))

        first = await search_facts_tool.execute(
            _execution(service, tool_id="search-1", tool_name="fact_search"), limit=2
        )
        second = await search_facts_tool.execute(
            _execution(service, tool_id="search-2", tool_name="fact_search"),
            limit=2,
            cursor=first.data["next_cursor"],
        )
        assert [fact["fact_id"] for fact in first.data["facts"]] == ["a", "b"]
        assert [fact["fact_id"] for fact in second.data["facts"]] == ["c"]
        assert first.data["total"] == 3
        assert "items" not in first.data
        json.dumps(first.data)
        wrong_filter = await search_facts_tool.execute(
            _execution(service, tool_id="search-wrong", tool_name="fact_search"),
            query="A",
            limit=2,
            cursor=first.data["next_cursor"],
        )
        assert wrong_filter.outcome.error.code == "invalid_ref"

        await _commit_direct(service, "d", "D", scope=("area", "project"))
        stale = await search_facts_tool.execute(
            _execution(service, tool_id="search-stale", tool_name="fact_search"),
            limit=2,
            cursor=first.data["next_cursor"],
        )
        assert stale.outcome.error.code == "write_conflict"

        history = await get_fact_history_tool.execute(
            _execution(service, tool_id="history-1", tool_name="fact_history"), fact_id="a", limit=1
        )
        assert history.data["events"][0]["op"] == "create"
        assert history.data["events"][0]["sources"]["items"][0]["kind"] == "test"
        json.dumps(history.data)
    finally:
        await connection.close()


async def test_due_reviews_return_evidence_not_a_recommended_action(tmp_path: Path) -> None:
    service, connection = await _service(tmp_path)
    try:
        plan = await plan_fact_changes_tool.execute(
            _execution(service, tool_id="plan-due", tool_name="fact_plan_changes"),
            changes=[
                _create(
                    fact_id="due",
                    text="Review me",
                    lifecycle="temporary",
                    expires_at="2026-07-01T00:00:00Z",
                )
            ],
            reason="Temporary item.",
        )
        await commit_fact_changes_tool.execute(
            _execution(service, tool_id="commit-due", tool_name="fact_commit_changes"), plan_id=plan.data["plan_id"]
        )
        due = await get_due_fact_reviews_tool.execute(
            _execution(service, tool_id="due-1", tool_name="fact_due_reviews"), limit=1
        )
        assert due.data["reviews"][0]["fact"]["fact_id"] == "due"
        assert "recommend" not in due.content.lower()
        json.dumps(due.data)
    finally:
        await connection.close()


async def test_retention_uses_all_scopes_but_only_due_evidence_backed_lifecycle_changes(tmp_path: Path) -> None:
    service, connection = await _service(tmp_path)

    def retention(tool_id: str, tool_name: str) -> ToolExecution:
        return _execution(
            service,
            tool_id=tool_id,
            tool_name=tool_name,
            area=None,
            session_id="retention-run",
            origin_automation_id=BUILTIN_MEMORY_RETENTION_ID,
            run_automation_id=BUILTIN_MEMORY_RETENTION_ID,
        )

    try:
        await _commit_direct(
            service,
            "due-project",
            "Project claim",
            scope=("area", "project"),
            lifecycle="temporary",
            expires_at="2026-07-01T00:00:00Z",
            occurred_at="2026-06-01T00:00:00Z",
        )
        await _commit_direct(
            service,
            "successor",
            "New project claim",
            scope=("area", "project"),
            occurred_at="2026-07-01T00:00:00Z",
        )
        await _commit_direct(
            service,
            "old-project",
            "Old project claim",
            scope=("area", "project"),
            occurred_at="2026-05-01T00:00:00Z",
        )
        await _commit_direct(
            service,
            "due-other",
            "Other claim",
            scope=("area", "other"),
            lifecycle="temporary",
            review_at="2026-07-01T00:00:00Z",
            review_basis="explicit",
        )
        await _commit_direct(
            service,
            "future",
            "Future review",
            scope=("area", "project"),
            lifecycle="temporary",
            review_at="2026-08-01T00:00:00Z",
            review_basis="explicit",
        )

        due = await get_due_fact_reviews_tool.execute(
            retention("retention-due", "fact_due_reviews"),
            limit=10,
        )
        assert {item["fact"]["fact_id"] for item in due.data["reviews"]} == {"due-project", "due-other"}
        project_review = next(item for item in due.data["reviews"] if item["fact"]["fact_id"] == "due-project")
        assert project_review["explicit_expiry_due"] is True
        assert [item["fact_id"] for item in project_review["related_facts"]] == ["future", "successor"]
        other_review = next(item for item in due.data["reviews"] if item["fact"]["fact_id"] == "due-other")
        assert other_review["explicit_expiry_due"] is False

        descriptive_origin_only = await get_fact_tool.execute(
            _execution(
                service,
                tool_id="interactive-get",
                tool_name="fact_get",
                origin_automation_id=BUILTIN_MEMORY_RETENTION_ID,
            ),
            fact_id="due-other",
        )
        assert descriptive_origin_only.outcome.error.code == "permission_denied"

        forbidden = await plan_fact_changes_tool.execute(
            retention("retention-create", "fact_plan_changes"),
            changes=[_create(fact_id="invented", text="Invented")],
            reason="Retention must not create facts.",
        )
        assert forbidden.outcome.error.code == "invalid_input"

        future = await plan_fact_changes_tool.execute(
            retention("retention-future", "fact_plan_changes"),
            changes=[
                {
                    "op": "review",
                    "fact_id": "future",
                    "certainty": "uncertain",
                    "reason": "Not due.",
                }
            ],
            reason="Not due.",
        )
        assert future.outcome.error.code == "invalid_input"

        cross_scope = await plan_fact_changes_tool.execute(
            retention("retention-cross", "fact_plan_changes"),
            changes=[
                {
                    "op": "review",
                    "fact_id": "due-project",
                    "certainty": "confirmed",
                    "evidence_fact_ids": ["due-other"],
                    "reason": "Wrong scope.",
                }
            ],
            reason="Wrong scope.",
        )
        assert cross_scope.outcome.error.code == "invalid_input"

        old_evidence = await plan_fact_changes_tool.execute(
            retention("retention-old", "fact_plan_changes"),
            changes=[
                {
                    "op": "expire",
                    "fact_id": "due-project",
                    "evidence_fact_ids": ["old-project"],
                    "reason": "Old evidence must not decide expiry.",
                }
            ],
            reason="Old evidence.",
        )
        assert old_evidence.outcome.error.code == "invalid_input"

        self_without_expiry = await plan_fact_changes_tool.execute(
            retention("retention-self", "fact_plan_changes"),
            changes=[
                {
                    "op": "expire",
                    "fact_id": "due-other",
                    "evidence_fact_ids": ["due-other"],
                    "reason": "A review date is not an expiry.",
                }
            ],
            reason="No explicit expiry.",
        )
        assert self_without_expiry.outcome.error.code == "invalid_input"

        plan = await plan_fact_changes_tool.execute(
            retention("retention-plan", "fact_plan_changes"),
            changes=[
                {
                    "op": "supersede",
                    "fact_id": "due-project",
                    "successor_id": "successor",
                    "evidence_fact_ids": ["successor"],
                    "reason": "Newer direct evidence.",
                },
                {
                    "op": "review",
                    "fact_id": "due-other",
                    "certainty": "uncertain",
                    "review_at": "2026-10-28T12:00:00Z",
                    "review_basis": "inferred",
                    "reason": "No newer evidence found.",
                },
            ],
            reason="Review every currently due fact.",
        )
        assert not plan.is_error
        supersession = plan.data["events"][0]
        assert supersession["actor"] == f"automation:{BUILTIN_MEMORY_RETENTION_ID}"
        assert supersession["origin"] == "memory.retention"
        assert supersession["sources"]["items"][1]["kind"] == "fact"
        assert supersession["sources"]["items"][1]["ref"] == "successor"
        assert "extra" not in supersession["sources"]["items"][1]

        committed = await commit_fact_changes_tool.execute(
            retention("retention-commit", "fact_commit_changes"),
            plan_id=plan.data["plan_id"],
        )
        assert not committed.is_error
        assert service.ledger.get("due-project").status == "superseded"
        assert service.ledger.get("due-other").certainty == "uncertain"
    finally:
        await connection.close()


async def test_retention_due_snapshot_and_selected_evidence_are_cas_pinned(tmp_path: Path, monkeypatch) -> None:
    service, connection = await _service(tmp_path)
    principal = FactPrincipal(
        "seed",
        frozenset({("area", "project")}),
        frozenset({("area", "project")}),
    )

    def retention(tool_id: str, tool_name: str) -> ToolExecution:
        return _execution(
            service,
            tool_id=tool_id,
            tool_name=tool_name,
            area=None,
            session_id="retention-race",
            run_automation_id=BUILTIN_MEMORY_RETENTION_ID,
        )

    async def mutate(fact_id: str, request_key: str) -> None:
        preview = await service.plan(
            principal,
            [
                {
                    "op": "amend",
                    "fact_id": fact_id,
                    "labels": [request_key],
                    "sources": [{"kind": "test", "ref": request_key}],
                }
            ],
            request_key=request_key,
            actor="seed",
            origin="test",
            reason="simulate concurrent change",
        )
        await service.commit(principal, preview.plan_id)

    try:
        await _commit_direct(
            service,
            "due-race",
            "Due race",
            scope=("area", "project"),
            lifecycle="temporary",
            review_at="2026-07-01T00:00:00Z",
            review_basis="explicit",
            occurred_at="2026-06-01T00:00:00Z",
        )
        await _commit_direct(
            service,
            "race-evidence",
            "Race evidence",
            scope=("area", "project"),
            occurred_at="2026-07-01T00:00:00Z",
        )

        original_batch = service.retention_review_batch

        async def raced_batch(caller: FactPrincipal):
            batch = await original_batch(caller)
            preview = await service.plan(
                principal,
                [
                    {
                        "op": "review",
                        "fact_id": "due-race",
                        "certainty": "confirmed",
                        "review_at": "2026-10-28T12:00:00Z",
                        "review_basis": "explicit",
                        "reason": "concurrent review",
                        "sources": [{"kind": "test", "ref": "target-raced"}],
                    }
                ],
                request_key="target-raced",
                actor="seed",
                origin="test",
                reason="simulate concurrent review",
            )
            await service.commit(principal, preview.plan_id)
            return batch

        monkeypatch.setattr(service, "retention_review_batch", raced_batch)
        stale_due = await plan_fact_changes_tool.execute(
            retention("stale-due", "fact_plan_changes"),
            changes=[
                {
                    "op": "review",
                    "fact_id": "due-race",
                    "certainty": "confirmed",
                    "evidence_fact_ids": ["race-evidence"],
                    "reason": "Evidence from stale due snapshot.",
                }
            ],
            reason="Stale due snapshot.",
        )
        assert stale_due.outcome.error.code == "write_conflict"
        monkeypatch.setattr(service, "retention_review_batch", original_batch)

        await _commit_direct(
            service,
            "due-evidence-race",
            "Due evidence race",
            scope=("area", "project"),
            lifecycle="temporary",
            review_at="2026-07-01T00:00:00Z",
            review_basis="explicit",
            occurred_at="2026-06-01T00:00:00Z",
        )
        await _commit_direct(
            service,
            "selected-evidence",
            "Selected evidence",
            scope=("area", "project"),
            occurred_at="2026-07-02T00:00:00Z",
        )
        plan = await plan_fact_changes_tool.execute(
            retention("evidence-plan", "fact_plan_changes"),
            changes=[
                {
                    "op": "supersede",
                    "fact_id": "due-evidence-race",
                    "successor_id": "selected-evidence",
                    "evidence_fact_ids": ["selected-evidence"],
                    "reason": "Selected newer evidence.",
                }
            ],
            reason="Prepare evidence-pinned decision.",
        )
        assert not plan.is_error

        await mutate("selected-evidence", "evidence-raced")
        stale_evidence = await commit_fact_changes_tool.execute(
            retention("evidence-commit", "fact_commit_changes"),
            plan_id=plan.data["plan_id"],
        )
        assert stale_evidence.outcome.error.code == "write_conflict"
        assert service.ledger.get("due-evidence-race").status == "active"
    finally:
        await connection.close()


async def test_retention_review_window_conflicts_only_for_new_relevant_evidence(tmp_path: Path) -> None:
    service, connection = await _service(tmp_path)

    def retention(tool_id: str) -> ToolExecution:
        return _execution(
            service,
            tool_id=tool_id,
            tool_name="fact_plan_changes",
            area=None,
            session_id="retention-window",
            run_automation_id=BUILTIN_MEMORY_RETENTION_ID,
        )

    async def review_plan(fact_id: str, tool_id: str):
        return await plan_fact_changes_tool.execute(
            retention(tool_id),
            changes=[
                {
                    "op": "review",
                    "fact_id": fact_id,
                    "certainty": "uncertain",
                    "review_at": "2026-10-28T12:00:00Z",
                    "review_basis": "explicit",
                    "reason": "No newer evidence at review time.",
                }
            ],
            reason="Review current evidence window.",
        )

    try:
        for fact_id in ("due-window", "due-unrelated"):
            await _commit_direct(
                service,
                fact_id,
                fact_id,
                scope=("area", "project"),
                lifecycle="temporary",
                review_at="2026-07-01T00:00:00Z",
                review_basis="explicit",
                occurred_at="2026-06-01T00:00:00Z",
            )

        stale = await review_plan("due-window", "window-stale-plan")
        assert not stale.is_error
        assert "review_window" not in stale.data["events"][0]["payload"]
        await _commit_direct(
            service,
            "new-related",
            "New related evidence",
            scope=("area", "project"),
            occurred_at="2026-07-02T00:00:00Z",
        )
        rejected = await commit_fact_changes_tool.execute(
            retention("window-stale-commit"),
            plan_id=stale.data["plan_id"],
        )
        assert rejected.outcome.error.code == "write_conflict"
        assert service.ledger.get("due-window").status == "active"

        clean = await review_plan("due-unrelated", "window-clean-plan")
        assert not clean.is_error
        await _commit_direct(
            service,
            "unrelated",
            "Unrelated evidence",
            scope=("area", "project"),
            subjects=["beta"],
            occurred_at="2026-07-03T00:00:00Z",
        )
        committed = await commit_fact_changes_tool.execute(
            retention("window-clean-commit"),
            plan_id=clean.data["plan_id"],
        )
        assert not committed.is_error
        event = service.ledger.history("due-unrelated")[-1]
        assert "review_window" in event.record["payload"]
        assert "expected_review_window" not in event.record["payload"]
    finally:
        await connection.close()


async def test_retention_scope_race_reaches_cas_and_releases_plan(tmp_path: Path) -> None:
    service, connection = await _service(tmp_path)
    old_scope = ("area", "project")
    new_scope = ("project", "moved")

    def retention(tool_id: str) -> ToolExecution:
        return _execution(
            service,
            tool_id=tool_id,
            tool_name="fact_plan_changes",
            area=None,
            session_id="retention-scope-race",
            run_automation_id=BUILTIN_MEMORY_RETENTION_ID,
        )

    try:
        await _commit_direct(
            service,
            "due-scope-race",
            "Due scope race",
            scope=old_scope,
            lifecycle="temporary",
            review_at="2026-07-01T00:00:00Z",
            review_basis="explicit",
        )
        review_change = {
            "op": "review",
            "fact_id": "due-scope-race",
            "certainty": "uncertain",
            "review_at": "2026-10-28T12:00:00Z",
            "review_basis": "explicit",
            "reason": "No newer evidence.",
        }
        plan = await plan_fact_changes_tool.execute(
            retention("scope-race-plan"),
            changes=[review_change],
            reason="Review current scope.",
        )
        assert not plan.is_error

        await _commit_direct(
            service,
            "new-scope-fact",
            "Unrelated new scope",
            scope=new_scope,
            subjects=["beta"],
        )
        retry = await plan_fact_changes_tool.execute(
            retention("scope-race-plan"),
            changes=[review_change],
            reason="Review current scope.",
        )
        assert not retry.is_error
        assert retry.data["plan_id"] == plan.data["plan_id"]

        mover = FactPrincipal("mover", frozenset({old_scope, new_scope}), frozenset({old_scope, new_scope}))
        moved = await service.plan(
            mover,
            [
                {
                    "op": "amend",
                    "fact_id": "due-scope-race",
                    "scope": {"kind": new_scope[0], "key": new_scope[1]},
                    "sources": [{"kind": "test", "ref": "scope-move"}],
                }
            ],
            request_key="scope-move",
            actor="mover",
            origin="test",
            reason="simulate concurrent scope correction",
        )
        await service.commit(mover, moved.plan_id)
        assert await service.known_scopes() == frozenset({old_scope, new_scope})

        rejected = await commit_fact_changes_tool.execute(
            retention("scope-race-commit"),
            plan_id=plan.data["plan_id"],
        )
        assert rejected.outcome.error.code == "write_conflict"
        stored = await service.plans.get(
            plan.data["plan_id"],
            owner_id=f"automation:{BUILTIN_MEMORY_RETENTION_ID}",
        )
        assert stored.status.value == "planned"
    finally:
        await connection.close()


async def test_only_six_fact_tools_are_registered_and_model_cannot_supply_authority() -> None:
    assert set(FACTS.tools) == {
        "fact_search",
        "fact_get",
        "fact_history",
        "fact_due_reviews",
        "fact_plan_changes",
        "fact_commit_changes",
    }
    schema = plan_fact_changes_tool.to_dict("fact_plan_changes")["function"]["parameters"]
    encoded = str(schema)
    assert "actor" not in encoded
    assert "origin" not in encoded
    assert '"scope"' not in encoded


async def test_search_and_due_cursors_page_beyond_one_hundred_and_accept_long_ids(tmp_path: Path) -> None:
    service, connection = await _service(tmp_path)
    principal = FactPrincipal("seed", frozenset({("area", "project")}), frozenset({("area", "project")}))
    try:
        changes = [
            {
                **_create(fact_id=f"bulk-{index:03d}", text=f"Bulk due fact {index:03d}"),
                "scope": {"kind": "area", "key": "project"},
                "sources": [{"kind": "test", "ref": f"bulk-{index:03d}"}],
                "lifecycle": "temporary",
                "expires_at": "2026-07-01T00:00:00Z",
            }
            for index in range(101)
        ]
        preview = await service.plan(
            principal,
            changes,
            request_key="bulk",
            actor="seed",
            origin="test",
            reason="cursor proof",
        )
        await service.commit(principal, preview.plan_id)

        first = await search_facts_tool.execute(
            _execution(service, tool_id="bulk-search-1", tool_name="fact_search"),
            query="Bulk",
            limit=100,
        )
        second = await search_facts_tool.execute(
            _execution(service, tool_id="bulk-search-2", tool_name="fact_search"),
            query="Bulk",
            limit=100,
            cursor=first.data["next_cursor"],
        )
        assert first.data["total"] == 101
        assert first.data["has_more"] is True
        assert len(first.data["facts"]) == 100
        assert len(second.data["facts"]) == 1
        assert second.data["has_more"] is False

        due_first = await get_due_fact_reviews_tool.execute(
            _execution(service, tool_id="bulk-due-1", tool_name="fact_due_reviews"),
            limit=100,
        )
        due_second = await get_due_fact_reviews_tool.execute(
            _execution(service, tool_id="bulk-due-2", tool_name="fact_due_reviews"),
            limit=100,
            cursor=due_first.data["next_cursor"],
        )
        assert due_first.data["total"] == 101
        assert len(due_first.data["reviews"]) == 100
        assert len(due_second.data["reviews"]) == 1

        long_id = "m" * 500
        long_preview = await service.plan(
            principal,
            [
                {
                    **_create(fact_id=long_id, text="Long cursor first"),
                    "scope": {"kind": "area", "key": "project"},
                    "sources": [{"kind": "test", "ref": "long"}],
                },
                {
                    **_create(fact_id="z", text="Long cursor second"),
                    "scope": {"kind": "area", "key": "project"},
                    "sources": [{"kind": "test", "ref": "z"}],
                },
            ],
            request_key="long",
            actor="seed",
            origin="test",
            reason="long cursor proof",
        )
        await service.commit(principal, long_preview.plan_id)
        long_first = await search_facts_tool.execute(
            _execution(service, tool_id="long-search-1", tool_name="fact_search"),
            query="Long cursor",
            limit=1,
        )
        assert len(long_first.data["next_cursor"]) > 500
        long_second = await search_facts_tool.execute(
            _execution(service, tool_id="long-search-2", tool_name="fact_search"),
            query="Long cursor",
            limit=1,
            cursor=long_first.data["next_cursor"],
        )
        assert [fact["fact_id"] for fact in long_second.data["facts"]] == ["z"]
    finally:
        await connection.close()
