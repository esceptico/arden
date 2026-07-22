import json
from datetime import UTC, datetime
from typing import Any

import aiosqlite

_SCHEMA = """
CREATE TABLE IF NOT EXISTS monitor_state (
    namespace TEXT PRIMARY KEY,
    state TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

_SQL_GET_STATE = "SELECT state FROM monitor_state WHERE namespace = ?"

_SQL_UPSERT_STATE = """
INSERT INTO monitor_state(namespace, state, updated_at)
VALUES (?, ?, ?)
ON CONFLICT(namespace) DO UPDATE SET
    state = excluded.state,
    updated_at = excluded.updated_at
"""


class MonitorStateStore:
    def __init__(self, conn: aiosqlite.Connection):
        self.conn = conn

    async def init_schema(self) -> None:
        await self.conn.executescript(_SCHEMA)
        await self.conn.commit()

    async def get_state(self, namespace: str) -> dict[str, Any]:
        rows = await self.conn.execute_fetchall(_SQL_GET_STATE, (namespace,))
        if not rows:
            return {}
        raw = rows[0]["state"]
        try:
            parsed = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}

    async def set_state(self, namespace: str, state: dict[str, Any]) -> None:
        await self.conn.execute(
            _SQL_UPSERT_STATE,
            (namespace, json.dumps(state), datetime.now(UTC).isoformat()),
        )
        await self.conn.commit()
