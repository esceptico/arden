from pathlib import Path

import aiosqlite
import numpy as np
import sqlite_vec


def serialize_embedding(embedding: np.ndarray | list[float] | None) -> bytes | None:
    if embedding is None:
        return None
    arr = embedding if isinstance(embedding, np.ndarray) else np.array(embedding)
    arr = arr.astype(np.float32)
    norm = np.linalg.norm(arr)
    if norm > 0:
        arr = arr / norm
    return arr.tobytes()


def deserialize_embedding(data: bytes | None) -> np.ndarray | None:
    if data is None:
        return None
    return np.frombuffer(data, dtype=np.float32).copy()


async def connect(db_path: Path, *, vec: bool = False, readonly: bool = False) -> aiosqlite.Connection:
    conn = aiosqlite.connect(db_path, isolation_level=None if readonly else "")
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
    return conn
