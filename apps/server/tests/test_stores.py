import asyncio

import pytest

import arden.database as database
from arden.config import Config
from arden.core.public_refs import public_ref
from arden.server.stores import Stores


@pytest.mark.asyncio
async def test_stores_owns_distinct_event_writer_connection(tmp_path):
    stores = await Stores.connect(
        Config(arden_dir=tmp_path, memory=False, _env_file=None),
        defer_recovery=True,
    )

    assert stores.event_conn is not stores.conn
    assert stores.sessions.store.events._write_conn is stores.event_conn
    assert stores.event_conn.write_coordinator is stores.write_coordinator
    assert stores.automation_settlement_conn.write_coordinator is stores.write_coordinator
    assert stores.chat_completion_conn.write_coordinator is stores.write_coordinator

    await stores.close()

    with pytest.raises(ValueError, match="no active connection"):
        await stores.event_conn.execute("SELECT 1")
    with pytest.raises(ValueError, match="no active connection"):
        await stores.conn.execute("SELECT 1")


@pytest.mark.asyncio
async def test_stores_connect_closes_connections_when_initialization_fails(tmp_path, monkeypatch):
    opened = []
    original_connect = database.connect

    async def tracked_connect(*args, **kwargs):
        connection = await original_connect(*args, **kwargs)
        opened.append(connection)
        return connection

    async def fail_schema(_self):
        raise RuntimeError("schema failed")

    monkeypatch.setattr("arden.server.stores.database.connect", tracked_connect)
    monkeypatch.setattr("arden.server.stores.SessionStore.init_schema", fail_schema)

    with pytest.raises(RuntimeError, match="schema failed"):
        await Stores.connect(
            Config(arden_dir=tmp_path, memory=False, _env_file=None),
            defer_recovery=True,
        )

    assert len(opened) == 5
    for connection in opened:
        with pytest.raises(ValueError, match="no active connection"):
            await connection.execute("SELECT 1")


@pytest.mark.asyncio
async def test_background_activity_waits_for_session_snapshot_transaction(tmp_path):
    stores = await Stores.connect(
        Config(arden_dir=tmp_path, memory=False, _env_file=None),
        defer_recovery=True,
    )
    session_store = stores.sessions.store
    try:
        await session_store.record_background_agent_started(
            task_id="task-1",
            agent_ref=public_ref("research", "task-1", empty_slug="agent"),
            session_id="session-1",
            parent_run_id="run-1",
            command="research",
        )

        async with session_store._session_context_transaction():
            pending = asyncio.create_task(
                session_store.record_background_agent_event(
                    task_id="task-1",
                    session_id="session-1",
                    status="activity",
                    detail="working",
                )
            )
            await asyncio.sleep(0.05)
            assert not pending.done()

        assert await asyncio.wait_for(pending, timeout=1) == 2
        rows = await stores.read_conn.execute_fetchall(
            "SELECT status FROM background_agent_events WHERE session_id = ? ORDER BY seq",
            ("session-1",),
        )
        assert [row["status"] for row in rows] == ["started", "activity"]
    finally:
        await stores.close()
