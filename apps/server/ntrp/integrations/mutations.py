import hashlib
import json
import sqlite3
import threading
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from ntrp.agent import ToolEffect, ToolOutcome, ToolOutcomeStatus, ToolResult, ToolVerification
from ntrp.settings import NTRP_DIR
from ntrp.tools.core.context import ToolExecution

IDEMPOTENCY_LEDGER_SERVICE = "idempotency_ledger"
DEFAULT_LEDGER_PATH = NTRP_DIR / "idempotency.sqlite3"


class IdempotencyConflict(ValueError):
    pass


class IdempotencyLedger:
    def __init__(self, path: Path = DEFAULT_LEDGER_PATH):
        self.path = path
        self._lock = threading.Lock()

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS mutation_idempotency (
                namespace TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                payload_sha256 TEXT NOT NULL,
                state TEXT NOT NULL,
                result_json TEXT,
                PRIMARY KEY (namespace, idempotency_key)
            )
            """
        )
        return connection

    def begin(self, namespace: str, idempotency_key: str, payload: dict[str, Any]) -> ToolResult | None:
        digest = hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode()
        ).hexdigest()
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT payload_sha256, state, result_json FROM mutation_idempotency WHERE namespace=? AND idempotency_key=?",
                (namespace, idempotency_key),
            ).fetchone()
            if row:
                if row[0] != digest:
                    raise IdempotencyConflict("Idempotency key was already used with a different payload")
                if row[1] == "complete" and row[2]:
                    return _result_from_json(row[2])
                return ToolResult.failure(
                    code="mutation_uncertain",
                    message="A prior attempt with this idempotency key has no confirmed terminal receipt.",
                    preview="Verify prior attempt",
                    status=ToolOutcomeStatus.UNCERTAIN,
                    recovery_action="Verify provider state before retrying with the same idempotency key.",
                )
            connection.execute(
                "INSERT INTO mutation_idempotency VALUES (?, ?, ?, 'started', NULL)",
                (namespace, idempotency_key, digest),
            )
        return None

    def complete(self, namespace: str, idempotency_key: str, result: ToolResult) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                "UPDATE mutation_idempotency SET state='complete', result_json=? WHERE namespace=? AND idempotency_key=?",
                (_result_json(result), namespace, idempotency_key),
            )

    def abort(self, namespace: str, idempotency_key: str) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                "DELETE FROM mutation_idempotency WHERE namespace=? AND idempotency_key=?",
                (namespace, idempotency_key),
            )


def _result_json(result: ToolResult) -> str:
    return json.dumps(
        {
            "content": result.content,
            "preview": result.preview,
            "is_error": result.is_error,
            "data": result.data,
            "outcome": result.outcome.to_dict() if result.outcome else None,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _result_from_json(raw: str) -> ToolResult:
    value = json.loads(raw)
    outcome = ToolOutcome.from_dict(value["outcome"]) if value.get("outcome") else None
    data = dict(value.get("data") or {})
    data["idempotent_replay"] = True
    return ToolResult(
        content=value["content"],
        preview=value["preview"],
        is_error=bool(value["is_error"]),
        data=data,
        outcome=outcome,
    )


def mutation_result(
    *,
    content: str,
    preview: str,
    operation: str,
    target: str,
    receipt: str | None,
    before_ref: str | None = None,
    after_ref: str | None = None,
    observed: str | None = None,
    data: dict[str, Any] | None = None,
) -> ToolResult:
    status = ToolOutcomeStatus.SUCCEEDED if after_ref or observed else ToolOutcomeStatus.UNCERTAIN
    return ToolResult(
        content=content,
        preview=preview,
        is_error=status is ToolOutcomeStatus.UNCERTAIN,
        data=data,
        outcome=ToolOutcome(
            status=status,
            effect=ToolEffect(operation=operation, target=target, before_ref=before_ref, after_ref=after_ref),
            verification=(
                ToolVerification(postcondition="Provider mutation is readable by its returned reference", observed=observed)
                if observed
                else None
            ),
            receipt=receipt,
        ),
    )


async def execute_idempotent(
    execution: ToolExecution,
    *,
    namespace: str,
    idempotency_key: str,
    payload: dict[str, Any],
    invoke: Callable[[], Awaitable[ToolResult]],
) -> ToolResult:
    ledger = execution.ctx.services.get(IDEMPOTENCY_LEDGER_SERVICE)
    if not isinstance(ledger, IdempotencyLedger):
        ledger = IdempotencyLedger()
    try:
        replay = ledger.begin(namespace, idempotency_key, payload)
    except IdempotencyConflict as error:
        return ToolResult.failure(
            code="idempotency_conflict",
            message=str(error),
            preview="Idempotency conflict",
            recovery_action="Use the original payload or a new idempotency key.",
        )
    if replay is not None:
        return replay
    result = await invoke()
    if result.outcome and result.outcome.status is ToolOutcomeStatus.SUCCEEDED:
        ledger.complete(namespace, idempotency_key, result)
    elif result.outcome and result.outcome.status is ToolOutcomeStatus.FAILED:
        ledger.abort(namespace, idempotency_key)
    return result
