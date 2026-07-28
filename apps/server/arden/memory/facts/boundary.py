"""Facts-local scope and timestamp normalization for fact tools."""

import re
from datetime import date, datetime
from typing import Any, Literal

from arden.memory.facts.service import FactScope

FactTimePrecision = Literal["millisecond", "second", "minute", "day", "unknown"]
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?P<fraction>\.\d+)?(?:Z|[+-]\d{2}:\d{2})$")


def fact_read_scopes(area: Any | None) -> frozenset[FactScope]:
    """Return the server-derived visibility set for canonical facts."""

    scopes: set[FactScope] = {("global", None), ("user", None)}
    if area is not None:
        scopes.add(("area", area.area_id))
    return frozenset(scopes)


def fact_write_scope(*, kind: str, area: Any | None) -> FactScope:
    """Return the one server-derived scope allowed for a fact create."""

    if kind == "directive":
        return "global", None
    if area is not None:
        return "area", area.area_id
    return "user", None


def source_time(value: object) -> tuple[str | None, FactTimePrecision]:
    """Normalize a source timestamp without claiming false precision."""

    if not isinstance(value, str):
        return None, "unknown"
    if _DATE_RE.fullmatch(value):
        try:
            date.fromisoformat(value)
        except ValueError:
            return None, "unknown"
        return value, "day"
    match = _TIMESTAMP_RE.fullmatch(value)
    if match is None:
        return None, "unknown"
    try:
        point = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None, "unknown"
    if match["fraction"] is None:
        return point.isoformat(timespec="seconds"), "second"
    return point.isoformat(timespec="milliseconds"), "millisecond"
