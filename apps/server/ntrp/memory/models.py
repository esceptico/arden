"""Memory models: scoped artifacts in one flat pool.

Scopes are default visibility metadata, not a graph hierarchy. Records are
atomic artifacts with sparse metadata and optional source evidence.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal

TimePrecision = Literal["millisecond", "second", "minute", "day", "unknown"]


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class SourceRef:
    kind: str
    ref: str
    captured_at: str | None = field(default_factory=now_iso)
    scope_kind: str | None = None
    scope_key: str | None = None
    occurred_at: str | None = None
    time_precision: TimePrecision = "unknown"
    role: str | None = None
    excerpt_hash: str | None = None
    extra: Mapping[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict:
        data = dict(self.extra)
        data.update(
            {
                "kind": self.kind,
                "ref": self.ref,
                "scope_kind": self.scope_kind,
                "scope_key": self.scope_key,
                "occurred_at": self.occurred_at,
                "time_precision": self.time_precision,
                "role": self.role,
                "excerpt_hash": self.excerpt_hash,
            }
        )
        if self.captured_at is not None:
            data["captured_at"] = self.captured_at
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "SourceRef":
        known = {
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
        return cls(
            kind=str(data.get("kind") or "unknown"),
            ref=str(data.get("ref") or ""),
            captured_at=(str(data["captured_at"]) if data.get("captured_at") is not None else None),
            scope_kind=data.get("scope_kind"),
            scope_key=data.get("scope_key"),
            occurred_at=data.get("occurred_at"),
            time_precision=data.get("time_precision") or "unknown",
            role=data.get("role"),
            excerpt_hash=data.get("excerpt_hash"),
            extra={key: value for key, value in data.items() if key not in known},
        )


class Kind(StrEnum):
    """Small v1 memory function types.

    Keep this intentionally boring. Preferences are facts about the user;
    project facts are facts with project scope; procedures that should steer
    behavior are directives. Free-form junk-drawer `note` records are avoided
    for new writes.
    """

    DIRECTIVE = "directive"
    FACT = "fact"
    SOURCE = "source"
    CHANGELOG = "changelog"
    LESSON = "lesson"  # continual-learning playbook item — a working-pattern the agent DISTILLED (vs directive = user-stated)


# Source-trust precedence: a direct user statement outranks a curator-compiled
# fact, which outranks a passively-ingested integration fact, which outranks a
# machine-authored dream insight. Used by synthesis (phrasing/exclusion) so
# low-trust sources aren't laundered into user-confidence claims.
TRUST_LEVEL: dict[str, int] = {"user": 4, "curator": 3, "chat_turn": 3, "dreamer": 1}
TRUST_DEFAULT = 2  # integration:* and unknown


def source_trust(kind: str) -> int:
    return TRUST_LEVEL.get((kind or "").split(":")[0].lower(), TRUST_DEFAULT)


@dataclass
class Record:
    id: str
    text: str
    kind: str = Kind.FACT
    scope_kind: str | None = None
    scope_key: str | None = None
    created_at: str = field(default_factory=now_iso)
    last_confirmed_at: str = field(default_factory=now_iso)
    superseded_by: str | None = None
    pinned: bool = False
    source_ref: SourceRef | None = None
    imp: int | None = None  # 1-10 poignancy from the scorer; None = unscored (neutral 5)
    sources: tuple[SourceRef, ...] = ()

    def __post_init__(self) -> None:
        """Keep the singular provenance field as a first-source compatibility view."""
        if self.sources:
            self.sources = tuple(self.sources)
            self.source_ref = self.sources[0]
        elif self.source_ref is not None:
            self.sources = (self.source_ref,)
