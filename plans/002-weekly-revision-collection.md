# Plan 002: Run managed-history collection weekly

## Outcome

Wire the existing reachability collector into production for both canonical
fact and wiki repositories, on a weekly schedule with the existing 30-day
grace period.

## Current state

- `ManagedFileRepository.collect()` validates unreachable objects and removes
  only objects outside its configured grace period.
- Revision tests cover reachability, recovery protection, grace, and
  corruption.
- No production code calls `collect()`.
- The ledger makes collection mandatory before enabling the history layer.

## Implementation

### Domain boundary

Add a meaningful `FactLedger.collect_history()` method returning the existing
`CollectionReport`; it delegates to its private managed repository without
exposing `_repository`.

`WikiService.repository.collect()` remains the wiki call. Do not add a second
wrapper only for symmetry.

### Scheduler wiring

Reuse the proven scheduler rather than adding a second timer loop:

1. Add a non-LLM builtin ID in `arden/constants.py`.
2. Add `Memory Storage Maintenance` to
   `apps/server/arden/automation/builtins.py` with
   `TimeTrigger(every="7d")` and an internal handler.
3. Seed it only when canonical facts/wiki are enabled.
4. Keep it outside `_HEALTH_PHASES`; it is housekeeping, not a fifth required
   semantic maintenance phase.
5. Inject one `collect_managed_history` callback from `Runtime` into
   `AutomationRuntime`.
6. Register the handler in `AutomationRuntime.start_scheduler()`.
7. Add its exact ID/handler and seven-day backstop to the scheduler's explicit
   missed-run catch-up policy. Registration alone is insufficient because
   `_should_catch_up_missed()` otherwise advances an overdue interval.
8. Run fact and wiki collection through `asyncio.to_thread`; return scanned,
   removed, retained, and bytes-removed counts for both repositories.

Use each repository's configured default grace. Do not pass a shorter grace,
delete reachable history, or make collection depend on Retention, Synthesis,
Wiki Maintenance, or an LLM.

Errors from corruption or I/O must fail the builtin run and use normal
scheduler retry/history. Do not log-and-continue or advance a separate
checkpoint after a partial failure. Re-running a completed collection is safe.

## Tests

Extend:

- `test_revisions.py`: retain existing collection contract unchanged;
- `test_fact_ledger.py`: public fact-history collection delegates correctly;
- `test_automation_store.py`: builtin identity, weekly cadence, non-LLM handler,
  and seeding only in canonical-memory mode;
- `test_fact_runtime.py`: handler invokes both repositories, returns both
  reports, and propagates one-side failure;
- `automation/test_scheduler_catchup.py`: the exact seeded seven-day builtin
  remains due after restart, while ordinary overdue interval automations still
  advance normally.

Use fixture repositories containing:

- reachable active and archived objects;
- recovery-protected objects;
- young unreachable objects;
- expired unreachable objects;
- corrupt unreachable metadata.

## Acceptance

- A production caller exists.
- Both repositories are collected at least weekly.
- The grace remains 30 days.
- The operation is visible in automation run history but does not use a model
  or alter the four health workers.
- Collection failure is explicit and retryable.
