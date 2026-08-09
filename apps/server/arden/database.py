import asyncio
from collections.abc import Awaitable, Callable, Iterable
from pathlib import Path
from typing import Any, TypeVar

import aiosqlite
import numpy as np
import sqlite_vec

T = TypeVar("T")


class WriteCoordinator:
    """Serialize transactions that write to the same SQLite database."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._owner: CoordinatedConnection | None = None
        self._owner_task: asyncio.Task[Any] | None = None

    async def acquire(self, connection: "CoordinatedConnection") -> None:
        task = asyncio.current_task()
        if task is None:
            raise RuntimeError("database operation has no asyncio task")
        if self._owner is connection and self._owner_task is task:
            return
        if self._owner_task is task:
            raise RuntimeError("one task cannot write through two SQLite connections at once")
        await self._lock.acquire()
        self._owner = connection
        self._owner_task = task

    def owns_current_task(self, connection: "CoordinatedConnection") -> bool:
        return self._owner is connection and self._owner_task is asyncio.current_task()

    def release(self, connection: "CoordinatedConnection") -> None:
        if not self.owns_current_task(connection):
            raise RuntimeError("database write coordinator released by a non-owner")
        self._owner = None
        self._owner_task = None
        self._lock.release()


class CoordinatedConnection:
    """An aiosqlite connection sharing one transaction gate with peer writers."""

    def __init__(self, connection: aiosqlite.Connection, coordinator: WriteCoordinator) -> None:
        self._connection = connection
        self.write_coordinator = coordinator

    def __getattr__(self, name: str) -> Any:
        return getattr(self._connection, name)

    async def _rollback_raw(self) -> None:
        rollback = asyncio.create_task(self._connection.rollback())
        try:
            await asyncio.shield(rollback)
        except asyncio.CancelledError:
            await rollback
            raise

    async def _run(self, operation: Callable[[], Awaitable[T]]) -> T:
        await self.write_coordinator.acquire(self)
        try:
            result = await operation()
        except BaseException:
            try:
                if self._connection.in_transaction:
                    await self._rollback_raw()
            finally:
                self.release_if_idle(force=True)
            raise
        self.release_if_idle()
        return result

    def release_if_idle(self, *, force: bool = False) -> None:
        if not self.write_coordinator.owns_current_task(self):
            return
        if force or not self._connection.in_transaction:
            self.write_coordinator.release(self)

    async def execute(self, sql: str, parameters: Iterable[Any] | None = None) -> aiosqlite.Cursor:
        params = () if parameters is None else parameters
        return await self._run(lambda: self._connection.execute(sql, params))

    async def executemany(self, sql: str, parameters: Iterable[Iterable[Any]]) -> aiosqlite.Cursor:
        return await self._run(lambda: self._connection.executemany(sql, parameters))

    async def executescript(self, sql_script: str) -> aiosqlite.Cursor:
        return await self._run(lambda: self._connection.executescript(sql_script))

    async def execute_insert(self, sql: str, parameters: Iterable[Any] | None = None) -> aiosqlite.Row | None:
        params = () if parameters is None else parameters
        return await self._run(lambda: self._connection.execute_insert(sql, params))

    async def execute_fetchall(
        self,
        sql: str,
        parameters: Iterable[Any] | None = None,
    ) -> list[aiosqlite.Row]:
        params = () if parameters is None else parameters
        return await self._run(lambda: self._connection.execute_fetchall(sql, params))

    async def commit(self) -> None:
        await self._run(self._connection.commit)

    async def rollback(self) -> None:
        await self.write_coordinator.acquire(self)
        try:
            await self._rollback_raw()
        finally:
            self.release_if_idle(force=True)

    async def close(self) -> None:
        await self.write_coordinator.acquire(self)
        try:
            await self._connection.close()
        finally:
            self.release_if_idle(force=True)


async def rollback_safely(connection: aiosqlite.Connection | CoordinatedConnection) -> None:
    if isinstance(connection, CoordinatedConnection):
        await connection.rollback()
    else:
        await asyncio.shield(connection.rollback())


def serialize_embedding(embedding: np.ndarray | list[float] | None) -> bytes | None:
    if embedding is None:
        return None
    arr = embedding if isinstance(embedding, np.ndarray) else np.array(embedding)
    arr = arr.astype(np.float32)
    norm = np.linalg.norm(arr)
    if norm > 0:
        arr = arr / norm
    return arr.tobytes()


async def connect(
    db_path: Path,
    *,
    vec: bool = False,
    readonly: bool = False,
    autocommit: bool = False,
    write_coordinator: WriteCoordinator | None = None,
) -> aiosqlite.Connection | CoordinatedConnection:
    conn = aiosqlite.connect(db_path, isolation_level=None if readonly or autocommit else "")
    # aiosqlite (0.22) spawns a non-daemon worker thread that blocks on its op
    # queue; a connection that's never closed wedges interpreter shutdown
    # (threading._shutdown joins it forever). Mark the worker daemon BEFORE the
    # await (which starts the thread) so a leaked handle can't hang process exit
    # — e.g. a pytest run that finishes but never returns.
    conn._thread.daemon = True
    conn = await conn
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA journal_mode=WAL;")
    await conn.execute("PRAGMA synchronous=NORMAL;")
    await conn.execute("PRAGMA busy_timeout=30000;")
    if readonly:
        await conn.execute("PRAGMA query_only=ON;")
    else:
        await conn.execute("PRAGMA foreign_keys=ON;")
    if vec:
        await conn.enable_load_extension(True)
        await conn.load_extension(sqlite_vec.loadable_path())
        await conn.enable_load_extension(False)
    if write_coordinator is not None:
        return CoordinatedConnection(conn, write_coordinator)
    return conn
