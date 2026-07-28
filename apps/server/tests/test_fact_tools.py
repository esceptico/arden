"""Structured fact-tool contracts above FactService."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from arden.context.models import AreaContext, SessionState
from arden.database import connect
from arden.integrations.core import FACTS
from arden.memory.facts import FactLedger, FactPlanStore, FactPrincipal, FactService
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

if TYPE_CHECKING:
    from pathlib import Path


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
) -> ToolExecution:
    ctx = ToolContext(
        session_state=SessionState(session_id="session-1", started_at=JULY),
        registry=ToolRegistry(),
        run=RunContext(run_id=run_id),
        io=IOBridge(),
        services={FACT_SERVICE: service},
        area=area,
        background_tasks=BackgroundTaskRegistry(session_id="session-1"),
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


async def _commit_direct(service: FactService, fact_id: str, text: str, *, scope: tuple[str, str | None]) -> None:
    principal = FactPrincipal("seed", frozenset({scope}), frozenset({scope}))
    preview = await service.plan(
        principal,
        [
            {
                **_create(fact_id=fact_id, text=text),
                "scope": {"kind": scope[0], "key": scope[1]},
                "sources": [{"kind": "test", "ref": fact_id}],
            }
        ],
        request_key=f"seed:{fact_id}",
        actor="seed",
        origin="test",
        reason="seed",
    )
    await service.commit(principal, preview.plan_id)


async def test_plan_and_commit_use_server_derived_identity_scope_and_provenance(tmp_path: Path) -> None:
    service, connection = await _service(tmp_path)
    try:
        plan = await plan_fact_changes_tool.execute(
            _execution(service, tool_id="plan-1", tool_name="plan_fact_changes"),
            changes=[_create(fact_id="one", text="One fact")],
            reason="User stated this.",
        )
        assert not plan.is_error
        retry = await plan_fact_changes_tool.execute(
            _execution(
                service,
                tool_id="plan-1",
                tool_name="plan_fact_changes",
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

        committed = await commit_fact_changes_tool.execute(
            _execution(service, tool_id="commit-1", tool_name="commit_fact_changes"), plan_id=plan.data["plan_id"]
        )
        assert not committed.is_error
        json.dumps(committed.data)
        assert committed.outcome is not None and committed.outcome.effect is not None
        assert committed.outcome.effect.operation == "commit"

        fact = await get_fact_tool.execute(_execution(service, tool_id="get-1", tool_name="get_fact"), fact_id="one")
        json.dumps(fact.data)
        assert fact.data["fact"]["scope"] == {"kind": "area", "key": "project"}
        assert fact.data["fact"]["sources"]["total"] == 1
    finally:
        await connection.close()


async def test_read_tools_page_stably_and_enforce_server_derived_area_scope(tmp_path: Path) -> None:
    service, connection = await _service(tmp_path)
    try:
        await _commit_direct(service, "other", "Hidden", scope=("area", "other"))
        for fact_id in ("a", "b", "c"):
            await _commit_direct(service, fact_id, fact_id.upper(), scope=("area", "project"))

        first = await search_facts_tool.execute(
            _execution(service, tool_id="search-1", tool_name="search_facts"), limit=2
        )
        second = await search_facts_tool.execute(
            _execution(service, tool_id="search-2", tool_name="search_facts"),
            limit=2,
            cursor=first.data["next_cursor"],
        )
        assert [fact["fact_id"] for fact in first.data["facts"]] == ["a", "b"]
        assert [fact["fact_id"] for fact in second.data["facts"]] == ["c"]
        assert first.data["total"] == 3
        assert "items" not in first.data
        json.dumps(first.data)
        wrong_filter = await search_facts_tool.execute(
            _execution(service, tool_id="search-wrong", tool_name="search_facts"),
            query="A",
            limit=2,
            cursor=first.data["next_cursor"],
        )
        assert wrong_filter.outcome.error.code == "invalid_ref"

        await _commit_direct(service, "d", "D", scope=("area", "project"))
        stale = await search_facts_tool.execute(
            _execution(service, tool_id="search-stale", tool_name="search_facts"),
            limit=2,
            cursor=first.data["next_cursor"],
        )
        assert stale.outcome.error.code == "write_conflict"

        history = await get_fact_history_tool.execute(
            _execution(service, tool_id="history-1", tool_name="get_fact_history"), fact_id="a", limit=1
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
            _execution(service, tool_id="plan-due", tool_name="plan_fact_changes"),
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
            _execution(service, tool_id="commit-due", tool_name="commit_fact_changes"), plan_id=plan.data["plan_id"]
        )
        due = await get_due_fact_reviews_tool.execute(
            _execution(service, tool_id="due-1", tool_name="get_due_fact_reviews"), limit=1
        )
        assert due.data["reviews"][0]["fact"]["fact_id"] == "due"
        assert "recommend" not in due.content.lower()
        json.dumps(due.data)
    finally:
        await connection.close()


async def test_only_six_fact_tools_are_registered_and_model_cannot_supply_authority() -> None:
    assert set(FACTS.tools) == {
        "search_facts",
        "get_fact",
        "get_fact_history",
        "get_due_fact_reviews",
        "plan_fact_changes",
        "commit_fact_changes",
    }
    schema = plan_fact_changes_tool.to_dict("plan_fact_changes")["function"]["parameters"]
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
            _execution(service, tool_id="bulk-search-1", tool_name="search_facts"),
            query="Bulk",
            limit=100,
        )
        second = await search_facts_tool.execute(
            _execution(service, tool_id="bulk-search-2", tool_name="search_facts"),
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
            _execution(service, tool_id="bulk-due-1", tool_name="get_due_fact_reviews"),
            limit=100,
        )
        due_second = await get_due_fact_reviews_tool.execute(
            _execution(service, tool_id="bulk-due-2", tool_name="get_due_fact_reviews"),
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
            _execution(service, tool_id="long-search-1", tool_name="search_facts"),
            query="Long cursor",
            limit=1,
        )
        assert len(long_first.data["next_cursor"]) > 500
        long_second = await search_facts_tool.execute(
            _execution(service, tool_id="long-search-2", tool_name="search_facts"),
            query="Long cursor",
            limit=1,
            cursor=long_first.data["next_cursor"],
        )
        assert [fact["fact_id"] for fact in long_second.data["facts"]] == ["z"]
    finally:
        await connection.close()
