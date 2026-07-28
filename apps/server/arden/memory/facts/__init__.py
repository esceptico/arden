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
from .plan_store import (
    FactPlanCorruptionError,
    FactPlanOwnershipError,
    FactPlanRequestConflictError,
    FactPlanStatus,
    FactPlanStore,
)
from .service import DueFactReview, FactCommitResult, FactPlanPreview, FactPrincipal, FactScopeError, FactService

__all__ = [
    "DueReviewCandidate",
    "DueFactReview",
    "Fact",
    "FactConflictError",
    "FactEvent",
    "FactLedger",
    "FactLedgerCorruptionError",
    "FactLedgerError",
    "FactPlan",
    "FactPlanCorruptionError",
    "FactPlanOwnershipError",
    "FactPlanPreview",
    "FactPlanRequestConflictError",
    "FactPlanStatus",
    "FactPlanStore",
    "FactPrincipal",
    "FactScopeError",
    "FactService",
    "FactCommitResult",
    "FactValidationError",
    "adapt_legacy_source_ref",
]
