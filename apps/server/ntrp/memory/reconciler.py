"""Typed validation for schema-v2 memory reconciliation batches."""

from __future__ import annotations

import re
from datetime import datetime
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict

from ntrp.memory.models import Kind, Record, SourceRef
from ntrp.memory.scopes import USER_SCOPE, MemoryScope

if TYPE_CHECKING:
    from collections.abc import Sequence

OperationName = Literal["ADD", "SUPERSEDE", "MERGE", "RETRACT", "NOOP", "ASK"]


class RecordOperation(BaseModel):
    model_config = ConfigDict(frozen=True)

    op: OperationName
    text: str | None = None
    kind: Kind | None = None
    scope: MemoryScope | None = None
    target_ids: tuple[str, ...] = ()
    question: str | None = None
    meta_labels: tuple[str, ...] | None = None
    entity_labels: tuple[str, ...] | None = None

    @classmethod
    def add(
        cls,
        text: str,
        *,
        kind: Kind = Kind.FACT,
        scope: MemoryScope = USER_SCOPE,
        meta_labels: tuple[str, ...] | None = None,
        entity_labels: tuple[str, ...] | None = None,
    ) -> RecordOperation:
        return cls(
            op="ADD",
            text=text,
            kind=kind,
            scope=scope,
            meta_labels=meta_labels,
            entity_labels=entity_labels,
        )

    @classmethod
    def supersede(
        cls,
        target_id: str,
        text: str,
        *,
        kind: Kind | None = None,
        scope: MemoryScope | None = None,
        meta_labels: tuple[str, ...] | None = None,
        entity_labels: tuple[str, ...] | None = None,
    ) -> RecordOperation:
        return cls(
            op="SUPERSEDE",
            target_ids=(target_id,),
            text=text,
            kind=kind,
            scope=scope,
            meta_labels=meta_labels,
            entity_labels=entity_labels,
        )

    @classmethod
    def merge(
        cls,
        target_ids: tuple[str, ...],
        text: str,
        *,
        kind: Kind | None = None,
        scope: MemoryScope | None = None,
    ) -> RecordOperation:
        return cls(op="MERGE", target_ids=target_ids, text=text, kind=kind, scope=scope)

    @classmethod
    def retract(cls, *target_ids: str) -> RecordOperation:
        return cls(op="RETRACT", target_ids=target_ids)

    @classmethod
    def noop(cls) -> RecordOperation:
        return cls(op="NOOP")

    @classmethod
    def ask(cls, question: str) -> RecordOperation:
        return cls(op="ASK", question=question)


