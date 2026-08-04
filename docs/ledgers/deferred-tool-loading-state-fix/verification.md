# Verification

## Status

Implementation verified.

## Evidence

| ID | Related work | Check | Observed | Result |
| --- | --- | --- | --- | --- |
| V-01 | F-01/F-03 | `uv run pytest tests/test_openai_client.py::test_responses_request_allows_visible_tool_search_loader_with_native_deferred_tools tests/test_openai_client.py::test_responses_request_uses_native_deferred_tool_search tests/test_deferred_tools.py::test_compaction_unloads_deferred_tools_and_refreshes_schema -q` from `apps/server` | `3 passed`; current behavior reproduced | Pass |
| V-02 | Anthropic equivalent | Targeted Anthropic duplicate-loader request-shape test | `1 passed`; current behavior reproduced | Pass |
| V-03 | I-01–I-08 | Focused deferred tools, providers, wiki state, cancellation, outcomes, workflow, and namespace suites | `189 passed`; final workflow/namespace rerun `54 passed` | Pass |
| V-04 | I-01–I-08 | `uv run pytest -q` from `apps/server` | `2532 passed in 150.59s` | Pass |
| V-05 | Changed Python | `uv run ruff check .` from `apps/server` | All checks passed | Pass |
| V-06 | Changed files | `ruff format --check` over all 26 changed Python files | 26 files already formatted | Pass |
| V-07 | Patch integrity | `git diff --check` | No errors | Pass |
| V-08 | Ledger | Skill validator after final consolidation | `VALID` | Pass |
| V-09 | I-09–I-12 | Focused tools/wiki/deferred/docs/scope/runtime regressions | `213 passed`; final focused safety rerun `141 passed` | Pass |
| V-10 | I-09–I-12 | `uv run pytest -q` from `apps/server` | `2538 passed in 141.11s` | Pass |
| V-11 | I-09–I-12 | `uv run ruff check .` from `apps/server` | All checks passed | Pass |
| V-12 | I-09–I-12 | Changed Python formatting plus `git diff --check` | All changed files formatted; no patch errors | Pass |

## Covered regressions

- Native requests contain one search mechanism, never custom and native together.
- Structured receipts reconstruct discovery; prose does not.
- Compaction restores loaded tools without silently clearing them.
- Wiki reads authorize later-turn edits but compaction requires content to be reread.
- Missing workflow inputs spawn zero agents; fan-out is bounded and approval-gated.
- Cancellation does not start a fresh model run solely to narrate cancellation.
- Provider-visible failed results contain typed outcome and recovery metadata.
- Wiki full-replacement preflight fails before approval when unread.
- Exact wiki patches require one unique current match, preserve unrelated content, and conflict when the page changes before commit.
- Native discovery of a full wiki mutation also exposes `wiki_read_page`.

## Failures and gaps

- Comparison-repository tests were inspected, not executed.
- Repository-wide `ruff format --check .` still reports three unrelated, clean baseline files: `arden/context/store.py`, `arden/storage_budget.py`, and `tests/test_session_store.py`. Changed files pass formatting.
- Model-level nondeterminism still benefits from a production trace/eval in addition to deterministic request and dispatcher tests.
- Wiki observations are in-memory; process restart safely falls back to requiring another read.
- Preflight handlers are intentionally limited to server-placed tools. Client file mutations retain their independent device-side exact-match/CAS enforcement.

## Outcome

All verified incident mechanisms and Claude-report findings in scope are implemented and covered by the full server suite. Generic duplicate-call suppression was intentionally excluded because repeated identical stateful calls can be correct.
