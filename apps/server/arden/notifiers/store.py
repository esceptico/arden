import json

import aiosqlite

from arden.notifiers.models import NotifierConfig

SCHEMA = """
CREATE TABLE IF NOT EXISTS notifier_configs (
    name TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    config TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""

SQL_LIST = "SELECT name, type, config, created_at FROM notifier_configs ORDER BY created_at"
SQL_GET = "SELECT name, type, config, created_at FROM notifier_configs WHERE name = ?"
SQL_SAVE = "INSERT OR REPLACE INTO notifier_configs (name, type, config, created_at) VALUES (?, ?, ?, ?)"
SQL_RENAME = "UPDATE notifier_configs SET name = ?, config = ? WHERE name = ?"
SQL_DELETE = "DELETE FROM notifier_configs WHERE name = ?"


class NotifierStore:
    def __init__(self, conn: aiosqlite.Connection):
        self.conn = conn

    async def init_schema(self) -> None:
        await self.conn.executescript(SCHEMA)
        await self.conn.commit()

    async def list_all(self) -> list[NotifierConfig]:
        rows = await self.conn.execute_fetchall(SQL_LIST)
        return [NotifierConfig(**row) for row in rows]

    async def get(self, name: str) -> NotifierConfig | None:
        rows = await self.conn.execute_fetchall(SQL_GET, (name,))
        if not rows:
            return None
        return NotifierConfig(**rows[0])

    async def save(self, config: NotifierConfig) -> None:
        await self.conn.execute(
            SQL_SAVE, (config.name, config.type, json.dumps(config.config), config.created_at.isoformat())
        )
        await self.conn.commit()

    async def rename(self, old_name: str, new_name: str, new_config: dict) -> None:
        await self.conn.execute(SQL_RENAME, (new_name, json.dumps(new_config), old_name))
        await self.conn.commit()

    async def delete(self, name: str) -> bool:
        cursor = await self.conn.execute(SQL_DELETE, (name,))
        await self.conn.commit()
        return cursor.rowcount > 0
