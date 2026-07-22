import asyncio
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from arden.context.models import SessionState
from arden.server.app import app
from arden.server.deps import require_run_registry
from arden.server.state import RunRegistry, RunState


def _make_run(session_id: str = "sess-1") -> RunState:
    run = RunState(run_id="run-1", session_id=session_id)
    run.session_state = SessionState(session_id=session_id, started_at=datetime.now(UTC))
    return run


def _client_with_run(run: RunState | None) -> TestClient:
    registry = MagicMock(spec=RunRegistry)
    registry.get_accepting_run.return_value = run
    app.dependency_overrides[require_run_registry] = lambda: registry
    return TestClient(app)


def test_set_auto_on_updates_run_only():
    run = _make_run()
    assert not hasattr(run.session_state, "skip_approvals")
    assert run.approval_controls.skip_approvals is False
    client = _client_with_run(run)
    try:
        r = client.post("/sessions/sess-1/auto", json={"value": True})
        assert r.status_code == 200
        assert r.json() == {"status": "ok", "skip_approvals": True, "auto_resolved": 0}
        assert run.approval_controls.skip_approvals is True
        assert not hasattr(run.session_state, "skip_approvals")
    finally:
        app.dependency_overrides.clear()


def test_set_auto_off_updates_run_only():
    run = _make_run()
    run.approval_controls.skip_approvals = True
    client = _client_with_run(run)
    try:
        r = client.post("/sessions/sess-1/auto", json={"value": False})
        assert r.status_code == 200
        assert run.approval_controls.skip_approvals is False
        assert not hasattr(run.session_state, "skip_approvals")
    finally:
        app.dependency_overrides.clear()


def test_set_auto_on_resolves_pending_approvals():
    run = _make_run()
    loop = asyncio.new_event_loop()
    try:
        future = loop.create_future()
        run.pending_approvals["tool-x"] = future
        client = _client_with_run(run)
        try:
            r = client.post("/sessions/sess-1/auto", json={"value": True})
            assert r.status_code == 200
            body = r.json()
            assert body["auto_resolved"] == 1
            assert future.done()
            result = future.result()
            assert result["approved"] is True
            assert result["tool_id"] == "tool-x"
        finally:
            app.dependency_overrides.clear()
    finally:
        loop.close()


def test_set_auto_off_leaves_pending_approvals_pending():
    run = _make_run()
    loop = asyncio.new_event_loop()
    try:
        future = loop.create_future()
        run.pending_approvals["tool-x"] = future
        client = _client_with_run(run)
        try:
            r = client.post("/sessions/sess-1/auto", json={"value": False})
            assert r.status_code == 200
            assert r.json()["auto_resolved"] == 0
            assert not future.done()
        finally:
            app.dependency_overrides.clear()
    finally:
        loop.close()


def test_set_auto_no_active_run_is_noop():
    client = _client_with_run(None)
    try:
        r = client.post("/sessions/sess-1/auto", json={"value": True})
        assert r.status_code == 200
        assert r.json() == {"status": "ok", "skip_approvals": True, "auto_resolved": 0}
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_resolved_future_unblocks_awaiter():
    """The whole point of auto-resolving futures: a tool that's currently
    awaiting approval should continue executing as if the user approved."""
    run = _make_run()
    future = asyncio.get_running_loop().create_future()
    run.pending_approvals["tool-x"] = future
    client = _client_with_run(run)

    async def consumer() -> dict:
        return await future

    consumer_task = asyncio.create_task(consumer())
    try:
        # Give the consumer a chance to start awaiting.
        await asyncio.sleep(0)
        r = client.post("/sessions/sess-1/auto", json={"value": True})
        assert r.status_code == 200
        result = await asyncio.wait_for(consumer_task, timeout=1.0)
        assert result["approved"] is True
    finally:
        app.dependency_overrides.clear()
