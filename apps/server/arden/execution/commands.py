import json
from dataclasses import dataclass
from datetime import UTC, datetime

import aiosqlite

from arden.execution.command_envelope import (
    CancelToolCommand,
    ExecuteToolCommand,
    ExecutorCommandPayloadError,
    parse_executor_command,
)

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
    payload: ExecuteToolCommand | CancelToolCommand
    invocation_id: str

    @property
    def command_type(self) -> str:
        return self.payload.type

    def wire_payload(self) -> dict:
        return self.payload.model_dump(mode="json")


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
        payload: ExecuteToolCommand | CancelToolCommand,
    ) -> ExecutorCommand:
        payload_json = payload.model_dump(mode="json")
        cursor = await self._conn.execute(
            """
            INSERT INTO executor_commands (executor_id, command_type, invocation_id, payload, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (executor_id, payload.type, payload.invocation_id, json.dumps(payload_json), _now()),
        )
        await self._conn.commit()
        assert cursor.lastrowid is not None
        return ExecutorCommand(
            seq=cursor.lastrowid,
            executor_id=executor_id,
            payload=payload,
            invocation_id=payload.invocation_id,
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
    try:
        payload = parse_executor_command(json.loads(row["payload"]))
    except json.JSONDecodeError as exc:
        raise ExecutorCommandPayloadError("executor command payload is not valid JSON") from exc
    if row["command_type"] != payload.type:
        raise ValueError("executor command row type does not match its payload")
    if row["invocation_id"] != payload.invocation_id:
        raise ValueError("executor command row invocation does not match its payload")
    return ExecutorCommand(
        seq=row["seq"],
        executor_id=row["executor_id"],
        payload=payload,
        invocation_id=row["invocation_id"],
    )
