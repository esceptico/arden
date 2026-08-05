import hashlib
import json
import sqlite3
import threading
from collections.abc import Awaitable, Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

from arden.agent import ToolEffect, ToolOutcome, ToolOutcomeStatus, ToolResult, ToolVerification
from arden.integrations.base import IntegrationConnectionError, IntegrationOperationError
from arden.integrations.tool_errors import operation_error_result
from arden.tools.core.context import ToolExecution

IDEMPOTENCY_LEDGER_SERVICE = "idempotency_ledger"
_PROVEN_NO_EFFECT_CODES = frozenset({"invalid_ref", "not_found", "rate_limited"})


class IdempotencyConflict(ValueError):
    pass


class IdempotencyLedger:
    def __init__(self, path: Path):
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
                    recovery_action=(
                        "Verify provider state. If the effect is absent, retry with a new idempotency key; "
                        "do not rotate the key before verification."
                    ),
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
    return result.serialized_payload()


def _result_from_json(raw: str) -> ToolResult:
    result = ToolResult.from_serialized_payload(raw)
    if result is None:
        raise ValueError("persisted mutation result is not a ToolResult envelope")
    data = dict(result.data or {})
    data["idempotent_replay"] = True
    return replace(result, data=data)


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
                ToolVerification(postcondition="Provider returned a mutation reference", observed=observed)
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
    before_mutation: Callable[[], Awaitable[None]] | None = None,
) -> ToolResult:
    ledger = execution.ctx.services.get(IDEMPOTENCY_LEDGER_SERVICE)
    if not isinstance(ledger, IdempotencyLedger):
        raise RuntimeError("Idempotency ledger service is not configured")
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
    if before_mutation is not None:
        try:
            await before_mutation()
        except BaseException:
            ledger.abort(namespace, idempotency_key)
            raise
    try:
        result = await invoke()
    except IntegrationConnectionError:
        ledger.abort(namespace, idempotency_key)
        raise
    except IntegrationOperationError as error:
        return _mutation_uncertain(operation_error_result(error, preview="Mutation failed"))
    outcome = result.outcome
    if outcome and outcome.status is ToolOutcomeStatus.SUCCEEDED:
        ledger.complete(namespace, idempotency_key, result)
        return result
    if outcome and outcome.status is ToolOutcomeStatus.DENIED:
        ledger.abort(namespace, idempotency_key)
        return result
    if outcome and outcome.status is ToolOutcomeStatus.FAILED:
        error = outcome.error
        if error is not None and error.code in _PROVEN_NO_EFFECT_CODES:
            ledger.abort(namespace, idempotency_key)
            return result
    if outcome and outcome.status is ToolOutcomeStatus.UNCERTAIN:
        return result
    return _mutation_uncertain(result)


def _mutation_uncertain(result: ToolResult) -> ToolResult:
    original_error = result.outcome.error if result.outcome else None
    data = dict(result.data or {})
    if original_error is not None:
        data["original_error"] = {"code": original_error.code, "retryable": original_error.retryable}
    return ToolResult.failure(
        code="mutation_uncertain",
        message="The provider did not confirm whether the mutation was applied.",
        preview="Verify provider state",
        status=ToolOutcomeStatus.UNCERTAIN,
        recovery_action=(
            "Verify provider state. If the effect is absent, retry with a new idempotency key; "
            "do not rotate the key before verification."
        ),
        data=data or None,
    )
