import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import aiosqlite

_SCHEMA = """
CREATE TABLE IF NOT EXISTS executor_devices (
    executor_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    capabilities_json TEXT NOT NULL,
    enrolled_at TEXT NOT NULL,
    last_seen_at TEXT,
    revoked_at TEXT
);
"""


@dataclass(frozen=True)
class ExecutorDevice:
    executor_id: str
    name: str
    capabilities: tuple[str, ...]
    enrolled_at: datetime
    last_seen_at: datetime | None
    revoked_at: datetime | None


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(UTC).isoformat()


class ExecutorDeviceStore:
    def __init__(self, conn: aiosqlite.Connection):
        self._conn = conn

    async def init_schema(self) -> None:
        await self._conn.executescript(_SCHEMA)
        await self._conn.commit()

    async def enroll(self, *, name: str, capabilities: list[str]) -> tuple[ExecutorDevice, str]:
        """Returns the device and its plaintext token; only the hash is stored."""
        executor_id = f"exec_{uuid.uuid4().hex[:12]}"
        token = secrets.token_urlsafe(32)
        await self._conn.execute(
            """
            INSERT INTO executor_devices (executor_id, name, token_hash, capabilities_json, enrolled_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (executor_id, name, _hash_token(token), ",".join(capabilities), _now()),
        )
        await self._conn.commit()
        device = await self.get(executor_id)
        assert device is not None
        return device, token

    async def authenticate(self, token: str) -> ExecutorDevice | None:
        cursor = await self._conn.execute(
            "SELECT * FROM executor_devices WHERE token_hash = ? AND revoked_at IS NULL",
            (_hash_token(token),),
        )
        cursor.row_factory = aiosqlite.Row
        row = await cursor.fetchone()
        return _device_from_row(row) if row else None

    async def get(self, executor_id: str) -> ExecutorDevice | None:
        cursor = await self._conn.execute(
            "SELECT * FROM executor_devices WHERE executor_id = ?",
            (executor_id,),
        )
        cursor.row_factory = aiosqlite.Row
        row = await cursor.fetchone()
        return _device_from_row(row) if row else None

    async def touch(self, executor_id: str) -> None:
        await self._conn.execute(
            "UPDATE executor_devices SET last_seen_at = ? WHERE executor_id = ?",
            (_now(), executor_id),
        )
        await self._conn.commit()

    async def revoke(self, executor_id: str) -> None:
        await self._conn.execute(
            "UPDATE executor_devices SET revoked_at = ? WHERE executor_id = ? AND revoked_at IS NULL",
            (_now(), executor_id),
        )
        await self._conn.commit()

    async def list_devices(self) -> list[ExecutorDevice]:
        cursor = await self._conn.execute("SELECT * FROM executor_devices ORDER BY enrolled_at")
        cursor.row_factory = aiosqlite.Row
        rows = await cursor.fetchall()
        return [_device_from_row(row) for row in rows]


def _device_from_row(row: aiosqlite.Row) -> ExecutorDevice:
    return ExecutorDevice(
        executor_id=row["executor_id"],
        name=row["name"],
        capabilities=tuple(part for part in row["capabilities_json"].split(",") if part),
        enrolled_at=datetime.fromisoformat(row["enrolled_at"]),
        last_seen_at=datetime.fromisoformat(row["last_seen_at"]) if row["last_seen_at"] else None,
        revoked_at=datetime.fromisoformat(row["revoked_at"]) if row["revoked_at"] else None,
    )
