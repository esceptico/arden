"""Schema-v2 codec for immutable memory ledger entries."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import TYPE_CHECKING, Literal

from arden.memory.markers import META_RE
from arden.memory.models import Kind, SourceRef, TimePrecision

if TYPE_CHECKING:
    from collections.abc import Mapping

_LINE_RE = re.compile(
    r"^- (?P<occurred>\S+) \^(?P<id>[\w-]+) \[(?P<kind>[\w-]+)\]"
    r"(?P<tags>(?: \[(?:pin|imp:\d+|ent:[a-z0-9-]+)\])*) (?P<text>.*)$"
)
_RFC3339_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_AUTHORITATIVE_FIELDS = frozenset({"id", "text", "kind", "occurred_at", "pinned", "imp", "entity"})
_META_FIELDS = frozenset(
    {"recorded_at", "sequence", "time_precision", "scope", "sources", "supersedes", "successor_id", "operation"}
)


@dataclass(frozen=True)
class LedgerMeta:
    recorded_at: str
    sequence: int
    time_precision: TimePrecision
    scope_kind: str
    scope_key: str | None
    sources: tuple[SourceRef, ...]
    supersedes: tuple[str, ...] = ()
    successor_id: str | None = None
    operation: Literal["record", "retract"] = "record"
    extra: Mapping[str, object] = field(default_factory=dict)
    scope_extra: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class LedgerEntry:
    id: str
    text: str
    kind: Kind
    occurred_at: str | None
    meta: LedgerMeta
    pinned: bool = False
    imp: int | None = None
    entity: tuple[str, ...] = ()


def _validate_rfc3339(value: str, *, allow_date: bool = False) -> None:
    if allow_date and _DATE_RE.fullmatch(value):
        try:
            date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"date must be a valid RFC 3339 full-date: {value!r}") from exc
        return
    if not _RFC3339_RE.fullmatch(value):
        raise ValueError(f"timestamp must be RFC 3339 with an explicit offset: {value!r}")
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"timestamp must be RFC 3339 with an explicit offset: {value!r}") from exc


def _parse_tags(raw: str) -> tuple[bool, int | None, tuple[str, ...], bool]:
    tags = re.findall(r"\[([^]]+)\]", raw)
    pinned = "pin" in tags
    superseded = "superseded" in tags
    importance = [int(tag.removeprefix("imp:")) for tag in tags if tag.startswith("imp:")]
    if len(importance) > 1:
        raise ValueError("ledger line has duplicate importance tags")
    entities = tuple(tag.removeprefix("ent:") for tag in tags if tag.startswith("ent:"))
    return pinned, importance[0] if importance else None, entities, superseded


def _source_from_dict(data: object) -> SourceRef:
    if not isinstance(data, dict):
        raise ValueError("ledger source must be an object")
    source = SourceRef.from_dict(data)
    if source.time_precision not in ("millisecond", "second", "minute", "day", "unknown"):
        raise ValueError("invalid source time_precision")
    if source.captured_at:
        _validate_rfc3339(source.captured_at)
    if source.occurred_at:
        _validate_rfc3339(source.occurred_at, allow_date=source.time_precision == "day")
    return source


def _parse_v2(readable: str, metadata: str) -> LedgerEntry:
    line_match = _LINE_RE.fullmatch(readable)
    meta_match = META_RE.fullmatch(metadata)
    if line_match is None or meta_match is None:
        raise ValueError("invalid schema-v2 ledger entry")
    try:
        raw_meta = json.loads(meta_match["meta"])
    except json.JSONDecodeError as exc:
        raise ValueError("invalid schema-v2 metadata JSON") from exc
    if not isinstance(raw_meta, dict):
        raise ValueError("ledger metadata must be an object")
    duplicates = _AUTHORITATIVE_FIELDS.intersection(raw_meta)
    if duplicates:
        raise ValueError(f"metadata duplicates authoritative readable fields: {sorted(duplicates)}")

    missing = {"recorded_at", "sequence", "time_precision", "scope", "sources"}.difference(raw_meta)
    if missing:
        raise ValueError(f"ledger metadata is missing required fields: {sorted(missing)}")
    scope = raw_meta["scope"]
    if not isinstance(scope, dict) or not isinstance(scope.get("kind"), str):
        raise ValueError("ledger scope must contain a string kind")
    if scope.get("key") is not None and not isinstance(scope["key"], str):
        raise ValueError("ledger scope key must be a string or null")
    if not isinstance(raw_meta["recorded_at"], str):
        raise ValueError("ledger recorded_at must be a string")
    _validate_rfc3339(raw_meta["recorded_at"])
    if not isinstance(raw_meta["sequence"], int) or isinstance(raw_meta["sequence"], bool):
        raise ValueError("ledger sequence must be an integer")
    precision = raw_meta["time_precision"]
    if precision not in ("millisecond", "second", "minute", "day", "unknown"):
        raise ValueError("invalid ledger time_precision")
    if not isinstance(raw_meta["sources"], list):
        raise ValueError("ledger sources must be a list")
    supersedes = raw_meta.get("supersedes", [])
    if not isinstance(supersedes, list) or not all(isinstance(item, str) for item in supersedes):
        raise ValueError("ledger supersedes must be a list of strings")
    successor_id = raw_meta.get("successor_id")
    if successor_id is not None and not isinstance(successor_id, str):
        raise ValueError("ledger successor_id must be a string")
    operation = raw_meta.get("operation", "record")
    if operation not in ("record", "retract"):
        raise ValueError("invalid ledger operation")

    occurred_token = line_match["occurred"]
    occurred_at = None if occurred_token == "unknown" else occurred_token
    if occurred_at is not None:
        _validate_rfc3339(occurred_at, allow_date=precision == "day")
    pinned, imp, entity, _ = _parse_tags(line_match["tags"])
    try:
        kind = Kind(line_match["kind"])
    except ValueError as exc:
        raise ValueError(f"invalid ledger kind: {line_match['kind']!r}") from exc

    return LedgerEntry(
        id=line_match["id"],
        text=line_match["text"],
        kind=kind,
        occurred_at=occurred_at,
        pinned=pinned,
        imp=imp,
        entity=entity,
        meta=LedgerMeta(
            recorded_at=raw_meta["recorded_at"],
            sequence=raw_meta["sequence"],
            time_precision=precision,
            scope_kind=scope["kind"],
            scope_key=scope.get("key"),
            sources=tuple(_source_from_dict(item) for item in raw_meta["sources"]),
            supersedes=tuple(supersedes),
            successor_id=successor_id,
            operation=operation,
            extra={key: value for key, value in raw_meta.items() if key not in _META_FIELDS},
            scope_extra={key: value for key, value in scope.items() if key not in {"kind", "key"}},
        ),
    )


def parse_ledger_entry(raw: str) -> LedgerEntry:
    """Parse one schema-v2 two-line entry."""
    lines = raw.rstrip("\r\n").splitlines()
    if len(lines) == 2:
        return _parse_v2(lines[0], lines[1])
    raise ValueError("schema-v2 ledger entry must contain a readable line and metadata line")


def render_ledger_entry(entry: LedgerEntry) -> str:
    """Render one entry without normalizing its timestamp strings."""
    conflicting = _AUTHORITATIVE_FIELDS.intersection(entry.meta.extra)
    if conflicting:
        raise ValueError(f"metadata duplicates authoritative readable fields: {sorted(conflicting)}")
    occurred = entry.occurred_at or "unknown"
    if entry.occurred_at is not None:
        _validate_rfc3339(entry.occurred_at, allow_date=entry.meta.time_precision == "day")
    _validate_rfc3339(entry.meta.recorded_at)
    tags = " [pin]" if entry.pinned else ""
    if entry.imp is not None:
        tags += f" [imp:{entry.imp}]"
    tags += "".join(f" [ent:{entity}]" for entity in entry.entity)
    readable = f"- {occurred} ^{entry.id} [{entry.kind}]{tags} {entry.text}"
    scope: dict[str, object] = dict(entry.meta.scope_extra)
    scope["kind"] = entry.meta.scope_kind
    if entry.meta.scope_key is not None:
        scope["key"] = entry.meta.scope_key
    metadata: dict[str, object] = {
        "recorded_at": entry.meta.recorded_at,
        "sequence": entry.meta.sequence,
        "time_precision": entry.meta.time_precision,
        "scope": scope,
        "sources": [source.to_dict() for source in entry.meta.sources],
    }
    if entry.meta.supersedes:
        metadata["supersedes"] = list(entry.meta.supersedes)
    if entry.meta.successor_id is not None:
        metadata["successor_id"] = entry.meta.successor_id
    if entry.meta.operation != "record":
        metadata["operation"] = entry.meta.operation
    metadata.update(entry.meta.extra)
    encoded = json.dumps(metadata, ensure_ascii=False, separators=(",", ":"))
    return f"{readable}\n  <!-- arden:meta {encoded} -->"
