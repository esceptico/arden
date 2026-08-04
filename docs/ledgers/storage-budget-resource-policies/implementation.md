# Implementation

> A checked item means implemented, not verified.

## Intended outcome

Storage settings show an additive, understandable resource breakdown and can plan/enforce a limit below current usage without silently deleting protected history. Cleanup is deterministic, previewed, progressively authorized, restart-safe, and measured in actual filesystem bytes.

## Checklist

- [x] **I-01 — Introduce a typed resource inventory**
  - Outcome: Return additive categories with `id`, label, total/reclaimable bytes, item count, policy tier, protection reason, description key, and measurement kind (`physical` or `logical_estimate`). Category totals reconcile exactly to Arden's root total.
  - Scope: `storage_budget.py`, API schemas/types, resource-owner adapters; avoid path heuristics outside a central registry.
  - Required verification: Fixture filesystem and SQLite snapshots cover every top-level Arden-owned byte, symlinks are excluded, live SQLite sidecars are classified safely, and category/total arithmetic is exact.
- [x] **I-02 — Build the resource breakdown UI and explanations**
  - Outcome: Settings shows size-sorted categories, reclaimable amounts, protected reasons, and concise focus/hover tooltips; replace the ambiguous aggregate “protected” sentence.
  - Scope: `ArchiveTab.tsx`, shared types, existing `Tooltip`/`IconButton`, loading/error/empty states.
  - Required verification: Component tests for keyboard/focus tooltip access, byte formatting, ordering, long labels, blocked reasons, and API error states.
- [x] **I-03 — Define durable cleanup policy and protections**
  - Outcome: Persist enabled tiers and visible defaults: backup/log archives expire after 14 days with manual Keep; current chats remain unlimited unless the interactive aggressive tier is enabled, then require ≥90 days inactivity and retain ≥100 newest. Current/open, pinned, active-run, unfinished-goal, and automation sessions are ineligible.
  - Scope: config/schema migration and session eligibility service. Keep pins desktop-owned for now; destructive current-chat plans must be interactive and include the current/pinned set.
  - Required verification: Restart persistence and table-driven eligibility tests for every protected state and combination.
- [x] **I-04 — Add a dry-run cleanup planner**
  - Outcome: Any valid limit produces a stable plan: before/target/estimated-after bytes, ordered actions, counts, destructive tier, blockers, and a plan revision/hash. No mutation occurs during planning.
  - Scope: new planner/service/API plus desktop preview and tier-specific consent.
  - Required verification: Deterministic plans, exact ordering, stale-plan rejection, concurrent-growth handling, unattainable-limit explanation, and no writes in dry-run tests.
- [x] **I-05 — Implement safe and backup-archive executors**
  - Outcome: Tier 0 reuses reference-safe blob cleanup; tier 1 deletes only allowlisted backup artifacts inside the archive root while preserving the configured recovery floor.
  - Scope: storage owner adapters, anchored path operations, audit receipts, cancellation/idempotency.
  - Required verification: Traversal/symlink/race tests, newest/pinned backup protection, partial-failure retry, exact reclaimed-byte accounting, and no deletion outside allowlisted roots.
- [x] **I-06 — Repair permanent session deletion**
  - Outcome: One transaction removes an eligible archived session from all 22 session-owned tables/projections and records blob cleanup work; external blob deletion is retryable and idempotent.
  - Scope: session store/service, ownership registry, search/event/outbox/tool-result cleanup; preserve unrelated shared content-addressed blobs.
  - Required verification: Seed every session-keyed table, delete one session, prove zero owned rows/refs remain, shared blobs survive, rollback on transaction failure, and existing orphan reconciliation is explicit.
- [x] **I-07 — Execute archived-chat cleanup**
  - Outcome: Cold-convert oldest eligible archived chats to a full-fidelity compressed bundle with a lightweight searchable metadata stub; permanent deletion remains a separately consented follow-on tier.
  - Scope: planner executor, paginated candidate inventory, canonical export/import, atomic hot-to-cold state transition, rehydration on open, and audit receipts.
  - Required verification: Deterministic export, full restore parity, hashes/blob integrity, interrupted conversion rollback, ordering/protection floors, search/list behavior, rehydration UX, and physical-byte reconciliation.
- [x] **I-08 — Execute inactive-current-chat cleanup**
  - Outcome: With separate explicit opt-in, delete only chats inactive for at least 90 days while retaining at least 100 newest and enforcing every protection.
  - Scope: interactive desktop plan supplies current/pinned IDs; server revalidates run, goal, automation, recency, and minimum-count protection immediately before mutation.
  - Required verification: Race tests where a candidate becomes active/pinned/current after planning; executor must revalidate and skip it.
- [x] **I-09 — Reclaim physical SQLite space**
  - Outcome: Migrate existing databases once from `auto_vacuum=NONE` to `INCREMENTAL` through the verified offline compactor; new databases start incremental. Thereafter reclaim bounded page batches during idle time, with full `VACUUM INTO` reserved for exceptional fragmentation.
  - Scope: schema/version migration, existing offline compactor, bounded idle maintenance, freelist/fragmentation telemetry; never block API readiness unpredictably.
  - Required verification: One-time migration parity, actual incremental file shrink, batch/cancellation bounds, low-disk failure, concurrent-write safety, restart behavior, and exceptional full-compaction rollback.
- [x] **I-10 — Add observability and end-to-end quota proof**
  - Outcome: Expose last plan/run, bytes reclaimed by category, skipped protections, errors, next retry, and whether the configured limit is currently attainable.
  - Scope: health/ops API, Settings status, structured logs, durable audit receipts.
  - Required verification: End-to-end fixture starts above a below-current limit, executes enabled tiers in order, physically reaches the target or truthfully stops at the protected floor, and remains correct after restart.

## Notes

- The executor follows the tier order and revalidates every planned item. Current-chat execution receives the desktop's latest current/pinned set, so changed protections stale the plan.
- Cold bundles preserve canonical database rows plus referenced tool-result blobs, stream large blobs, verify hashes/prose/counts, and restore into the current schema.
- Existing `auto_vacuum=NONE` databases still require the one-time verified offline compactor; runtime status states this explicitly instead of pretending row deletion immediately shrank the file.
