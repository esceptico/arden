import json
from collections.abc import Mapping
from datetime import datetime

import aiosqlite

from arden.automation.models import Automation, IdempotencyClaim
from arden.automation.triggers import parse_triggers
from arden.core.public_refs import is_public_ref

AUTOMATION_RUN_FIELDS = (
    "id",
    "task_id",
    "chat_run_id",
    "chat_session_id",
    "started_at",
    "ended_at",
    "status",
    "result",
    "error",
)

MODEL_AUTOMATION_RUN_FIELDS = (
    "automation_ref",
    "chat_session_id",
    "started_at",
    "ended_at",
    "status",
    "result",
    "error",
)

_CONTROL_BYTES = ("\x1f", "\x00")


def parse_datetime(raw: str | None) -> datetime | None:
    return None if raw is None else datetime.fromisoformat(raw)


def required_datetime(raw: str | None) -> datetime:
    if raw is None:
        raise RuntimeError("persisted automation datetime is null")
    return datetime.fromisoformat(raw)


def _text(row: Mapping[str, object], field: str) -> str:
    value = row[field]
    if not isinstance(value, str):
        raise RuntimeError(f"persisted automation {field} is not text")
    return value


def _optional_text(row: Mapping[str, object], field: str) -> str | None:
    value = row[field]
    if value is not None and not isinstance(value, str):
        raise RuntimeError(f"persisted automation {field} is not text or null")
    return value


def _bool(row: Mapping[str, object], field: str) -> bool:
    value = row[field]
    if type(value) is not int or value not in (0, 1):
        raise RuntimeError(f"persisted automation {field} is not a SQLite boolean")
    return bool(value)


def _int(row: Mapping[str, object], field: str) -> int:
    value = row[field]
    if type(value) is not int:
        raise RuntimeError(f"persisted automation {field} is not an integer")
    return value


def _optional_int(row: Mapping[str, object], field: str) -> int | None:
    value = row[field]
    if value is None:
        return None
    if type(value) is not int:
        raise RuntimeError(f"persisted automation {field} is not an integer or null")
    return value


def automation_run(row: aiosqlite.Row) -> dict[str, object]:
    """Project a persisted run without exposing it to the model."""
    return {field: row[field] for field in AUTOMATION_RUN_FIELDS}


def model_automation_run(row: aiosqlite.Row) -> dict[str, object]:
    """Project a persisted run through its stable model-facing reference."""
    automation_ref = row["automation_ref"]
    if not isinstance(automation_ref, str) or not is_public_ref(automation_ref):
        raise RuntimeError("persisted automation run has an invalid automation_ref")
    return {field: row[field] for field in MODEL_AUTOMATION_RUN_FIELDS}


def automation(row: Mapping[str, object]) -> Automation:
    automation_ref = _text(row, "automation_ref")
    if not is_public_ref(automation_ref):
        raise RuntimeError("persisted automation has an invalid automation_ref")
    return Automation(
        task_id=_text(row, "task_id"),
        automation_ref=automation_ref,
        name=_text(row, "name"),
        description=_optional_text(row, "description"),
        description_source=_optional_text(row, "description_source"),
        prompt=_text(row, "prompt"),
        model=_optional_text(row, "model"),
        triggers=parse_triggers(_text(row, "triggers")),
        enabled=_bool(row, "enabled"),
        created_at=datetime.fromisoformat(_text(row, "created_at")),
        next_run_at=parse_datetime(_optional_text(row, "next_run_at")),
        last_run_at=parse_datetime(_optional_text(row, "last_run_at")),
        last_result=_optional_text(row, "last_result"),
        running_since=parse_datetime(_optional_text(row, "running_since")),
        auto_approve=_bool(row, "auto_approve"),
        handler=_optional_text(row, "handler"),
        builtin=_bool(row, "builtin"),
        cooldown_minutes=_optional_int(row, "cooldown_minutes"),
        kind=_text(row, "kind"),
        max_iterations=_optional_int(row, "max_iterations"),
        iteration_count=_int(row, "iteration_count"),
        stop_when=_optional_text(row, "stop_when"),
        max_age_days=_optional_int(row, "max_age_days"),
        thread_id=_optional_text(row, "thread_id"),
        read_history=_bool(row, "read_history"),
        parent_automation_id=_optional_text(row, "parent_automation_id"),
        idempotency_key=_optional_text(row, "idempotency_key"),
        idempotency_scope=_optional_text(row, "idempotency_scope"),
        tool_scope=_text(row, "tool_scope"),
        triggers_source=_optional_text(row, "triggers_source"),
    )


