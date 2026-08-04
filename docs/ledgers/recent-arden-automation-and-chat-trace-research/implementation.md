# Implementation

> Checked boxes mean implemented. Verification is recorded separately.

## Intended outcome

Make terminal state, failure cause, retry policy, and maintenance conflicts consistent and inspectable without replacing the existing automation framework.

## Checklist

- [ ] **I-01 — Durable parent↔child cascade settlement (P0; partially implemented)**
  - [x] Persist stored-edge descendant cancellation with actor, cause, generation, and idempotency key.
  - [x] Persist an execution cancellation scope so restart reconciliation preserves the cancellation cause.
  - [x] Atomically settle awaited child completion, its terminal event, and the parent suspension.
  - [x] Reject late descendant registration under an existing cancellation scope before child execution begins.
  - [x] On restart, settle `cancel_requested` child rows and their awaited parent suspensions instead of respawning them.
  - [x] Emit `RUN_ERROR` rather than `RUN_FINISHED` for failed child sessions.
  - [ ] Extend the causal contract beyond current user/agent cancellation to timeout, supersede, automation, and shutdown; add crash-point tests for every direction.
  - Reuse `background_agent_runs`, suspensions, completion IDs, and the outbox as the durable spawn edge/event substrate.
  - Parent→child: persist cancel/timeout/shutdown/supersede with actor, cause, generation, and idempotency key; traverse descendants from stored edges, not only `RunRegistry.cancel_subtree()` memory.
  - Child→parent: transactionally settle child state, every open child invocation, the waiting parent tool/suspension, and one completion event.
  - Preserve `agent_timeout`, `user_cancelled`, `superseded`, or `shutdown`; never relabel an already-terminal owner as `server_restart`.
  - Emit a failed terminal event for failed child sessions instead of generic completed `RUN_FINISHED`.
  - Proof: crash/restart during each direction resumes idempotently; timeout and cancel assert identical cause across all tables/events and zero open invocations.

- [x] **I-02 — Typed Wiki Maintenance rejection (P1)**
  - Convert ambiguity, page validation, and generated-region conflicts to typed, deterministic tool results.
  - Suppress/skip the current prepared report or terminate visibly; do not invite opaque retries.
  - Proof: each conflict makes one attempt, exposes exact recovery, preserves generated content, and settles the builtin explicitly.

- [ ] **I-03 — Retry taxonomy and correlated incidents (P1)**
  - Separate `transient`, `expected_ordering`, `repair_required`, and `permanent_validation` failures.
  - Log `DetachedRunBindingPending` at debug/metric level while retaining durable retry.
  - Gate schema repair once, then suppress fleet retries until the invariant passes.
  - Persist error class, attempt, next retry, correlation ID, and last durable child event.

- [ ] **I-04 — Causal cancellation contract (P1)**
  - Add actor/source/reason to cancel request, terminal event, `chat_runs`, and automation settlement.
  - Proof: user Stop, supersede, automation, timeout, and shutdown remain distinguishable after restart.

- [ ] **I-05 — Honest outbox health (P2)**
  - Report recent dead separately from historical unacknowledged dead.
  - Add explicit acknowledge/archive/purge lifecycle; never auto-replay incompatible history.

- [ ] **I-06 — One durable compaction recording path (deferred by decision)**
  - Record manual, automatic, forced-retry, loop, and child compactions through one helper.
  - Include before/after messages, token watermark, duration, trigger, run, and rehydration state.
  - Proof: the `chat_compactions` table matches emitted compaction events.

- [x] **I-07 — Path-evidence and miss recovery (P1)**
  - Record paths returned by file list/find/search in the existing resource-observation ledger; user-provided paths start observed.
  - Record successful file writes/edits as exact discovery evidence so immediate follow-up reads are allowed.
  - Return resolved path, nearest existing ancestor, and bounded candidates on `not_found`.
  - Preserve permission and inspection failures as typed outcomes during candidate enrichment.
  - After two misses under one root, return `discovery_required` until the model lists/finds from an observed ancestor.
  - Proof: speculative unobserved descendants cannot fan out indefinitely; valid direct/user-supplied paths still work.

- [x] **I-08 — Typed behavioral budgets and step fan-out (P0; adopted)**
  - Add `StopReason.NO_PROGRESS` from structured tool outcomes; stop repeated non-retryable failure-only steps.
  - Add `max_tool_calls_per_step=10`, distinct from the existing cumulative `max_tool_calls`; reject an oversized batch before executing any call and preserve one typed result per call ID.
  - Add executor concurrency `6`, external-provider keys, explicit internal resource groups, and conflict serialization; acceptance is not concurrency.
  - Retain cumulative call/step/time/token/cost budgets for unattended automations.
  - Proof: recent valid 1–9 call batches continue; 11-call batches perform no mutations and return 11 protocol-valid results; three non-retryable failure-only steps stop.

