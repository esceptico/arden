"""Strict startup contract for the context SQLite database."""

import aiosqlite

from arden.context.errors import SessionSchemaError
from arden.context.schema_manifest import assert_current_schema as _assert_current_shape
from arden.context.schema_sql import CANONICAL_SCHEMA_SQL

CURRENT_VERSION = 11


async def initialize_context_schema(conn: aiosqlite.Connection) -> None:
    """Create a fresh schema or verify the current schema exactly."""

    if await _is_fresh(conn):
        await _create_fresh(conn)
        return

    version = await _schema_version(conn)
    if version == CURRENT_VERSION:
        await _assert_current_shape(conn)
        return
    if version > CURRENT_VERSION:
        raise SessionSchemaError(f"context schema v{version} is newer than this server")
    raise SessionSchemaError(f"context schema v{version} is unsupported; only fresh or v{CURRENT_VERSION} is accepted")


async def _is_fresh(conn: aiosqlite.Connection) -> bool:
    rows = await conn.execute_fetchall("SELECT 1 FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' LIMIT 1")
    return not rows


async def _create_fresh(conn: aiosqlite.Connection) -> None:
    await conn.execute("PRAGMA auto_vacuum=INCREMENTAL")
    mode = await conn.execute_fetchall("PRAGMA auto_vacuum")
    if int(mode[0][0]) != 2:
        await conn.execute("VACUUM")
    try:
        await conn.executescript(f"BEGIN IMMEDIATE;\n{CANONICAL_SCHEMA_SQL}")
        await _assert_current_shape(conn)
        await conn.commit()
    except BaseException:
        await conn.rollback()
        raise


async def _schema_version(conn: aiosqlite.Connection) -> int:
    meta = await conn.execute_fetchall(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'session_store_meta'"
    )
    if not meta:
        raise SessionSchemaError("context database is partial: session_store_meta is missing")

    rows = await conn.execute_fetchall("SELECT key, value FROM session_store_meta ORDER BY key")
    if len(rows) != 1 or rows[0]["key"] != "schema_version":
        raise SessionSchemaError("context schema metadata must contain exactly one schema_version row")
    raw_version = rows[0]["value"]
    try:
        version = int(raw_version)
    except (TypeError, ValueError) as exc:
        raise SessionSchemaError("persisted context schema version is invalid") from exc
    if str(version) != raw_version:
        raise SessionSchemaError("persisted context schema version must be a canonical integer")
    return version
