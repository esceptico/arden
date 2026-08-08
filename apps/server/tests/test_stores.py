import pytest

import arden.database as database
from arden.config import Config
from arden.server.stores import Stores


@pytest.mark.asyncio
async def test_stores_owns_distinct_event_writer_connection(tmp_path):
    stores = await Stores.connect(
        Config(arden_dir=tmp_path, memory=False, _env_file=None),
        defer_recovery=True,
    )

    assert stores.event_conn is not stores.conn
    assert stores.sessions.store.events._write_conn is stores.event_conn

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
