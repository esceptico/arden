"""Explicit, fail-closed switch from legacy memory to canonical facts."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

MARKER_NAME = ".fact-ledger-cutover.json"
LEDGER_DIRECTORY = "facts"
_SCHEMA_VERSION = 1
_MODE = "fact-ledger"
_FIELDS = frozenset({"schema_version", "mode", "ledger_root", "migrated_at"})
_MAX_MARKER_BYTES = 4_096


class FactCutoverError(RuntimeError):
    """The explicit fact cutover marker is present but invalid."""


@dataclass(frozen=True, slots=True)
class FactCutover:
    migrated_at: datetime


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise FactCutoverError("fact cutover marker has duplicate fields")
        result[key] = value
    return result


def load_fact_cutover(memory_root: Path) -> FactCutover | None:
    """Return the active cutover, or None only when its marker is absent."""

    marker = Path(memory_root) / MARKER_NAME
    try:
        if marker.is_symlink():
            raise FactCutoverError("fact cutover marker must be a regular file")
        content = marker.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise FactCutoverError("fact cutover marker could not be read") from exc
    if not content or len(content) > _MAX_MARKER_BYTES:
        raise FactCutoverError("fact cutover marker has an invalid size")
    try:
        value = json.loads(content, object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError, FactCutoverError) as exc:
        raise FactCutoverError("fact cutover marker is invalid JSON") from exc
    if not isinstance(value, dict) or set(value) != _FIELDS:
        raise FactCutoverError("fact cutover marker has invalid fields")
    if type(value["schema_version"]) is not int or value["schema_version"] != _SCHEMA_VERSION:
        raise FactCutoverError("unsupported fact cutover schema")
    if value["mode"] != _MODE or value["ledger_root"] != LEDGER_DIRECTORY:
        raise FactCutoverError("fact cutover marker targets an unsupported ledger")
    migrated_at = _utc(value["migrated_at"])
    if content != fact_cutover_content(migrated_at):
        raise FactCutoverError("fact cutover marker is not canonical")
    return FactCutover(migrated_at)


def fact_cutover_content(migrated_at: datetime) -> bytes:
    """Return the canonical bytes the offline migrator must publish last."""

    if not isinstance(migrated_at, datetime) or migrated_at.tzinfo is None or migrated_at.utcoffset() != timedelta(0):
        raise ValueError("migrated_at must be a UTC datetime")
    value = {
        "schema_version": _SCHEMA_VERSION,
        "mode": _MODE,
        "ledger_root": LEDGER_DIRECTORY,
        "migrated_at": migrated_at.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z"),
    }
    return (json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _utc(value: object) -> datetime:
    if not isinstance(value, str):
        raise FactCutoverError("fact cutover migrated_at must be a UTC timestamp")
    try:
        point = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise FactCutoverError("fact cutover migrated_at is invalid") from exc
    if point.tzinfo is None or point.utcoffset() != timedelta(0):
        raise FactCutoverError("fact cutover migrated_at must be UTC")
    return point.astimezone(UTC)
