from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio

import arden.database as database
from arden.automation.store import AutomationStore
from arden.automation.suggestions import AutomationSuggestion
from arden.automation.triggers import EventTrigger, TimeTrigger


@pytest_asyncio.fixture
async def automation_store(tmp_path: Path):
    conn = await database.connect(tmp_path / "automation.db")
    store = AutomationStore(conn)
    await store.init_schema()
    yield store
    await conn.close()


def _suggestion(
    suggestion_id: str,
    *,
    name: str | None = None,
    description: str | None = None,
    prompt: str | None = None,
    triggers: list | None = None,
    rationale: str = "because it fits",
    category: str = "Status reports",
    evidence: list[str] | None = None,
    icon: str | None = None,
    created_at: datetime | None = None,
) -> AutomationSuggestion:
    return AutomationSuggestion(
        id=suggestion_id,
        name=name or suggestion_id,
        description=description or f"{suggestion_id} description",
        prompt=prompt or f"Run {suggestion_id}",
        triggers=triggers or [TimeTrigger(at="09:00", days="mon")],
        rationale=rationale,
        category=category,
        evidence=evidence if evidence is not None else [f"evidence for {suggestion_id}"],
        icon=icon,
        created_at=created_at or datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_migration_creates_suggestions_table(automation_store: AutomationStore):
    rows = await automation_store.conn.execute_fetchall("PRAGMA table_info(automation_suggestions)")
    columns = {row["name"] for row in rows}
    assert columns == {
        "id",
        "name",
        "description",
        "prompt",
        "triggers",
        "rationale",
        "evidence",
        "category",
        "icon",
        "status",
        "created_at",
        "source_automation_id",
    }

    version_rows = await automation_store.conn.execute_fetchall(
        "SELECT value FROM automation_meta WHERE key = 'schema_version'"
    )
    assert int(version_rows[0]["value"]) >= 11


@pytest.mark.asyncio
async def test_replace_active_suggestions_round_trips(automation_store: AutomationStore):
    await automation_store.replace_active_suggestions(
        [
            _suggestion(
                "s1",
                triggers=[TimeTrigger(at="09:00", days="mon")],
                evidence=["a", "b"],
                icon="GitPullRequest",
            ),
            _suggestion(
                "s2",
                triggers=[EventTrigger(event_type="calendar_event_starting", lead_minutes=15)],
                evidence=[],
                icon=None,
            ),
        ]
    )

    active = await automation_store.list_active_suggestions()
    by_id = {s.id: s for s in active}
    assert set(by_id) == {"s1", "s2"}

    s1 = by_id["s1"]
    assert s1.status == "active"
    assert s1.evidence == ["a", "b"]
    assert s1.icon == "GitPullRequest"
    assert len(s1.triggers) == 1
    assert isinstance(s1.triggers[0], TimeTrigger)
    assert str(s1.triggers[0].at) == "09:00"
    assert str(s1.triggers[0].days) == "mon"

    s2 = by_id["s2"]
    assert s2.evidence == []
    assert s2.icon is None
    assert isinstance(s2.triggers[0], EventTrigger)
    assert s2.triggers[0].event_type == "calendar_event_starting"
    assert s2.triggers[0].lead_minutes == 15


@pytest.mark.asyncio
async def test_list_active_ordered_by_created_at_desc(automation_store: AutomationStore):
    base = datetime.now(UTC)
    await automation_store.replace_active_suggestions(
        [
            _suggestion("old", created_at=base - timedelta(hours=2)),
            _suggestion("new", created_at=base),
            _suggestion("mid", created_at=base - timedelta(hours=1)),
        ]
    )

    active = await automation_store.list_active_suggestions()
    assert [s.id for s in active] == ["new", "mid", "old"]


@pytest.mark.asyncio
async def test_replace_active_only_touches_active_rows(automation_store: AutomationStore):
    await automation_store.replace_active_suggestions(
        [_suggestion("keep-dismissed"), _suggestion("keep-accepted"), _suggestion("to-replace")]
    )
    await automation_store.mark_suggestion_dismissed("keep-dismissed")
    await automation_store.mark_suggestion_accepted("keep-accepted", "auto-123")

    await automation_store.replace_active_suggestions([_suggestion("fresh")])

    active_ids = {s.id for s in await automation_store.list_active_suggestions()}
    assert active_ids == {"fresh"}

    all_rows = await automation_store.conn.execute_fetchall(
        "SELECT id, status, source_automation_id FROM automation_suggestions ORDER BY id"
    )
    by_id = {row["id"]: row for row in all_rows}
    assert by_id["keep-dismissed"]["status"] == "dismissed"
    assert by_id["keep-accepted"]["status"] == "accepted"
    assert by_id["keep-accepted"]["source_automation_id"] == "auto-123"
    assert "to-replace" not in by_id


@pytest.mark.asyncio
async def test_dismiss_removes_from_active(automation_store: AutomationStore):
    await automation_store.replace_active_suggestions([_suggestion("s1"), _suggestion("s2")])

    await automation_store.mark_suggestion_dismissed("s1")

    active_ids = {s.id for s in await automation_store.list_active_suggestions()}
    assert active_ids == {"s2"}


@pytest.mark.asyncio
async def test_accept_records_source_automation(automation_store: AutomationStore):
    await automation_store.replace_active_suggestions([_suggestion("s1")])

    await automation_store.mark_suggestion_accepted("s1", "auto-999")

    assert await automation_store.list_active_suggestions() == []
    row = (
        await automation_store.conn.execute_fetchall(
            "SELECT status, source_automation_id FROM automation_suggestions WHERE id = 's1'"
        )
    )[0]
    assert row["status"] == "accepted"
    assert row["source_automation_id"] == "auto-999"


@pytest.mark.asyncio
async def test_list_excluded_signatures_returns_dismissed_and_accepted(automation_store: AutomationStore):
    await automation_store.replace_active_suggestions(
        [
            _suggestion("active-1", name="Active one", description="still active"),
            _suggestion("dismissed-1", name="Dismissed one", description="user said no"),
            _suggestion("accepted-1", name="Accepted one", description="user said yes"),
        ]
    )
    await automation_store.mark_suggestion_dismissed("dismissed-1")
    await automation_store.mark_suggestion_accepted("accepted-1", "auto-1")

    signatures = await automation_store.list_excluded_signatures()

    assert set(signatures) == {
        "Dismissed one — Run dismissed-1",
        "Accepted one — Run accepted-1",
    }
