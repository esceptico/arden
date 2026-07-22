from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class OutboxEvent:
    id: int
    event_type: str
    payload: dict
    idempotency_key: str
    status: str
    attempts: int
    available_at: datetime
    created_at: datetime
    updated_at: datetime
    aggregate_type: str | None = None
    aggregate_id: str | None = None
    locked_at: datetime | None = None
    locked_by: str | None = None
    last_error: str | None = None
