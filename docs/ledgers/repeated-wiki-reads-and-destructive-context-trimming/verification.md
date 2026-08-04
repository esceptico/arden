# Verification

## Status

All implementation phases pass focused and full-server regression coverage, lint, formatting, and ledger validation.

## Evidence

| ID | Related work | Check | Expected | Observed | Result | Evidence and time |
| --- | --- | --- | --- | --- | --- | --- |
| V-00 | Baseline | Inspect production transcript and runtime path | Distinguish UI retries, compaction, mutation invalidation, and model-generated repetition | 14 separate successful reads preceded the first mutation; canonical context limiter replaces live messages and placeholders self-compact | pass | SQLite read-only queries plus source inspection, 2026-08-04 |
| V-01 | I-01–I-04 | Focused server regression suite | Canonical/request separation, stable recovery pointer, receipt downgrade, unchanged reread, compaction/deferred behavior pass | `143 passed in 12.09s` | pass | `uv run pytest tests/test_agent_lib.py tests/test_model_context_budget.py tests/test_wiki_tools.py tests/test_deferred_tools.py -q`, 2026-08-04 |
| V-02 | I-01–I-04 | Ruff lint | No lint errors | `All checks passed!` | pass | `uv run ruff check arden tests`, 2026-08-04 |
| V-03 | I-01–I-04 | Full server suite | No regressions | `2542 passed in 151.78s` | pass | `uv run pytest -q`, 2026-08-04 |
| V-04 | I-01–I-04 | Changed-file formatting | All changed Python files formatted | `12 files already formatted` | pass | `uv run ruff format --check <12 changed files>`, 2026-08-04 |
| V-05 | Ledger | Validate structure/evidence state | Zero errors/warnings | `VALID: 0 errors, 0 warning(s)` | pass | `validate_ledger.py ... --repo .`, 2026-08-04 |
| V-06 | I-06–I-13 | Consolidated focused regression suite | Context projection, restart, recovery, providers, background results, compaction, receipts, and tool namespaces pass | `530 passed in 24.64s` | pass | `uv run pytest tests/test_compactor.py ... tests/test_chat_background_merge.py -q`, 2026-08-05 |
| V-07 | I-06–I-13 | Full server suite | No regressions | `2552 passed in 161.41s` | pass | `uv run pytest -q`, 2026-08-05 |
| V-08 | I-06–I-13 | Ruff lint | No lint errors | `All checks passed!` | pass | `uv run ruff check arden tests`, 2026-08-05 |
| V-09 | I-06–I-13 | Changed-file formatting | Every changed Python file formatted | `40 files left unchanged` after final pass | pass | `uv run ruff format <changed Python files>`, 2026-08-05 |
| V-10 | I-06–I-13 | Diff whitespace validation | No whitespace errors | no output | pass | `git diff --check`, 2026-08-05 |
| V-11 | Ledger | Validate structure/evidence state | Zero errors/warnings | `VALID: 0 errors, 0 warning(s)` | pass | `validate_ledger.py ... --repo .`, 2026-08-05 |
| V-12 | I-14 | Full session-store suite | Migration, CAS, recovery, rewind, and stale-writer regressions pass | `122 passed in 5.94s` | pass | `uv run pytest -q tests/test_session_store.py`, 2026-08-05 |
| V-13 | I-14 | Session service and context routes | Service serialization and route contracts pass | `15 passed in 3.53s` | pass | `uv run pytest -q tests/test_session_service.py tests/test_context_routes.py`, 2026-08-05 |
| V-14 | I-14 | Background streaming regressions | Generation fencing does not break backgrounded run handling | `8 passed, 71 deselected in 3.55s` | pass | `uv run pytest -q tests/test_streaming_events.py -k 'backgrounded or background_result or clear or rewind'`, 2026-08-05 |
| V-15 | I-14 | Changed-code lint and diff whitespace | No lint or whitespace errors | `All checks passed!`; `git diff --check` emitted no output | pass | Ruff on context/session/chat files plus changed test; `git diff --check`, 2026-08-05 |
| V-16 | I-15 | Full session-store suite | Cross-session isolation, rollback, cold restore, CAS, migration, and storage regressions pass | `127 passed in 5.91s` | pass | `uv run pytest -q tests/test_session_store.py`, 2026-08-05 |
| V-17 | I-15 | Session service and context routes | Cold-aware clear preserves existing service and route behavior | `15 passed in 3.57s` | pass | `uv run pytest -q tests/test_session_service.py tests/test_context_routes.py`, 2026-08-05 |
| V-18 | I-16 | Atomic branch and background-result regressions | Branch records commit together, survive source deletion/cache-copy failure, and cannot resume or re-notify | `151 passed`; focused branch subset `5 passed` | pass | Session-store plus background run/tool suites, 2026-08-05 |
| V-19 | I-17 | Final recovery-edge regressions | Content-only exact envelopes and malformed/non-array projection pinning work | `7 passed` across two focused commands | pass | Focused tool and session-store selections, 2026-08-05 |
| V-20 | I-06–I-17 | Consolidated context/provider/offload suite | Context, branch, provider, background, and recovery contracts pass together | `363 passed, 15 deselected in 15.85s` | pass | Consolidated 11-file pytest command, 2026-08-05 |
| V-21 | I-01–I-17 | Fresh full server suite | No regressions | `2579 passed in 144.26s` | pass | `uv run pytest -q`, 2026-08-05 |
| V-22 | I-01–I-17 | Ruff lint and diff whitespace | No lint or whitespace errors | `All checks passed!`; `git diff --check` emitted no output | pass | `uv run ruff check arden tests`; `git diff --check`, 2026-08-05 |
| V-23 | I-01–I-17, ledger | Changed-file formatting and ledger validation | All changed Python files formatted; ledger has no errors or warnings | `42 files already formatted`; `VALID: 0 errors, 0 warning(s)` | pass | Changed-file `ruff format --check`; `validate_ledger.py ... --repo .`, 2026-08-05 |

## Failures and gaps

- Repository-wide `ruff format --check .` reports one pre-existing unrelated file: `arden/storage_budget.py`. Every file changed by this work is formatted.
- Post-deployment transcript observation is intentionally deferred and does not block code completion.
- V-01 through V-05 cover the superseded request-only limiter design; V-06 through V-10 cover the final ingestion/checkpoint architecture.
- V-07 predates the final architecture; V-21 is the fresh full-server proof.

## Outcome

I-01 through I-17 are implemented and regression-verified. Post-deployment repeated-read observation remains intentionally deferred.
