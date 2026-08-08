import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio
from pydantic import ValidationError

import arden.database as database
from arden.areas.agent import AreaCustodianReport
from arden.areas.work_store import AreaWorkConflict, AreaWorkReportError, AreaWorkStore
from arden.context.store import SessionStore
from arden.services.session import SessionService


@pytest_asyncio.fixture
async def work_env(tmp_path: Path):
    conn = await database.connect(tmp_path / "sessions.db")
    sessions_store = SessionStore(conn, event_conn=conn)
    await sessions_store.init_schema()
    sessions = SessionService(sessions_store)
    work = AreaWorkStore(conn)
    await work.init_schema()
    first = await sessions.create_area(name="Health")
    second = await sessions.create_area(name="Visa")
    yield conn, sessions, work, first, second
    await conn.close()


@pytest.mark.asyncio
async def test_outcomes_are_stable_and_isolated_by_area(work_env) -> None:
    _conn, _sessions, work, health, visa = work_env

    created = await work.create_outcome(
        health["area_id"],
        key="labs-normal",
        title="Lab values normalized",
        success_criteria="All flagged values are in range",
        priority=5,
        source="user",
    )

    assert created.outcome_id == f"outcome:{health['area_id']}:labs-normal"
    assert [item.stable_key for item in (await work.snapshot(health["area_id"])).outcomes] == ["labs-normal"]
    assert (await work.snapshot(visa["area_id"])).outcomes == []
    assert await work.update_outcome(visa["area_id"], "labs-normal", title="wrong") is None


@pytest.mark.asyncio
async def test_user_updates_require_current_version(work_env) -> None:
    _conn, _sessions, work, health, _visa = work_env
    created = await work.create_outcome(
        health["area_id"],
        key="labs-normal",
        title="Lab values normalized",
        success_criteria="All flagged values are in range",
        priority=5,
        source="user",
    )

    updated = await work.update_outcome(
        health["area_id"],
        "labs-normal",
        expected_updated_at=created.updated_at,
        title="Labs stable",
    )
    assert updated is not None and updated.title == "Labs stable"

    with pytest.raises(AreaWorkConflict):
        await work.update_outcome(
            health["area_id"],
            "labs-normal",
            expected_updated_at=created.updated_at,
            title="stale",
        )


@pytest.mark.asyncio
async def test_work_items_reference_outcomes_and_use_stable_identity(work_env) -> None:
    _conn, _sessions, work, health, _visa = work_env
    outcome = await work.create_outcome(
        health["area_id"],
        key="labs-normal",
        title="Lab values normalized",
        success_criteria="All flagged values are in range",
        priority=5,
        source="user",
    )

    item = await work.create_work_item(
        health["area_id"],
        key="book-labs",
        outcome_key=outcome.stable_key,
        kind="action",
        text="Book the follow-up panel",
        owner="custodian",
    )

    assert item.item_id == f"work:{health['area_id']}:book-labs"
    assert item.outcome_id == outcome.outcome_id
    assert [row.stable_key for row in (await work.snapshot(health["area_id"])).work_items] == ["book-labs"]

    completed = await work.update_work_item(
        health["area_id"],
        "book-labs",
        expected_updated_at=item.updated_at,
        status="completed",
    )
    assert completed is not None and completed.status == "completed" and completed.completed_at is not None


@pytest.mark.asyncio
async def test_brief_caps_recent_completions_and_selects_one_active_item_per_area(work_env) -> None:
    _conn, _sessions, work, health, visa = work_env
    for area, prefix in ((health, "health"), (visa, "visa")):
        for index in range(2):
            item = await work.create_work_item(
                area["area_id"],
                key=f"{prefix}-{index}",
                outcome_key=None,
                kind="action",
                text=f"{prefix} action {index}",
                owner="custodian",
            )
            if index == 0:
                await work.update_work_item(
                    area["area_id"],
                    item.stable_key,
                    expected_updated_at=item.updated_at,
                    status="in_progress",
                )
    target = next(row for row in (await work.snapshot(health["area_id"])).work_items if row.stable_key == "health-1")
    completion = report(
        outcome_changes=[],
        work_changes=[
            {
                "op": "complete",
                "key": target.stable_key,
                "expected_updated_at": target.updated_at,
            }
        ],
        evidence=[
            {
                "target_type": "work",
                "target_key": target.stable_key,
                "event_type": "completed",
                "summary": "Finished the health action",
                "source_refs": [
                    {
                        "provider": "arden",
                        "kind": "session",
                        "ref": "health-chat~abc123",
                        "title": "Health chat",
                    }
                ],
            }
        ],
    )
    assert await work.apply_report(health["area_id"], "run:brief", completion)

    brief = await work.brief(
        {health["area_id"], visa["area_id"]},
        now=datetime.now(UTC),
    )

    assert [row["stable_key"] for row in brief["done"]] == ["health-1"]
    assert brief["done"][0]["text"] == "Finished the health action"
    assert {row["area_id"] for row in brief["in_progress"]} == {
        health["area_id"],
        visa["area_id"],
    }
    assert all(row["status"] == "in_progress" for row in brief["in_progress"])


