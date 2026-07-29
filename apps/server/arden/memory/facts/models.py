"""Immutable public values for the canonical fact ledger."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any


class FactLedgerError(RuntimeError):
    """Base error for fact-ledger failures."""


class FactLedgerCorruptionError(FactLedgerError):
    """Stored ledger data or its materialized files are invalid."""


class FactConflictError(FactLedgerError):
    """A planned fact dependency changed before publication."""


class FactValidationError(FactLedgerError, ValueError):
    """A requested change does not meet the strict schema."""


@dataclass(frozen=True)
class Fact:
    fact_id: str
    text: str
    normalized_text: str
    kind: str
    labels: tuple[str, ...]
    subjects: tuple[str, ...]
    scope: Mapping[str, str | None]
    lifecycle: str
    status: str
    certainty: str
    evidence_class: str
    sources: tuple[Mapping[str, Any], ...]
    created_at: datetime
    reviewed_at: datetime | None
    review_at: datetime | None
    review_basis: str | None
    expires_at: datetime | None
    successor_id: str | None
    version: str

    @property
    def fact_version(self) -> str:
        """Hash of the complete immutable event chain."""
        return self.version


@dataclass(frozen=True)
class FactReadSnapshot:
    """One immutable current fact view and its canonical access scopes."""

    facts: Mapping[str, Fact]
    known_scopes: frozenset[tuple[str, str | None]]


@dataclass(frozen=True)
class FactEvent:
    event_id: str
    plan_id: str
    fact_id: str
    op: str
    occurred_at: datetime
    actor: str
    origin: str
    plan_reason: str
    record: Mapping[str, Any]


@dataclass(frozen=True)
class FactPlan:
    plan_id: str
    events: tuple[FactEvent, ...]
    dependencies: Mapping[str, str]
    duplicate_windows: Mapping[str, str]


@dataclass(frozen=True)
class FactChangeFeed:
    """One pinned delta whose routes cover every before/after event state."""

    from_revision: str | None
    through_revision: str | None
    events: tuple[FactEvent, ...]
    affected_fact_ids: tuple[str, ...]
    affected_routes: tuple[tuple[str, str | None, str], ...]


@dataclass(frozen=True)
class DueReviewCandidate:
    fact: Fact
    due_at: datetime
    related_facts: tuple[Fact, ...]
    review_window: str
