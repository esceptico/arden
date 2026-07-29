# Plan 006: Finish the promised core ownership splits

## Outcome

Split the four explicitly identified files above 1,000 lines along real
ownership boundaries while preserving public imports, behavior, transaction
semantics, and error types.

Current sizes:

- `revisions/repository.py`: 1,150
- `memory/facts/ledger.py`: 1,815
- `wiki/service.py`: 1,801
- `automation/scheduler.py`: 1,162

Do this after plans 001-005 so their regression contracts are fixed first.
Use one commit per file split.

## Rules

- No mixins, compatibility shims, generic utility module, duplicated registry,
  relative import, `TYPE_CHECKING`, postponed annotations, or reflection.
- Do not move code merely to satisfy a line count.
- Dependencies point toward models/codecs/storage; runtime remains the
  composition root.
- Keep current public class names and import paths stable.
- Prefer direct pure functions and explicit value objects over one-method
  wrapper classes.
- Preserve every CAS, recovery, idempotency, cancellation, and error boundary.

## Split 1: revision repository

Keep in `revisions/repository.py`:

- `ManagedFileRepository`;
- commit, recovery, CAS, tree/materialization orchestration;
- public read/write facade.

Extract:

- `revisions/query.py`: history traversal and diff pagination/pure diff
  rendering;
- `revisions/maintenance.py`: integrity, storage reporting, reachable-root
  calculation, and collection scanning.

Pass explicit storage/snapshot values to extracted functions. Do not expose
private filesystem handles through a new public API.

Proof: all `test_revisions.py` behavior, import compatibility, exact diff
pagination, integrity, and collection reports.

## Split 2: fact ledger

Keep in `memory/facts/ledger.py`:

- `FactLedger`;
- pinned snapshots and repository bridge;
- public query/plan/commit orchestration.

Extract:

- `memory/facts/event_codec.py`: JSONL decode/encode, field validation,
  timestamps, source/scope parsing;
- `memory/facts/state.py`: deterministic event replay, lifecycle transitions,
  successor graph, duplicate/window, Retention, and Maintenance invariants.

Use explicit function arguments/results. Do not create a second ledger object or
move I/O into the codec/state modules.

Proof: fact-ledger, change-feed, plan, Retention, Maintenance, and service tests;
codec round trip; replay and rejection parity.

## Split 3: wiki service

Keep in `wiki/service.py`:

- `WikiService` public facade;
- ordinary page lifecycle and generated/health publication orchestration;
- public exception imports.

Extract:

- `wiki/snapshots.py`: snapshot indexing, name/redirect resolution, link
  resolution, and prospective validation;
- `wiki/changes.py`: history/feed/detail evidence, provenance parsing, warning
  projection, and the plan-005 Maintenance restoration helper;
- `wiki/rename.py`: rename planning, parsed-link rewrite construction, and
  redirect identity.

Do not weaken snapshot pinning or create a second cache/index truth.

Proof: wiki operations, changes, generated publication, Maintenance, context,
approvals, links, health, and canonical router suites.

## Split 4: automation scheduler

Keep in `automation/scheduler.py`:

- `Scheduler` and its public value/error types;
- lifecycle, wake/tick coordination, task/session reservation, and public
  methods.

Extract:

- `automation/event_dispatch.py`: event matching, durable queue delivery,
  retry/dead-letter timing;
- `automation/run_execution.py`: handler/agent/session execution and terminal
  settlement.

The scheduler remains the sole owner of live task tracking and reservations.
Extracted functions/components receive explicit typed dependencies and return
typed outcomes; they do not reach back through `getattr` or mutate scheduler
internals indirectly.

Proof: catch-up, event queue, session deferral, idempotency, cancellation,
settlement/outbox ordering, handler, agent, and status suites.

## Architecture guard

Update `apps/server/tests/test_memory_architecture.py` to:

- keep the no-cycle, absolute-import, eager-annotation, and forbidden-reflection
  checks;
- assert the four named facade files remain below 1,000 physical lines;
- assert extracted modules do not import runtime/router/tool layers;
- smoke-import the stable public classes and exceptions from their existing
  paths.

Update `docs/architecture/wiki-memory-dependencies.md` with the actual module
ownership and mark step 4 complete. Append the same evidence to the maintenance
ledger.

## Acceptance

- Each split is behavior-only and independently reviewable.
- All four facade files are below 1,000 lines for meaningful ownership reasons.
- Public imports and runtime behavior are unchanged.
- Full server/Desktop tests and runtime smoke pass after the final split.