@pytest.mark.asyncio
async def test_archive_preserves_work_and_permanent_delete_cascades(work_env) -> None:
    conn, sessions, work, health, _visa = work_env
    await work.create_outcome(
        health["area_id"],
        key="labs-normal",
        title="Lab values normalized",
        success_criteria="All flagged values are in range",
        priority=5,
        source="user",
    )

    assert await sessions.archive_area(health["area_id"])
    assert len((await work.snapshot(health["area_id"])).outcomes) == 1

    await conn.execute("DELETE FROM areas WHERE area_id = ?", (health["area_id"],))
    await conn.commit()
    assert (await work.snapshot(health["area_id"])).outcomes == []


def report(**overrides) -> AreaCustodianReport:
    data = {
        "asks": [],
        "report": "advanced work",
        "next_check_hours": 24,
        "next_check_reason": "continue tomorrow",
        "work_remaining": True,
        "outcome_changes": [
            {
                "op": "create",
                "key": "labs-normal",
                "title": "Lab values normalized",
                "success_criteria": "All flagged values are in range",
                "priority": 5,
            }
        ],
        "work_changes": [
            {
                "op": "create",
                "key": "book-labs",
                "outcome_key": "labs-normal",
                "kind": "action",
                "text": "Book the follow-up panel",
                "owner": "custodian",
            }
        ],
        "evidence": [
            {
                "target_type": "work",
                "target_key": "book-labs",
                "event_type": "progress",
                "summary": "Found the correct lab and opening hours",
                "source_refs": [
                    {
                        "provider": "web",
                        "kind": "page",
                        "ref": "https://example.test/lab",
                        "title": "Lab opening hours",
                    }
                ],
            }
        ],
    }
    data.update(overrides)
    return AreaCustodianReport.model_validate(data)


@pytest.mark.asyncio
async def test_report_applies_atomically_once_with_evidence(work_env) -> None:
    _conn, _sessions, work, health, _visa = work_env
    submitted = report()

    assert await work.apply_report(health["area_id"], "run:r1", submitted)
    assert not await work.apply_report(health["area_id"], "run:r1", submitted)
    assert await work.report("run:r1") == submitted.model_dump(mode="json")

    snapshot = await work.snapshot(health["area_id"])
    assert [row.stable_key for row in snapshot.outcomes] == ["labs-normal"]
    assert [row.stable_key for row in snapshot.work_items] == ["book-labs"]
    assert [row.summary for row in snapshot.events] == ["Found the correct lab and opening hours"]
    assert snapshot.events[0].model_dump(mode="json")["source_refs"] == [
        {
            "provider": "web",
            "kind": "page",
            "ref": "https://example.test/lab",
            "title": "Lab opening hours",
        }
    ]


@pytest.mark.parametrize("raw_ref", ["session:s1", "area_health", "task:42", "internal:42"])
def test_evidence_rejects_raw_internal_identifiers(raw_ref: str) -> None:
    with pytest.raises(ValidationError):
        report(
            evidence=[
                {
                    "target_type": "area",
                    "event_type": "progress",
                    "summary": "Found supporting evidence",
                    "source_refs": [raw_ref],
                }
            ]
        )


@pytest.mark.parametrize("missing_field", ["provider", "kind", "ref", "title"])
def test_evidence_rejects_partial_provider_references(missing_field: str) -> None:
    source_ref = {
        "provider": "web",
        "kind": "page",
        "ref": "https://example.test/evidence",
        "title": "Evidence",
    }
    source_ref.pop(missing_field)

    with pytest.raises(ValidationError):
        report(
            evidence=[
                {
                    "target_type": "area",
                    "event_type": "progress",
                    "summary": "Found supporting evidence",
                    "source_refs": [source_ref],
                }
            ]
        )


