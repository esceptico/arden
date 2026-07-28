"""Canonical append-only facts; intentionally independent from wiki projections."""

from .ledger import FactLedger
from .legacy import adapt_legacy_source_ref
from .models import (
    DueReviewCandidate,
    Fact,
    FactConflictError,
    FactEvent,
    FactLedgerCorruptionError,
    FactLedgerError,
    FactPlan,
    FactValidationError,
)

__all__ = [
    "DueReviewCandidate",
    "Fact",
    "FactConflictError",
    "FactEvent",
    "FactLedger",
    "FactLedgerCorruptionError",
    "FactLedgerError",
    "FactPlan",
    "FactValidationError",
    "adapt_legacy_source_ref",
]