- [x] **I-09 — Direct multi-tool preload (P1; adopted)**
  - Add `names: list[str]` to the model-facing `tool_search` schema, bounded to 10–25 exact names, and document multi-preload in the native prompt.
  - Reuse current run-local `loaded_tools`; do not persist or version a preloaded toolset.
  - Return per-name `loaded/already_loaded/unknown/not_allowed` status in one result.
  - Proof: Email updates can preload its four stable tools in one call; all 212 historical single-select forms remain compatible.

- [ ] **I-09b — Model/context amplification and delta-first gating (deferred; not solved)**
  - Preserve the measured problem and prior proposal, but do not change area cadence, context construction, or progress watermarks in this scope.

- [x] **I-10 — Three-layer search budget and provider-aware concurrency (P1)**
  - Stream/parse `rg`; enforce hard result, per-line byte, total serialized-byte, timeout, and cancellation bounds before returning from the desktop producer.
  - Bound query/path/glob inputs and apply the serialized response cap to success, empty, single-match, and typed-failure branches.
  - Add `content|files_only|count`, cursor/offset, hard maxima, and structured `has_more/next_cursor/limit_reason`.
  - Avoid duplicating full snippet text in both `content` and structured `data`.
  - Add global and per-provider concurrency keys to the tool runner.
  - Proof: a 52 MB single line never crosses the device boundary; paging is deterministic; concurrency never exceeds configured `N`.

- [ ] **I-11 — Honest foreground/child performance accounting (P1)**
  - Give child model/tool activity its own run identity; expose parent aggregate as a separate roll-up.
  - Record foreground, child, tool, model, and compaction durations independently.
  - Proof: a parent can end while child activity continues without extending or mutating the parent run's own metrics.

- [ ] **I-12 — Explicit read cacheability and freshness (P2)**
  - Add registered cacheability/freshness metadata; coalesce concurrent identical reads and invalidate on declared writes.
  - Do not infer safety from the generic `READ` action.

- [x] **I-13 — Raw-result retention policy (P2)**
  - Do not persist unlimited raw search output by default; a bounded partial result is valid.
  - Assign short TTL/quota to explicitly requested overflow artifacts; promote only durable evidence.
  - Proof: expired referenced manifests become reclaimable under policy without deleting pinned evidence.

- [ ] **I-14 — Search-use prompt contract (P2; partially implemented)**
  - [x] Direct discovery through `files_only`/`count`, paged `content`, `next_cursor`, then `file_read`.
  - [ ] Add epoch-aware exact-page repeat suppression after a measured need; current path evidence and no-progress guards are the first-line controls.
  - Instruct agents to use `files_only`/`count` for discovery, narrow path/glob, page content, then `file_read` exact windows.
  - Warn/cache/block exact same-page repeats unless an intervening write invalidated the search epoch.

## Explicit non-goals

- Do not replace the builtin maintenance phases.
- Do not weaken wiki generated-region or destructive-command safety.
- Do not treat high persisted message count alone as a context failure; current compaction works and the model has sufficient context.
- Do not blanket-dedupe all reads or serialize all tool execution; freshness and provider concurrency are explicit contracts.
- Do not add a persistent/versioned preloaded toolset.
- Do not change compaction or model/context amplification in this scope; both remain explicit deferred gaps.

## Implemented files

- Desktop producer: `apps/desktop/electron/executor-tools.cjs`.
- Agent loop and scheduler: `apps/server/arden/agent/{agent.py,tools/runner.py,tools/dispatch.py}`.
- Deferred tools: `apps/server/arden/tools/deferred.py`, `apps/server/arden/core/prompts.py`.
- Durable cascade: `apps/server/arden/context/store.py`, `apps/server/arden/execution/{store.py,gateway.py}`, child session/router paths.
- Tool conflict domains: `apps/server/arden/tools/core/types.py`, `apps/server/arden/core/tool_executor.py`, and explicit filesystem/research/workflow policies.
- Maintenance failures: `apps/server/arden/wiki/maintenance/agent.py`, `apps/server/arden/tools/wiki_maintenance.py`.

## Notes

- Implementation is present in the uncommitted working tree based on revision `88570d0c`.
- Unchecked items are intentionally deferred or only partially implemented; they are not claimed complete.
