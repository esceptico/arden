"""Router tests for the contextual automation suggestions endpoints.

Offline only. The store-backed routes run against a real AutomationStore over a
tmp_path SQLite DB, exposed through a lightweight fake AutomationService (just
`.store` + a recording `create`) so the accept-on-create wiring is exercised
without provisioning channel sessions. The refresh route is exercised through a
fake AutomationRuntime whose `refresh_suggestions` seeds the store and returns
the active set, pinning the GET-shaped response contract.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

import arden.database as database
from arden.automation.models import Automation
from arden.automation.store import AutomationStore
from arden.automation.suggestions import AutomationSuggestion
from arden.automation.triggers import EventTrigger, TimeTrigger
from arden.server.app import app
from arden.server.deps import require_automation_runtime, require_automation_service
from arden.server.runtime.automation import SuggesterUnavailableError


def _suggestion(
    suggestion_id: str,
    *,
    name: str | None = None,
    triggers: list | None = None,
    evidence: list[str] | None = None,
    icon: str | None = None,
    created_at: datetime | None = None,
) -> AutomationSuggestion:
    return AutomationSuggestion(
        id=suggestion_id,
        name=name or suggestion_id,
        description=f"{suggestion_id} summary",
        prompt=f"{suggestion_id} prompt",
        triggers=triggers or [TimeTrigger(at="09:00", days="mon")],
        rationale=f"because {suggestion_id} fits",
        category="Status reports",
        evidence=evidence if evidence is not None else [f"evidence for {suggestion_id}"],
        icon=icon,
        created_at=created_at or datetime.now(UTC),
    )


class _FakeAutomationService:
    """Records create() and exposes the real store for the suggestion routes."""

    def __init__(self, store: AutomationStore):
        self.store = store
        self.created: list[dict] = []
        self.next_task_id = "fresh-task"

    async def create(self, **kwargs) -> Automation:
        self.created.append(kwargs)
        now = datetime.now(UTC)
        return Automation(
            task_id=self.next_task_id,
            name=kwargs["name"],
            description=kwargs["description"],
            description_source="manual" if kwargs["description"] else "generated",
            prompt=kwargs["prompt"],
            model=None,
            triggers=[TimeTrigger(at="09:00", days="mon")],
            enabled=True,
            created_at=now,
            next_run_at=None,
            last_run_at=None,
            last_result=None,
            running_since=None,
            auto_approve=kwargs.get("auto_approve", False),
        )


class _FakeAutomationRuntime:
    def __init__(self, store: AutomationStore):
        self.store = store
        self.refresh_calls = 0

    async def refresh_suggestions(self) -> list[AutomationSuggestion]:
        self.refresh_calls += 1
        await self.store.replace_active_suggestions([_suggestion("refreshed")])
        return await self.store.list_active_suggestions()


@pytest_asyncio.fixture
async def store(tmp_path: Path):
    conn = await database.connect(tmp_path / "automation.db")
    s = AutomationStore(conn)
    await s.init_schema()
    yield s
    await conn.close()


@pytest.fixture
def service(store):
    return _FakeAutomationService(store)


@pytest.fixture
def client(store, service):
    runtime = _FakeAutomationRuntime(store)
    app.dependency_overrides[require_automation_service] = lambda: service
    app.dependency_overrides[require_automation_runtime] = lambda: runtime
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(require_automation_service, None)
        app.dependency_overrides.pop(require_automation_runtime, None)


async def test_get_returns_active_suggestions(store, client):
    older = datetime.now(UTC) - timedelta(hours=1)
    await store.replace_active_suggestions(
        [
            _suggestion("a", name="Weekly digest", icon="GitPullRequest"),
            _suggestion(
                "b",
                name="Calendar prep",
                triggers=[EventTrigger(event_type="calendar.event_approaching", lead_minutes=30)],
                icon=None,
                created_at=older,
            ),
        ]
    )

    body = client.get("/automations/suggestions").json()
    suggestions = body["suggestions"]

    assert [s["name"] for s in suggestions] == ["Weekly digest", "Calendar prep"]
    first = suggestions[0]
    assert set(first) == {"id", "name", "description", "prompt", "triggers", "rationale", "evidence", "category", "icon"}
    assert first["id"] == "a"
    assert first["triggers"] == [{"type": "time", "at": "09:00", "days": "mon"}]
    assert first["icon"] == "GitPullRequest"
    assert first["evidence"] == ["evidence for a"]

    second = suggestions[1]
    assert second["icon"] is None
    assert second["triggers"] == [{"type": "event", "event_type": "calendar.event_approaching", "lead_minutes": 30}]


async def test_dismiss_removes_from_active(store, client):
    await store.replace_active_suggestions([_suggestion("a"), _suggestion("b")])

    resp = client.post("/automations/suggestions/a/dismiss")
    assert resp.status_code == 204

    remaining = client.get("/automations/suggestions").json()["suggestions"]
    assert [s["id"] for s in remaining] == ["b"]


async def test_create_with_from_suggestion_id_marks_accepted(store, service, client):
    await store.replace_active_suggestions([_suggestion("a"), _suggestion("b")])
    service.next_task_id = "minted-task"

    resp = client.post(
        "/automations",
        json={
            "name": "Weekly digest",
            "description": "Summarizes merged pull requests.",
            "prompt": "Summarize merged PRs.",
            "trigger_type": "time",
            "at": "09:00",
            "days": "mon",
            "from_suggestion_id": "a",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["task_id"] == "minted-task"

    remaining = client.get("/automations/suggestions").json()["suggestions"]
    assert [s["id"] for s in remaining] == ["b"]

    rows = await store.conn.execute_fetchall(
        "SELECT status, source_automation_id FROM automation_suggestions WHERE id = 'a'"
    )
    assert rows[0]["status"] == "accepted"
    assert rows[0]["source_automation_id"] == "minted-task"


async def test_create_without_from_suggestion_id_leaves_suggestions(store, client):
    await store.replace_active_suggestions([_suggestion("a")])

    resp = client.post(
        "/automations",
        json={
            "name": "Standalone",
            "description": "Runs a standalone task.",
            "prompt": "No suggestion link.",
            "trigger_type": "time",
            "at": "09:00",
            "days": "mon",
        },
    )
    assert resp.status_code == 200

    remaining = client.get("/automations/suggestions").json()["suggestions"]
    assert [s["id"] for s in remaining] == ["a"]


async def test_refresh_recomputes_and_returns_active(client):
    body = client.post("/automations/suggestions/refresh").json()
    assert [s["id"] for s in body["suggestions"]] == ["refreshed"]
    assert body["suggestions"][0]["triggers"] == [{"type": "time", "at": "09:00", "days": "mon"}]


async def test_refresh_returns_503_when_suggester_unavailable(client):
    class _UnavailableRuntime:
        async def refresh_suggestions(self):
            raise SuggesterUnavailableError("memory or cheap_llm is not available")

    app.dependency_overrides[require_automation_runtime] = lambda: _UnavailableRuntime()

    resp = client.post("/automations/suggestions/refresh")

    assert resp.status_code == 503
