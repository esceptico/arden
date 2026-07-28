"""Canonical append-only facts; intentionally independent from wiki projections."""

from .consumer_store import FactConsumerStore, FactConsumerWatermark, FactConsumerWatermarkConflictError
from .cutover import (
    LEDGER_DIRECTORY,
    MARKER_NAME,
    FactCutover,
    FactCutoverError,
    fact_cutover_content,
    load_fact_cutover,
)
from .ledger import FactLedger
from .legacy import adapt_legacy_source_ref
from .models import (
    DueReviewCandidate,
    Fact,
    FactChangeFeed,
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
from .service import (
    DueFactReview,
    FactCommitResult,
    FactPage,
    FactPlanPreview,
    FactPrincipal,
    FactScopeError,
    FactService,
    RetentionReviewBatch,
)
from .synthesis import FactSynthesis, FactSynthesisError, FactSynthesisRenderer, FactSynthesisResult, SynthesisFact

__all__ = [
    "DueReviewCandidate",
    "DueFactReview",
    "Fact",
    "FactChangeFeed",
    "FactConsumerStore",
    "FactConsumerWatermark",
    "FactConsumerWatermarkConflictError",
    "FactCutover",
    "FactCutoverError",
    "FactConflictError",
    "FactEvent",
    "FactLedger",
    "FactLedgerCorruptionError",
    "FactLedgerError",
    "FactPage",
    "FactPlan",
    "FactPlanCorruptionError",
    "FactPlanOwnershipError",
    "FactPlanPreview",
    "FactPlanRequestConflictError",
    "FactPlanStatus",
    "FactPlanStore",
    "FactPrincipal",
    "RetentionReviewBatch",
    "SynthesisFact",
    "FactScopeError",
    "FactService",
    "FactCommitResult",
    "FactSynthesis",
    "FactSynthesisError",
    "FactSynthesisRenderer",
    "FactSynthesisResult",
    "FactValidationError",
    "LEDGER_DIRECTORY",
    "MARKER_NAME",
    "adapt_legacy_source_ref",
    "fact_cutover_content",
    "load_fact_cutover",
]
