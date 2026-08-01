import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import aiosqlite

_SCHEMA = """
CREATE TABLE IF NOT EXISTS executor_leases (
    lease_id TEXT PRIMARY KEY,
    executor_id TEXT NOT NULL,
    acquired_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_executor_leases_executor
    ON executor_leases(executor_id, acquired_at);
"""


@dataclass(frozen=True)
class ExecutorLease:
    lease_id: str
    executor_id: str
    acquired_at: datetime
    expires_at: datetime


def _now() -> datetime:
    return datetime.now(UTC)


class LeaseStore:
    """One fencing token per executor connection.

    Acquiring a lease supersedes every earlier lease for that executor, so a
    reconnected executor immediately fences results from its stale
    predecessor.
    """

    def __init__(self, conn: aiosqlite.Connection, *, ttl_seconds: int):
        self._conn = conn
        self._ttl = timedelta(seconds=ttl_seconds)

    async def init_schema(self) -> None:
        await self._conn.executescript(_SCHEMA)
        await self._conn.commit()

    async def acquire(self, executor_id: str) -> ExecutorLease:
        now = _now()
        lease = ExecutorLease(
            lease_id=f"lease_{uuid.uuid4().hex[:12]}",
            executor_id=executor_id,
            acquired_at=now,
            expires_at=now + self._ttl,
        )
        await self._conn.execute("DELETE FROM executor_leases WHERE executor_id = ?", (executor_id,))
        await self._conn.execute(
            "INSERT INTO executor_leases (lease_id, executor_id, acquired_at, expires_at) VALUES (?, ?, ?, ?)",
            (lease.lease_id, executor_id, lease.acquired_at.isoformat(), lease.expires_at.isoformat()),
        )
        await self._conn.commit()
        return lease

    async def renew(self, lease_id: str) -> ExecutorLease | None:
        """Extends a currently valid lease; a stale or unknown lease renews to nothing."""
        current = await self._get(lease_id)
        if current is None or current.expires_at <= _now():
            return None
        expires_at = _now() + self._ttl
        await self._conn.execute(
            "UPDATE executor_leases SET expires_at = ? WHERE lease_id = ?",
            (expires_at.isoformat(), lease_id),
        )
        await self._conn.commit()
        return ExecutorLease(
            lease_id=current.lease_id,
            executor_id=current.executor_id,
            acquired_at=current.acquired_at,
            expires_at=expires_at,
        )

    async def release_all(self, executor_id: str) -> None:
        await self._conn.execute("DELETE FROM executor_leases WHERE executor_id = ?", (executor_id,))
        await self._conn.commit()

    async def is_current(self, lease_id: str, executor_id: str) -> bool:
        current = await self._get(lease_id)
        return current is not None and current.executor_id == executor_id and current.expires_at > _now()

    async def _get(self, lease_id: str) -> ExecutorLease | None:
        cursor = await self._conn.execute(
            "SELECT * FROM executor_leases WHERE lease_id = ?",
            (lease_id,),
        )
        cursor.row_factory = aiosqlite.Row
        row = await cursor.fetchone()
        if not row:
            return None
        return ExecutorLease(
            lease_id=row["lease_id"],
            executor_id=row["executor_id"],
            acquired_at=datetime.fromisoformat(row["acquired_at"]),
            expires_at=datetime.fromisoformat(row["expires_at"]),
        )
