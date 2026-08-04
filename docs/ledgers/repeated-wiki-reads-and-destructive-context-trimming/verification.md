# Verification

## Status

Research baseline recorded. Implementation completion does not imply verification.

## Evidence

| ID | Related work | Check | Expected | Observed | Result | Evidence and time |
| --- | --- | --- | --- | --- | --- | --- |
| V-00 | Baseline | Inspect production transcript and runtime path | Distinguish UI retries, compaction, mutation invalidation, and model-generated repetition | 14 separate successful reads preceded the first mutation; canonical context limiter replaces live messages and placeholders self-compact | pass | SQLite read-only queries plus source inspection, 2026-08-04 |
| V-01 | I-01–I-04 | Focused server regression suite | Canonical/request separation, stable recovery pointer, receipt downgrade, unchanged reread, compaction/deferred behavior pass | `143 passed in 12.09s` | pass | `uv run pytest tests/test_agent_lib.py tests/test_model_context_budget.py tests/test_wiki_tools.py tests/test_deferred_tools.py -q`, 2026-08-04 |
| V-02 | I-01–I-04 | Ruff lint | No lint errors | `All checks passed!` | pass | `uv run ruff check arden tests`, 2026-08-04 |
| V-03 | I-01–I-04 | Full server suite | No regressions | `2542 passed in 151.78s` | pass | `uv run pytest -q`, 2026-08-04 |
| V-04 | I-01–I-04 | Changed-file formatting | All changed Python files formatted | `12 files already formatted` | pass | `uv run ruff format --check <12 changed files>`, 2026-08-04 |
| V-05 | Ledger | Validate structure/evidence state | Zero errors/warnings | `VALID: 0 errors, 0 warning(s)` | pass | `validate_ledger.py ... --repo .`, 2026-08-04 |

## Failures and gaps

- Repository-wide `ruff format --check .` initially reported five files. The two changed files were formatted; three pre-existing unrelated files remain: `arden/context/store.py`, `arden/storage_budget.py`, and `tests/test_session_store.py`.
- Post-deployment transcript observation is intentionally deferred and does not block code completion.

## Outcome

Verified in focused and full server suites. Live behavioral observation remains an accepted post-deployment gap.