def _validate_timestamp(source: SourceRef) -> None:
    if source.time_precision not in {"millisecond", "second", "minute", "day", "unknown"}:
        raise ValueError("invalid source time_precision")
    if source.captured_at:
        try:
            captured = datetime.fromisoformat(source.captured_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("source captured_at must be RFC 3339") from exc
        if captured.tzinfo is None:
            raise ValueError("source captured_at must include an offset")
    if source.occurred_at is None:
        return
    precision = source.time_precision
    if precision == "day":
        try:
            datetime.strptime(source.occurred_at, "%Y-%m-%d")
        except ValueError as exc:
            raise ValueError("source timestamp does not match day precision") from exc
        return
    try:
        occurred = datetime.fromisoformat(source.occurred_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("source occurred_at must be RFC 3339") from exc
    if occurred.tzinfo is None:
        raise ValueError("source occurred_at must include an offset")
    if precision == "minute" and (occurred.second or occurred.microsecond):
        raise ValueError("source timestamp does not match minute precision")
    if precision == "second" and occurred.microsecond:
        raise ValueError("source timestamp does not match second precision")
    fraction = re.search(r"\.(\d+)(?:Z|[+-]\d{2}:\d{2})$", source.occurred_at)
    if precision == "millisecond" and (fraction is None or len(fraction.group(1)) != 3):
        raise ValueError("source timestamp does not match millisecond precision")


def _validate_scope(scope: MemoryScope | None) -> None:
    if scope is None or scope.kind not in {"global", "user", "area", "integration"}:
        raise ValueError("operation requires a valid scope")
    if scope.kind in {"area", "integration"} and not scope.key:
        raise ValueError(f"{scope.kind} scope requires a key")
    if scope.kind in {"global", "user"} and scope.key is not None:
        raise ValueError(f"{scope.kind} scope cannot have a key")


def validate_operations(
    operations: list[RecordOperation] | tuple[RecordOperation, ...],
    records: list[Record] | tuple[Record, ...],
    source: SourceRef | Sequence[SourceRef],
) -> tuple[RecordOperation, ...]:
    """Validate a complete batch before any storage planning or mutation."""
    sources = (source,) if isinstance(source, SourceRef) else tuple(source)
    if not sources:
        raise ValueError("operation source evidence is required")
    for evidence in sources:
        explicitly_unknown = (evidence.kind == "source" and evidence.ref == "unknown") or evidence.kind == "source:unknown"
        if not explicitly_unknown and not evidence.ref.strip():
            raise ValueError("operation source evidence is required")
        _validate_timestamp(evidence)

    by_id = {record.id: record for record in records}
    normalized: list[RecordOperation] = []
    consumed: set[str] = set()
    for operation in operations:
        op = operation if isinstance(operation, RecordOperation) else RecordOperation.model_validate(operation)
        targets = tuple(dict.fromkeys(target.strip() for target in op.target_ids if target.strip()))
        missing = [target for target in targets if target not in by_id]
        if missing:
            raise ValueError(f"missing target: {missing[0]}")
        text = " ".join((op.text or "").split()) or None
        question = " ".join((op.question or "").split()) or None

        if op.op == "ADD":
            if text is None or op.kind is None or targets:
                raise ValueError("ADD requires text and kind and cannot have targets")
            _validate_scope(op.scope)
        elif op.op == "SUPERSEDE":
            if text is None or len(targets) != 1:
                raise ValueError("SUPERSEDE requires text and exactly one target")
        elif op.op == "MERGE":
            if text is None or len(targets) < 2:
                raise ValueError("MERGE requires text and at least two targets")
        elif op.op == "RETRACT":
            if not targets or text is not None:
                raise ValueError("RETRACT requires targets and cannot have text")
        elif op.op == "NOOP":
            if text is not None or question is not None or op.kind is not None or op.scope is not None or targets or op.meta_labels is not None or op.entity_labels is not None:
                raise ValueError("NOOP cannot carry payload fields")
        elif op.op == "ASK":
            if (
                question is None
                or targets
                or text is not None
                or op.kind is not None
                or op.scope is not None
                or op.meta_labels is not None
                or op.entity_labels is not None
            ):
                raise ValueError("ASK requires only a question")

        if op.op in {"SUPERSEDE", "MERGE", "RETRACT"}:
            overlap = consumed.intersection(targets)
            if overlap:
                raise ValueError(f"target appears in multiple mutations: {sorted(overlap)[0]}")
            consumed.update(targets)

        if targets and op.op not in {"NOOP", "ASK"}:
            target_scopes = {(by_id[target].scope_kind, by_id[target].scope_key) for target in targets}
            if len(target_scopes) != 1:
                raise ValueError("operation targets must share one scope")
            target_kind = Kind(by_id[targets[0]].kind)
            scope_kind, scope_key = next(iter(target_scopes))
            target_scope = MemoryScope(scope_kind or "user", scope_key)
            if op.scope is not None and op.scope != target_scope:
                raise ValueError("operation scope must match its targets")
            if op.kind is not None and op.op == "MERGE" and any(by_id[target].kind != op.kind for target in targets):
                raise ValueError("MERGE kind must match all targets")
            op = op.model_copy(update={"kind": op.kind or target_kind, "scope": op.scope or target_scope})
            _validate_scope(op.scope)

        normalized.append(op.model_copy(update={"text": text, "question": question, "target_ids": targets}))
    return tuple(normalized)
