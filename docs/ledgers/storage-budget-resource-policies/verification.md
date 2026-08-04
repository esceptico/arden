# Verification

## Status

Research, implementation, and regression verification are complete on `codex/storage-retention-policies`.

## Evidence

| ID | Related work | Check | Expected | Observed | Result | Evidence and time |
| --- | --- | --- | --- | --- | --- | --- |
| V-01 | F-04 | Measure current Arden directories without mutation. | Identify the categories dominating the 6.2 GiB total. | Archive 3.4 GiB, sessions DB 2.3 GiB, blobs 406 MiB, memory 28 MiB, search DB 26 MiB, logs 16 MiB. | Pass | `du -sh ~/.arden{,/archive,/sessions.db,/blobs,/memory,/search.db,/logs}`; 2026-08-03 |
| V-02 | F-01-F-03 | Trace report arithmetic and deletion candidates. | Explain the 6.2 GiB “protected” label precisely. | Protected is `remaining - remaining_reclaimable`; reclaimable contains only old unreferenced content-addressed tool-result blobs. | Pass | `storage_budget.py:68-137`; 2026-08-03 |
| V-03 | F-06 | Trace a below-current limit through UI, config schema, and enforcement. | Determine whether the value is rejected or merely unattainable. | Values ≥0.1 GB are accepted; enforcement cleans eligible blobs, then returns `quota_blocked` if the 85% target remains unreachable. | Pass | `ArchiveTab.tsx:48-64`; `schemas.py:536-547`; `storage_budget.py:101-127`; 2026-08-03 |
| V-04 | F-07-F-08 | Inspect permanent-delete SQL and enumerate live schema tables carrying `session_id`. | Prove whether current deletion reclaims a whole chat. | SQL deletes only the archived `sessions` row; service additionally removes one legacy result directory. Live schema lists 22 session-keyed tables. | Pass | Targeted source inspection; read-only `sqlite_master`/`pragma_table_info` query; 2026-08-03 |
| V-05 | F-13 | Split live rows/logical transcript bytes by archived state and check orphans. | Establish whether chat-tier planning needs per-state accounting and orphan handling. | 302 archived / 1,201 current sessions; archived message/event bodies ≈463 MiB, current ≈1.1 GiB; orphan messages/events exist. | Pass | Read-only joined SQLite aggregates; 2026-08-03 |
| V-06 | F-09 | Inspect live auto-vacuum mode and compaction implementation. | Determine whether row deletion returns filesystem bytes. | `PRAGMA auto_vacuum` returned `0`; only offline `VACUUM INTO` is implemented. | Pass | Read-only PRAGMAs; `session_compaction.py:617`; 2026-08-03 |
| V-07 | F-10-F-12 | Trace pin authority and reusable tooltip implementation. | Verify safety/UI prerequisites. | Pins live in desktop preferences; shared tooltip supports focus/hover and supplementary accessibility. | Pass | `actions/sessions.ts:118-135`; `stores/types.ts:89-90`; `Tooltip.tsx:31-58`; 2026-08-03 |
| V-08 | F-14-F-20 | Compare official storage/retention UX across Docker, macOS, GitHub/GitLab, ChatGPT, LangGraph/LangSmith, Langfuse, and OpenAI Agents SDK. | Find reusable policies without copying one system blindly. | Convergent patterns: category/reclaimable views, explicit destructive consent, TTL for artifact-like data, Keep/promote exceptions, archive ≠ delete, access-based retention, cold export, and idle compaction. | Pass | Official sources linked in `research.md`; accessed 2026-08-03 |
| V-09 | F-21 | Check official SQLite reclamation semantics against Arden's live `auto_vacuum=NONE`. | Choose a policy that returns physical bytes without repeated full vacuums. | `INCREMENTAL` needs one full migration from `NONE`, then supports bounded page truncation; selected for ongoing reclamation. | Pass | [SQLite PRAGMA](https://www.sqlite.org/pragma.html#pragma_auto_vacuum); live V-06; 2026-08-03 |
| V-10 | I-01, I-05 | Exercise category accounting, backup TTL/Keep, traversal rejection, symlink exclusion, and exact file revalidation. | Totals reconcile and no file outside an allowlisted owner root is eligible. | Category sums are exact; Keep and anchored archive checks hold. | Pass | `tests/test_storage_budget.py`; 2026-08-04 |
| V-11 | I-03, I-04, I-08 | Verify deterministic tier ordering, stable plan hashes, API separation, and execution-time protection revalidation. | Planning is read-only; stale/current/pinned/active changes prevent deletion. | Stable plan/API contract passes; a candidate removed by revalidation is skipped and audited. | Pass | `tests/test_storage_budget.py`; `tests/test_storage_routes.py`; `tests/test_storage_runtime.py`; 2026-08-04 |
| V-12 | I-06, I-07 | Cold-convert and restore a real session DB; verify deterministic self-contained blob bundles and ownership-complete purge. | Transcript/tool evidence restores exactly; empty transcripts remain valid; all session-owned rows disappear on purge. | Prose/count/blob hashes pass, empty and populated cold restores are lossless, and dynamic ownership deletion leaves no matching rows. | Pass | `tests/test_cold_storage.py`; `tests/test_session_store.py`; 2026-08-04 |
| V-13 | I-09 | Delete a multi-megabyte cold-converted session from a WAL database and run bounded incremental vacuum. | Combined SQLite/WAL physical bytes shrink; new DB reports incremental mode. | Physical footprint decreased and reclaim mode remained incremental. | Pass | `test_cold_conversion_incrementally_returns_database_pages`; 2026-08-04 |
| V-14 | I-02, I-04, I-08 | Render the Settings flow with category data and a destructive plan. | Resource tooltip is accessible; Save only previews; destructive execution needs a second click. | All interaction assertions pass. | Pass | `apps/desktop/tests/storageBudget.test.tsx`; 2026-08-04 |
| V-15 | I-01-I-10 | Run full server lint and test suite. | No regressions. | Ruff clean; 2,498 tests passed. | Pass | `cd apps/server && uv run ruff check . && uv run pytest -q`; 2026-08-04 |
| V-16 | I-02, I-04, I-08 | Run full desktop checks and production build. | Types/lint/tests/build pass. | Typecheck and lint clean; 1,041 tests passed; Vite build completed. | Pass | `bun run typecheck`; `bun run lint`; `bun test`; `bun run build`; 2026-08-04 |

## Failures and gaps

- The selected 14-day/90-day/100-chat defaults are visible, configurable product choices and should be tuned from usage.
- Existing `auto_vacuum=NONE` databases need the one-time offline verified compactor before row cleanup can physically truncate the main DB; status reports this requirement.
- No destructive cleanup was run against the user's live Arden data during implementation verification; destructive proof used isolated fixtures.
- The production build retains pre-existing warnings for CSS `::highlight(...)` parsing and large chunks; neither originates in this storage change.

## Outcome

The accepted design is implemented and verified. Cleanup remains preview-first, protection-aware, and honest about the one-time migration needed by existing SQLite files.
