"""Lossless adapters for values emitted by the pre-ledger memory model."""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import UTC, date, datetime
from typing import Any

from .ledger import _source, _source_precision_error
from .models import FactValidationError

_KNOWN_FIELDS = frozenset(
    {
        "kind",
        "ref",
        "captured_at",
        "scope_kind",
        "scope_key",
        "occurred_at",
        "time_precision",
        "role",
        "excerpt_hash",
    }
)
_REQUIRED_FIELDS = frozenset({"kind", "ref"})


def _json_safe(value: object, field: str) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise FactValidationError(f"{field} must be JSON-safe")
        return value
    if isinstance(value, (list, tuple)):
        return [_json_safe(item, field) for item in value]
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise FactValidationError(f"{field} keys must be strings")
            result[key] = _json_safe(item, field)
        return result
    raise FactValidationError(f"{field} must be JSON-safe")


def _utc_timestamp(value: object, field: str, *, timespec: str = "microseconds") -> str:
    if not isinstance(value, str):
        raise FactValidationError(f"{field} must be an RFC3339 timestamp")
    try:
        point = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise FactValidationError(f"invalid {field}") from exc
    if point.tzinfo is None or point.utcoffset() is None:
        raise FactValidationError(f"{field} must include a timezone")
    return point.astimezone(UTC).isoformat(timespec=timespec).replace("+00:00", "Z")


def adapt_legacy_source_ref(value: Mapping[str, object]) -> dict[str, Any]:
    """Convert a flattened ``SourceRef.to_dict()`` value to a ledger source.

    Legacy ``SourceRef`` stores arbitrary provenance alongside its known fields.
    The fact ledger keeps those fields in its explicit ``extra`` object instead.
    This adapter is intentionally structural: it neither reads legacy files nor
    depends on their storage implementation.
    """

    if not isinstance(value, Mapping):
        raise FactValidationError("legacy source must be an object")
    if "extra" in value:
        raise FactValidationError("legacy source extra collides with canonical extra field")
    if not set(value) >= _REQUIRED_FIELDS:
        raise FactValidationError("legacy source must contain kind and ref")

    result = {key: value[key] for key in _KNOWN_FIELDS & set(value)}
    if result.get("captured_at") is not None:
        result["captured_at"] = _utc_timestamp(result["captured_at"], "source.captured_at")
    precision = result.get("time_precision")
    if error := _source_precision_error(result.get("occurred_at"), precision):
        raise FactValidationError(error)
    if result.get("occurred_at") is not None and precision != "day":
        timespec = {
            "minute": "seconds",
            "second": "seconds",
            "millisecond": "milliseconds",
        }.get(precision, "microseconds")
        result["occurred_at"] = _utc_timestamp(
            result["occurred_at"],
            "source.occurred_at",
            timespec=timespec,
        )
    elif result.get("occurred_at") is not None:
        if not isinstance(result["occurred_at"], str):
            raise FactValidationError("source.occurred_at must be an ISO date")
        try:
            date.fromisoformat(result["occurred_at"])
        except ValueError as exc:
            raise FactValidationError("invalid source.occurred_at") from exc

    extra: dict[str, Any] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise FactValidationError("legacy source fields must be strings")
        if key not in _KNOWN_FIELDS:
            extra[key] = _json_safe(item, f"source.{key}")
    if extra:
        result["extra"] = extra
    return _source(result)