@pytest.mark.parametrize("raw_ref", ["session:s1", "area_health", "task:42", "internal:42"])
def test_arden_evidence_requires_public_references(raw_ref: str) -> None:
    with pytest.raises(ValidationError):
        report(
            evidence=[
                {
                    "target_type": "area",
                    "event_type": "progress",
                    "summary": "Found supporting evidence",
                    "source_refs": [
                        {
                            "provider": "arden",
                            "kind": "session",
                            "ref": raw_ref,
                            "title": "Health chat",
                        }
                    ],
                }
            ]
        )


def test_evidence_rejects_whitespace_instead_of_normalizing_it() -> None:
    with pytest.raises(ValidationError):
        report(
            evidence=[
                {
                    "target_type": "area",
                    "event_type": "progress",
                    "summary": "Found supporting evidence",
                    "source_refs": [
                        {
                            "provider": "web",
                            "kind": "page",
                            "ref": " https://example.test/evidence",
                            "title": "Evidence",
                        }
                    ],
                }
            ]
        )


@pytest.mark.asyncio
async def test_concurrent_duplicate_report_is_idempotent(work_env, tmp_path: Path) -> None:
    conn, _sessions, work, health, _visa = work_env
    first_read_conn = await database.connect(tmp_path / "sessions.db", readonly=True)
    second_conn = await database.connect(tmp_path / "sessions.db")
    second_read_conn = await database.connect(tmp_path / "sessions.db", readonly=True)
    ready = 0
    both_read = asyncio.Event()
    ready_lock = asyncio.Lock()

    class BarrierReadConnection:
        def __init__(self, read_conn) -> None:
            self.read_conn = read_conn
            self.waited = False

        async def execute_fetchall(self, sql, parameters=()):
            nonlocal ready
            rows = await self.read_conn.execute_fetchall(sql, parameters)
            if "FROM area_work_reports WHERE run_ref" not in sql or rows or self.waited:
                return rows
            self.waited = True
            async with ready_lock:
                ready += 1
                if ready == 2:
                    both_read.set()
            await both_read.wait()
            return rows

    first = AreaWorkStore(conn, BarrierReadConnection(first_read_conn))
    second = AreaWorkStore(second_conn, BarrierReadConnection(second_read_conn))
    submitted = report()
    try:
        results = await asyncio.gather(
            first.apply_report(health["area_id"], "run:concurrent", submitted),
            second.apply_report(health["area_id"], "run:concurrent", submitted),
        )
    finally:
        await first_read_conn.close()
        await second_read_conn.close()
        await second_conn.close()

    assert sorted(results) == [False, True]
    snapshot = await work.snapshot(health["area_id"])
    assert [row.stable_key for row in snapshot.outcomes] == ["labs-normal"]
    assert [row.stable_key for row in snapshot.work_items] == ["book-labs"]
    assert [row.summary for row in snapshot.events] == ["Found the correct lab and opening hours"]


@pytest.mark.asyncio
async def test_report_rejects_a_different_payload_for_the_same_run(work_env) -> None:
    _conn, _sessions, work, health, _visa = work_env
    submitted = report()
    changed = submitted.model_copy(update={"report": "different"})
    assert await work.apply_report(health["area_id"], "run:r1", submitted)

    with pytest.raises(AreaWorkReportError, match="already submitted a different report"):
        await work.apply_report(health["area_id"], "run:r1", changed)


@pytest.mark.asyncio
async def test_report_ignores_exact_noop_update_to_terminal_work_item(work_env) -> None:
    _conn, _sessions, work, health, _visa = work_env
    item = await work.create_work_item(
        health["area_id"],
        key="watching-section",
        outcome_key=None,
        kind="action",
        text="Added the WATCHING section",
        owner="custodian",
    )
    completed = await work.update_work_item(
        health["area_id"],
        item.stable_key,
        expected_updated_at=item.updated_at,
        status="completed",
    )
    unchanged = report(
        outcome_changes=[],
        work_changes=[
            {
                "op": "update",
                "key": completed.stable_key,
                "text": completed.text,
                "owner": completed.owner,
                "expected_updated_at": completed.updated_at,
            }
        ],
        evidence=[
            {
                "target_type": "work",
                "target_key": completed.stable_key,
                "event_type": "completed",
                "summary": "The WATCHING section remains complete",
                "source_refs": [
                    {
                        "provider": "memory",
                        "kind": "wiki_page",
                        "ref": "topics/health.md",
                        "title": "Health",
                    }
                ],
            }
        ],
    )

    assert await work.apply_report(health["area_id"], "run:noop", unchanged)
    snapshot = await work.snapshot(health["area_id"])
    assert snapshot.work_items[0].updated_at == completed.updated_at
    assert [event.summary for event in snapshot.events] == ["The WATCHING section remains complete"]


