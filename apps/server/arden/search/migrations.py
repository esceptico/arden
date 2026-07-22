import aiosqlite

from arden.logging import get_logger

_logger = get_logger(__name__)

CURRENT_SCHEMA_VERSION = 1


async def _get_schema_version(conn: aiosqlite.Connection) -> int:
    try:
        rows = await conn.execute_fetchall("SELECT value FROM meta WHERE key = 'schema_version'")
        return int(rows[0]["value"]) if rows else 0
    except Exception:
        return 0


async def _set_schema_version(conn: aiosqlite.Connection, version: int) -> None:
    await conn.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', ?)",
        (str(version),),
    )


async def _table_exists(conn: aiosqlite.Connection, name: str) -> bool:
    rows = await conn.execute_fetchall(
        "SELECT 1 FROM sqlite_master WHERE name = ?",
        (name,),
    )
    return bool(rows)


async def _delete_source(conn: aiosqlite.Connection, source: str) -> int:
    rows = await conn.execute_fetchall("SELECT id FROM items WHERE source = ?", (source,))
    if not rows:
        return 0

    item_ids = [row["id"] for row in rows]
    if await _table_exists(conn, "items_vec"):
        placeholders = ",".join("?" * len(item_ids))
        await conn.execute(
            f"DELETE FROM items_vec WHERE item_id IN ({placeholders})",
            item_ids,
        )

    cursor = await conn.execute("DELETE FROM items WHERE source = ?", (source,))
    return cursor.rowcount


async def _migrate_v1(conn: aiosqlite.Connection) -> None:
    deleted = await _delete_source(conn, "notes")
    if deleted:
        _logger.info("Removed legacy notes search rows", rows=deleted)


_MIGRATIONS = ((1, _migrate_v1),)


async def run_migrations(conn: aiosqlite.Connection) -> None:
    version = await _get_schema_version(conn)
    if version >= CURRENT_SCHEMA_VERSION:
        return

    for target_version, migrate in _MIGRATIONS:
        if version >= target_version:
            continue
        _logger.info("Running search schema migration v%d", target_version)
        await migrate(conn)
        await _set_schema_version(conn, target_version)
        version = target_version