def serialize_triggers(triggers: list) -> str:
    return json.dumps([{"type": trigger.type, **trigger.params()} for trigger in triggers])


def automation_values(item: Automation) -> tuple[object, ...]:
    return (
        item.task_id,
        item.automation_ref,
        item.name,
        item.description,
        item.description_source,
        item.prompt,
        item.model,
        serialize_triggers(item.triggers),
        int(item.enabled),
        item.created_at.isoformat(),
        item.last_run_at.isoformat() if item.last_run_at else None,
        item.next_run_at.isoformat() if item.next_run_at else None,
        item.last_result,
        item.running_since.isoformat() if item.running_since else None,
        int(item.auto_approve),
        item.handler,
        int(item.builtin),
        item.cooldown_minutes,
        item.kind,
        item.max_iterations,
        int(item.iteration_count),
        item.stop_when,
        item.max_age_days,
        item.thread_id,
        int(item.read_history),
        item.parent_automation_id,
        item.idempotency_key,
        item.idempotency_scope,
        item.tool_scope,
        item.triggers_source,
    )


def validate_idempotency_claim(
    *,
    scope: str,
    key: str,
    parent_automation_id: str | None,
    parent_fire_at: str | None,
    attempt_n: int | None,
    automation_task_id: str | None = None,
) -> None:
    if scope == "global":
        if parent_automation_id is not None or parent_fire_at is not None or attempt_n is not None:
            raise ValueError("scope='global' must not set parent_automation_id, parent_fire_at, or attempt_n")
    elif scope == "run":
        if parent_automation_id is None or parent_fire_at is None or attempt_n is not None:
            raise ValueError("scope='run' requires parent_automation_id and parent_fire_at and forbids attempt_n")
    elif scope == "attempt":
        if parent_automation_id is None or parent_fire_at is None or attempt_n is None:
            raise ValueError("scope='attempt' requires parent_automation_id, parent_fire_at, and attempt_n")
    else:
        raise ValueError(f"unknown idempotency scope: {scope!r}")

    for name, value in (
        ("key", key),
        ("parent_automation_id", parent_automation_id),
        ("parent_fire_at", parent_fire_at),
        ("automation_task_id", automation_task_id),
    ):
        if value is not None and any(control in value for control in _CONTROL_BYTES):
            raise ValueError(f"{name} must not contain control bytes \\x1f or \\x00")


def claim_id(
    *,
    scope: str,
    key: str,
    parent_automation_id: str | None,
    parent_fire_at: str | None,
    attempt_n: int | None,
) -> str:
    null = "\x00"
    return "\x1f".join(
        (
            scope,
            key,
            parent_automation_id if parent_automation_id is not None else null,
            parent_fire_at if parent_fire_at is not None else null,
            str(attempt_n) if attempt_n is not None else null,
        )
    )


def idempotency_claim(row: aiosqlite.Row) -> IdempotencyClaim:
    return IdempotencyClaim(
        claim_id=row["claim_id"],
        scope=row["scope"],
        key=row["key"],
        parent_automation_id=row["parent_automation_id"],
        parent_fire_at=row["parent_fire_at"],
        attempt_n=int(row["attempt_n"]) if row["attempt_n"] is not None else None,
        claimed_at=datetime.fromisoformat(row["claimed_at"]),
        automation_task_id=row["automation_task_id"],
    )