@pytest.mark.asyncio
async def test_report_rejects_real_update_to_terminal_work_item(work_env) -> None:
    _conn, _sessions, work, health, _visa = work_env
    item = await work.create_work_item(
        health["area_id"],
        key="watching-section",
        outcome_key=None,
        kind="action",
        text="Added the WATCHING section",
        owner="custodian",
    )
    completed = await work.update_work_item(
        health["area_id"],
        item.stable_key,
        expected_updated_at=item.updated_at,
        status="completed",
    )
    changed = report(
        outcome_changes=[],
        work_changes=[
            {
                "op": "update",
                "key": completed.stable_key,
                "text": "Rewrite the completed work",
                "expected_updated_at": completed.updated_at,
            }
        ],
        evidence=[],
    )

    with pytest.raises(AreaWorkReportError, match="Terminal work item"):
        await work.apply_report(health["area_id"], "run:changed-terminal", changed)


@pytest.mark.asyncio
async def test_invalid_report_rolls_back_every_operation(work_env) -> None:
    _conn, _sessions, work, health, _visa = work_env
    invalid = report(
        work_changes=[
            {
                "op": "complete",
                "key": "unknown",
                "expected_updated_at": "missing",
            }
        ]
    )

    with pytest.raises(AreaWorkReportError):
        await work.apply_report(health["area_id"], "run:bad", invalid)

    snapshot = await work.snapshot(health["area_id"])
    assert snapshot.outcomes == [] and snapshot.work_items == [] and snapshot.events == []


@pytest.mark.asyncio
async def test_agent_report_loses_to_a_newer_user_edit(work_env) -> None:
    _conn, _sessions, work, health, _visa = work_env
    outcome = await work.create_outcome(
        health["area_id"],
        key="labs-normal",
        title="Labs normalized",
        success_criteria="All values in range",
        priority=5,
        source="user",
    )
    stale_report = report(
        outcome_changes=[
            {
                "op": "update",
                "key": outcome.stable_key,
                "title": "Agent title",
                "expected_updated_at": outcome.updated_at,
            }
        ],
        work_changes=[],
        evidence=[],
    )
    await work.update_outcome(
        health["area_id"],
        outcome.stable_key,
        expected_updated_at=outcome.updated_at,
        title="User title",
    )

    with pytest.raises(AreaWorkReportError, match="changed since the run started"):
        await work.apply_report(health["area_id"], "run:stale", stale_report)

    assert (await work.snapshot(health["area_id"])).outcomes[0].title == "User title"


@pytest.mark.asyncio
async def test_schema_restart_archive_restore_and_report_replay_are_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "restart.db"
    conn = await database.connect(db_path)
    sessions = SessionService(SessionStore(conn, event_conn=conn))
    await sessions.store.init_schema()
    work = AreaWorkStore(conn)
    await work.init_schema()
    await work.init_schema()
    area = await sessions.create_area(name="Health")
    assert await work.apply_report(area["area_id"], "run:restart", report())
    assert await sessions.archive_area(area["area_id"])
    await conn.close()

    reopened = await database.connect(db_path)
    sessions = SessionService(SessionStore(reopened, event_conn=reopened))
    await sessions.store.init_schema()
    work = AreaWorkStore(reopened)
    await work.init_schema()
    restored = await sessions.restore_area(area["area_id"])

    assert restored is not None
    assert not await work.apply_report(area["area_id"], "run:restart", report())
    snapshot = await work.snapshot(area["area_id"])
    assert [row.stable_key for row in snapshot.outcomes] == ["labs-normal"]
    assert [row.summary for row in snapshot.events] == ["Found the correct lab and opening hours"]
    assert (await work.brief({area["area_id"]}))["in_progress"][0]["stable_key"] == "book-labs"
    await reopened.close()
