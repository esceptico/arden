import asyncio

import pytest

from arden import database


@pytest.mark.asyncio
async def test_shared_write_coordinator_waits_for_the_active_transaction(tmp_path):
    coordinator = database.WriteCoordinator()
    first = await database.connect(tmp_path / "sessions.db", write_coordinator=coordinator)
    second = await database.connect(tmp_path / "sessions.db", write_coordinator=coordinator)
    reader = await database.connect(tmp_path / "sessions.db", readonly=True)
    try:
        await first.execute("CREATE TABLE records (value TEXT NOT NULL)")
        await first.commit()
        await first.execute("BEGIN IMMEDIATE")
        await first.execute("INSERT INTO records VALUES ('first')")

        async def write_second() -> None:
            await second.execute("INSERT INTO records VALUES ('second')")
            await second.commit()

        pending = asyncio.create_task(write_second())
        await asyncio.sleep(0.05)
        assert not pending.done()
        assert await reader.execute_fetchall("SELECT value FROM records") == []

        await first.commit()
        await asyncio.wait_for(pending, timeout=1)

        rows = await reader.execute_fetchall("SELECT value FROM records ORDER BY rowid")
        assert [row["value"] for row in rows] == ["first", "second"]
    finally:
        await reader.close()
        await second.close()
        await first.close()


@pytest.mark.asyncio
async def test_shared_write_coordinator_rolls_back_and_releases_after_sql_error(tmp_path):
    coordinator = database.WriteCoordinator()
    first = await database.connect(tmp_path / "sessions.db", write_coordinator=coordinator)
    second = await database.connect(tmp_path / "sessions.db", write_coordinator=coordinator)
    try:
        await first.execute("CREATE TABLE records (value TEXT NOT NULL)")
        await first.commit()
        await first.execute("INSERT INTO records VALUES ('discarded')")

        with pytest.raises(Exception, match="no such table"):
            await first.execute("INSERT INTO missing_table VALUES ('broken')")

        await second.execute("INSERT INTO records VALUES ('kept')")
        await second.commit()
        rows = await second.execute_fetchall("SELECT value FROM records")
        assert [row["value"] for row in rows] == ["kept"]
    finally:
        await second.close()
        await first.close()


@pytest.mark.asyncio
async def test_cancelled_transaction_rolls_back_before_releasing_next_writer(tmp_path):
    coordinator = database.WriteCoordinator()
    first = await database.connect(tmp_path / "sessions.db", write_coordinator=coordinator)
    second = await database.connect(tmp_path / "sessions.db", write_coordinator=coordinator)
    started = asyncio.Event()
    blocked = asyncio.Event()
    try:
        await first.execute("CREATE TABLE records (value TEXT NOT NULL)")
        await first.commit()

        async def cancelled_transaction() -> None:
            try:
                await first.execute("BEGIN IMMEDIATE")
                await first.execute("INSERT INTO records VALUES ('discarded')")
                started.set()
                await blocked.wait()
            except BaseException:
                await database.rollback_safely(first)
                raise

        transaction = asyncio.create_task(cancelled_transaction())
        await asyncio.wait_for(started.wait(), timeout=1)
        transaction.cancel()
        with pytest.raises(asyncio.CancelledError):
            await transaction

        await second.execute("INSERT INTO records VALUES ('kept')")
        await second.commit()
        rows = await second.execute_fetchall("SELECT value FROM records")
        assert [row["value"] for row in rows] == ["kept"]
    finally:
        await second.close()
        await first.close()
