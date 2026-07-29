# Plan 004: Make Area rollback reconciliation fail loudly

## Outcome

An Area update must either leave its row and custodian automation in the new
contract, or restore and reconcile the old contract. If compensation also
fails, both failures must be visible.

## Current state

`AreaLifecycleService.update()`:

1. updates the Area row;
2. synchronizes/disables its custodian;
3. restores patched fields when step 2 fails;
4. silently ignores a failed `_sync_custodian(restored)`.

It also does not disable a partially created/enabled custodian when the restored
Area has no autonomy.

## Implementation

Refactor only the `update()` compensation path in
`apps/server/arden/areas/lifecycle.py`:

1. Capture the primary sync/disable exception.
2. Restore exactly the fields changed by the patch, including the existing
   `paused` to `paused_at` translation.
3. If restore returns no row, raise an `ExceptionGroup` containing the primary
   failure and an explicit restore failure.
4. Reconcile the restored contract:
   - autonomy present: `_sync_custodian(restored)`;
   - autonomy absent: `_disable_custodian(area_id)`.
5. If reconciliation succeeds, re-raise the original primary error.
6. If reconciliation fails, raise an `ExceptionGroup` containing both errors.

Use Python's built-in exception grouping instead of a custom error wrapper.
Never `pass`, fabricate success, or log-and-continue. Keep the normal
single-error path readable with guards and shallow nesting.

Do not introduce a queue, background retry, or new Area state for this local
request transaction. Startup's existing exact Area/automation reconciliation
remains the process-crash recovery path.

## Tests

Extend `apps/server/tests/test_areas_lifecycle.py`:

- failed new sync restores the row and successfully restores the old custodian;
- failed enable from a previously non-autonomous Area disables the partial
  custodian during rollback;
- failed rollback sync surfaces both exceptions and leaves the restored row
  visible;
- missing row during DB compensation surfaces both failures;
- archive and restore behavior is unchanged.

Add/extend router coverage to prove the failure remains a non-success response;
do not map it to a friendly 2xx or hide its causes.

## Acceptance

- No exception is swallowed.
- A successful rollback restores both persistent and live contracts.
- A failed rollback exposes both causes.
- The startup exact-agreement check remains green for all autonomous Areas.

