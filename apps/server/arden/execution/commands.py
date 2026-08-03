import json
from dataclasses import dataclass
from datetime import UTC, datetime

import aiosqlite

_SCHEMA = """
CREATE TABLE IF NOT EXISTS executor_commands (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    executor_id TEXT NOT NULL,
    command_type TEXT NOT NULL,
    invocation_id TEXT,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL,
    acked_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_executor_commands_pending
    ON executor_commands(executor_id, seq)
    WHERE acked_at IS NULL;
"""


@dataclass(frozen=True)
class ExecutorCommand:
    seq: int
    executor_id: str
    command_type: str
    payload: dict
    invocation_id: str | None


def _now() -> str:
    return datetime.now(UTC).isoformat()


class ExecutorCommandLog:
    """Bounded at-least-once delivery buffer, replayable by cursor.

    The durable invocation is the source of truth; commands are retained only
    until the executor acknowledges them.
    """

    def __init__(self, conn: aiosqlite.Connection):
        self._conn = conn

    async def init_schema(self) -> None:
        await self._conn.executescript(_SCHEMA)
        await self._conn.commit()

    async def append(
        self,
        executor_id: str,
        command_type: str,
        payload: dict,
        *,
        invocation_id: str | None = None,
    ) -> ExecutorCommand:
        cursor = await self._conn.execute(
            """
            INSERT INTO executor_commands (executor_id, command_type, invocation_id, payload, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (executor_id, command_type, invocation_id, json.dumps(payload), _now()),
        )
        await self._conn.commit()
        assert cursor.lastrowid is not None
        return ExecutorCommand(
            seq=cursor.lastrowid,
            executor_id=executor_id,
            command_type=command_type,
            payload=payload,
            invocation_id=invocation_id,
        )

    async def after(self, executor_id: str, cursor_seq: int) -> list[ExecutorCommand]:
        cursor = await self._conn.execute(
            """
            SELECT * FROM executor_commands
            WHERE executor_id = ? AND seq > ? AND acked_at IS NULL
            ORDER BY seq
            """,
            (executor_id, cursor_seq),
        )
        cursor.row_factory = aiosqlite.Row
        rows = await cursor.fetchall()
        return [_command_from_row(row) for row in rows]

    async def ack(self, executor_id: str, upto_seq: int) -> None:
        await self._conn.execute(
            """
            UPDATE executor_commands
            SET acked_at = ?
            WHERE executor_id = ? AND seq <= ? AND acked_at IS NULL
            """,
            (_now(), executor_id, upto_seq),
        )
        await self._conn.commit()


def _command_from_row(row: aiosqlite.Row) -> ExecutorCommand:
    return ExecutorCommand(
        seq=row["seq"],
        executor_id=row["executor_id"],
        command_type=row["command_type"],
        payload=json.loads(row["payload"]),
        invocation_id=row["invocation_id"],
    )
