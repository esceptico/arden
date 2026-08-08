from datetime import UTC, datetime
from types import SimpleNamespace

from fastapi.testclient import TestClient

from arden.context.models import SessionData, SessionState
from arden.server.app import app
from arden.server.bus import BusRegistry
from arden.server.deps import get_bus_registry, require_session_service
from arden.server.runtime import get_runtime
from arden.server.state import RunRegistry, RunStatus
from arden.tools.executor import ToolExecutor


def test_context_routes_are_registered():
    paths = TestClient(app).get("/openapi.json").json()["paths"]

    assert "/context" in paths
    assert "/compact" in paths
    assert "/directives" in paths


class _Config:
    chat_model = "claude-sonnet-4-6"
    compression_threshold = 0.8
    max_messages = 250
    compression_keep_ratio = 0.2
    summary_max_tokens = 1500


class _Runtime:
    def __init__(self):
        self.config = _Config()
        self.executor = ToolExecutor()
        self.run_registry = RunRegistry()


class _EmptyEventStore:
    async def get_latest_session_event_seq(self, session_id: str) -> int:
        return 0

    async def get_latest_session_checkpoint_seq(self, session_id: str) -> int:
        return 0


class _SessionService:
    saved = False

    def __init__(self):
        self.store = SimpleNamespace(events=_EmptyEventStore())

    async def load(self, session_id: str | None = None):
        state = SessionState(session_id=session_id or "sess-1", started_at=datetime.now(UTC))
        return SessionData(state=state, messages=[{"role": "user", "content": "hi"}], last_input_tokens=123)

    async def save(self, *args, **kwargs):
        self.saved = True

    async def list_turns(self, session_id: str, limit: int = 100):
        return [
            {
                "session_id": session_id,
                "turn_id": f"{session_id}:0",
                "turn_index": 0,
                "user_message_id": "u-1",
                "message_start_id": "u-1",
                "message_end_id": "a-1",
                "message_start_seq": 1,
                "message_end_seq": 2,
                "started_at": "2026-01-01T00:00:00+00:00",
                "ended_at": "2026-01-01T00:00:01+00:00",
            }
        ][:limit]


class _SmallSessionService(_SessionService):
    async def load(self, session_id: str | None = None):
        state = SessionState(session_id=session_id or "sess-1", started_at=datetime.now(UTC))
        messages = [{"role": "system", "content": "s"}]
        messages.extend({"role": "user", "content": f"small-{i}"} for i in range(6))
        return SessionData(state=state, messages=messages, last_input_tokens=58_517)


def test_context_usage_reports_loaded_deferred_tool_counts():
    runtime = _Runtime()
    run = runtime.run_registry.create_run("sess-1")
    run.status = RunStatus.RUNNING
    run.messages = [{"role": "user", "content": "active"}]
    run.loaded_tools.add("directives_set")

    app.dependency_overrides[get_runtime] = lambda: runtime
    app.dependency_overrides[require_session_service] = lambda: _SessionService()
    try:
        response = TestClient(app).get("/context?session_id=sess-1")
    finally:
        app.dependency_overrides.pop(get_runtime, None)
        app.dependency_overrides.pop(require_session_service, None)

    assert response.status_code == 200
    data = response.json()
    assert data["message_count"] == 1
    assert data["loaded_tool_count"] == 1
    assert data["deferred_tool_count"] >= 1
    assert data["visible_tool_count"] < data["tool_count"]


def test_context_usage_counts_canonical_active_run_messages():
    runtime = _Runtime()
    run = runtime.run_registry.create_run("sess-1")
    run.status = RunStatus.RUNNING
    run.messages = [{"role": "user", "content": f"active-{i}"} for i in range(4)]

    app.dependency_overrides[get_runtime] = lambda: runtime
    app.dependency_overrides[require_session_service] = lambda: _SessionService()
    try:
        response = TestClient(app).get("/context?session_id=sess-1")
    finally:
        app.dependency_overrides.pop(get_runtime, None)
        app.dependency_overrides.pop(require_session_service, None)

    assert response.status_code == 200
    assert response.json()["message_count"] == 4


def test_compact_rejects_active_run_to_avoid_overwriting_stream_state():
    runtime = _Runtime()
    session_service = _SessionService()
    runtime.session_service = session_service
    run = runtime.run_registry.create_run("sess-1")
    run.status = RunStatus.RUNNING
    buses = BusRegistry()

    app.dependency_overrides[get_runtime] = lambda: runtime
    app.dependency_overrides[require_session_service] = lambda: session_service
    app.dependency_overrides[get_bus_registry] = lambda: buses
    try:
        response = TestClient(app).post("/compact", json={"session_id": "sess-1"})
    finally:
        app.dependency_overrides.pop(get_runtime, None)
        app.dependency_overrides.pop(require_session_service, None)
        app.dependency_overrides.pop(get_bus_registry, None)

    assert response.status_code == 409
    assert session_service.saved is False


def test_manual_compact_bypasses_auto_threshold(monkeypatch):
    runtime = _Runtime()
    session_service = _SmallSessionService()
    runtime.session_service = session_service
    buses = BusRegistry()

    async def fake_compact_session(*_args, **_kwargs):
        return {
            "status": "compacted",
            "message": "Compacted 7 -> 5 messages (2 summarized)",
            "before_tokens": 58_517,
            "before_messages": 7,
            "after_messages": 5,
            "messages_compressed": 2,
        }

    monkeypatch.setattr("arden.server.routers.context.compact_session", fake_compact_session)

    app.dependency_overrides[get_runtime] = lambda: runtime
    app.dependency_overrides[require_session_service] = lambda: session_service
    app.dependency_overrides[get_bus_registry] = lambda: buses
    try:
        response = TestClient(app).post("/compact", json={"session_id": "sess-1"})
    finally:
        app.dependency_overrides.pop(get_runtime, None)
        app.dependency_overrides.pop(require_session_service, None)
        app.dependency_overrides.pop(get_bus_registry, None)

    assert response.status_code == 200
    assert response.json()["status"] == "compacted"


def test_manual_compact_noops_when_nothing_compactable_without_spinner_event():
    runtime = _Runtime()
    session_service = _SessionService()
    runtime.session_service = session_service
    buses = BusRegistry()

    app.dependency_overrides[get_runtime] = lambda: runtime
    app.dependency_overrides[require_session_service] = lambda: session_service
    app.dependency_overrides[get_bus_registry] = lambda: buses
    try:
        response = TestClient(app).post("/compact", json={"session_id": "sess-1"})
    finally:
        app.dependency_overrides.pop(get_runtime, None)
        app.dependency_overrides.pop(require_session_service, None)
        app.dependency_overrides.pop(get_bus_registry, None)

    assert response.status_code == 200
    assert response.json()["status"] == "nothing_to_compact"
    assert session_service.saved is False
    assert buses._buses == {}


def test_session_turns_route_returns_durable_turn_ranges():
    app.dependency_overrides[require_session_service] = lambda: _SessionService()
    try:
        response = TestClient(app).get("/session/turns?session_id=sess-1")
    finally:
        app.dependency_overrides.pop(require_session_service, None)

    assert response.status_code == 200
    assert response.json()["turns"][0]["message_start_id"] == "u-1"
    assert response.json()["turns"][0]["message_end_id"] == "a-1"
