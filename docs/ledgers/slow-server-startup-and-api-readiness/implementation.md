# Implementation

> A checked item means implemented, not verified.

## Intended outcome

Core API availability is independent of migrations, indexing, wiki health, interrupted-run recovery, and due automations. Warmup remains durable, observable, cancellable, and retryable.

## Checklist

- [x] **I-01 — Add startup phase observability**
  - Outcome: Structured elapsed time and outcome for stores/schema, wiki/facts, index sync, health, skills, MCP/tools, recovery, scheduler seed, and post-yield warmup.
  - Scope: `server/app.py`, runtime connect methods, tests.
  - Required verification: One cold-start trace accounts for the full wall time; no unlabelled gap over 1s.
- [x] **I-02 — Split core readiness from warmup**
  - Outcome: Initialize only the minimum request-safe runtime before lifespan `yield`; run pruning, title migration, curator reconciliation, index/health projection, completion redelivery, respawns, and interrupted-chat resume in supervised background work.
  - Scope: `server/app.py`, `server/runtime/core.py`; explicit runtime phase/state model.
  - Required verification: Artificially block every warmup phase and prove `/health` still responds within the readiness SLO; shutdown cancels/settles workers cleanly.
- [x] **I-03 — Remove scheduler/projection startup race**
  - Outcome: Reconcile interrupted runs first in background, durably enqueue/coalesce initial wiki projection, then enable catch-up automations. Never await projection before API readiness.
  - Scope: automation startup orchestration and existing dedicated projection outbox.
  - Required verification: A due wiki-writing automation cannot delay `/health`; projection converges after concurrent writes without repeated conflict storms.
- [x] **I-04 — Expose readiness versus warmup**
  - Outcome: `/health` reports `live`, warmup phase/progress/error, and capability readiness; dependent endpoints use cached/degraded behavior or typed `503 warming_up`.
  - Scope: health/runtime-info API and desktop bootstrap handling.
  - Required verification: Desktop becomes usable while projection/index recovery continues and clearly shows degraded capabilities.
- [x] **I-05 — Make unchanged-schema startup O(1)**
  - Outcome: Version migrations, add status/partial indexes for startup recovery queries, and replace full wiki-curator history replay with a durable watermark.
  - Scope: session schema migrations, curator queue reconciliation.
  - Required verification: Query plans use indexes; unchanged startup performs no table rewrite/full-history enqueue.
- [x] **I-06 — Make outbox completion lightweight**
  - Outcome: In the same transaction that marks delivery complete, replace the full payload with a compact receipt/hash. Keep full payloads for pending/running/retry/dead rows; prune completed receipts after a short configurable grace (proposed: 24h).
  - Scope: outbox store/schema/constants and consumers; no transcript-history responsibility.
  - Required verification: Crash/retry/idempotency tests; completed payload bytes approach zero; failed/dead replay remains lossless.
- [ ] **I-07 — Bound new transcript/event writes**
  - Outcome: Reconcile event retention from database state, not an in-memory counter. Persist bounded tool-result previews; store large raw bodies/media once as content-addressed artifacts with references and independent expiry.
  - Scope: session event retention, message/tool-result writers, artifact lifecycle, search projections.
  - Required verification: Per-session cap after restart; payload/media size tests; references remain resolvable for their declared lifetime; no duplicate indexing of raw tool bodies.
  - Partial: durable database-backed event pruning and existing content-addressed raw-result refs are covered. Independent blob expiry/reference GC remains before this item can close.
- [x] **I-08 — Add one Arden storage budget**
  - Outcome: A user-facing `max_space_gb` controls managed `~/.arden` growth. Background maintenance reports total/reclaimable/protected bytes, cleans to 85% of the cap, protects active/pinned sessions and explicit backups, and emits `quota_blocked` instead of silently deleting protected history.
  - Scope: configuration/settings UI, storage inventory, supervised maintenance worker, health/status API.
  - Required verification: Deterministic fake-filesystem quota tests; concurrent writes remain safe; maintenance never blocks readiness; no cleanup outside allowlisted Arden-owned paths.
- [ ] **I-09 — Pilot cold transcript bundles**
  - Outcome: Export representative archived sessions side-by-side with canonical rows as ATIF-v1.7 JSON+zstd and Letta-v1 JSON+zstd. Preserve user/assistant prose; compare full versus summarized tool calls; keep raw results as existing blob refs; record hashes, omissions, counts, and source revision in a sidecar manifest.
  - Scope: reproducible derived exporter/benchmark only. Archive deletion/replacement is a separate later decision because no reviewed harness uses trajectories as its authoritative store.
  - Required verification: Upstream schema validation, deterministic re-export, byte/token/search comparison, message-order parity, documented reconstruction limits, and blob-reference integrity. Canonical rows remain untouched.
- [ ] **I-10 — Explicit offline legacy migration and compaction**
  - Outcome: Losslessly move legacy inline tool results to blobs, replace event bodies with references, enforce the event cap, remove the stray FTS column, then compact via verified `VACUUM INTO` and atomic swap.
  - Scope: dedicated maintenance CLI/script; never silent server boot.
  - Required verification: preflight disk space, manifest/hash counts, SQLite integrity, row/reference parity, application smoke tests, rollback artifact.

## Notes

- Implemented on `codex/slow-startup-readiness`; verification evidence is in `verification.md`.
- I-07 remains partial; I-09 and I-10 remain intentionally separate from the online boot path.
- Do not run live `VACUUM` now: only 13 GiB is free and the active database is 8 GiB.
