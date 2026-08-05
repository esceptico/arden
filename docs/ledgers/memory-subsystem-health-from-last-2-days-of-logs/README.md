<!-- development-ledger:v2 -->

# Memory subsystem health from last 2 days of logs

## Status

| Field | Value |
| --- | --- |
| State | complete |
| Active phase | verification |
| Created | 2026-08-05T03:39:01+04:00 |
| Last updated | 2026-08-05T04:55:00+04:00 |
| Last consolidated | 2026-08-05T04:55:00+04:00 |
| Codebase branch | main |
| Codebase revision | 31dff5a421572d754bbe13ec64428837ee2634f8 |
| Sources checked through | code: 31dff5a421572d754bbe13ec64428837ee2634f8; runtime: `~/.arden` as of 2026-08-05T03:43+04:00; logs: 2026-08-03T09:29Z → 2026-08-04T23:43Z; web: not checked |

## Original task — verbatim

i want you to research the latest (2 days) logs and check if memory is working and which issues we may have
use /development-ledger for it

## Amendments — verbatim

2026-08-05, after the research pass and remediation-plan discussion:

> ok but:
>
> 1. remove dream automation it's a bit useless
> 2. do not overengineer
> 3. do not use defensive programming
>
> you can proceed with other ones. do not forget /development-ledger

## Current synthesis

**Memory is working.** Every memory automation ran and completed in the window, the fact ledger and
the search index agree exactly (110 live facts ↔ 110 indexed rows; 80 wiki pages ↔ 80 indexed rows),
and all four consumer watermarks are current to the last minute of the window. Nothing is stalled
behind the ledger head. [F-01, F-02, F-03]

Six issues sit on top of that healthy baseline, in severity order:

1. **Memory Dream has been dark for 3 days** — last run 2026-08-02, none since. `seed_builtins`
   rewrites `next_run_at` back into the past on every boot, and dream is not in the scheduler's
   catch-up allowlist, so the missed slot is skipped rather than run. It now only fires if the
   process happens to be alive at exactly 04:00 local. [F-04, F-05]
2. **53% of `tool_results` rows point at the dead `~/.ntrp/` blob root** (rename fallout) — 495 of
   935. Resuming a pre-rename session silently loses its offloaded tool results. Fully recoverable:
   200/200 sampled blobs exist at the identical path under `~/.arden/`. [F-06, F-07]
3. **The wiki read-gate rejects legitimate edits across run boundaries** — 15 of 36 `wiki_edit_page`
   calls and 3 `area_page_patch` calls failed with "Read required". The receipt is scoped to
   `ctx.run`; the model's memory of reading is per-session. Previously diagnosed, still unfixed. [F-08, F-09]
4. **`GeneratedRegionConflictError` escapes as an unhandled tool error** instead of the designed
   structured failure — 3 dead wiki-maintenance runs and 30 raw tracebacks handed to the agent on
   2026-08-03. No recurrence since. [F-10]
5. **A dead `memory_line` partition still sits in the search index** — 78 rows, 11 days stale, with
   no writer anywhere in the code. [F-12]
6. **Fact maintenance produced zero mutations** in the window (all 10 August events are `create`;
   consolidate reports amended 0 / merged 0 every run). Flagged, not concluded — the logs cannot
   distinguish this from a healthy no-op. [F-14, F-15]

One issue **resolved itself inside the window**: the `session_messages_fts has no column named
file_search_text` schema fault destroyed 22 area-automation runs on 2026-08-03 between 13:32 and
13:59 UTC, then the healing migration repaired the triggers. The live DB is canonical. No action. [F-13]

**Remediation outcome (same day).** All six issues are now closed or dispositioned:

1. Dream → **removed entirely** per the user (P-01R): spec, handler, renderer modules, health owner
   and constants deleted; `builtin-memory-dream` added to the retired-id sweep. Takes full effect on
   next server restart.
2. Blob paths → **repaired**: 495 rows rewritten to `~/.arden/`, 0 stale remain, 400/400 sampled
   files resolve. Table backed up first.
3. Read-gate → **found already fixed** (uncommitted codex change, 2026-08-04): receipts are
   session-scoped via `RunRegistry`. The window's failures predate the fix; the two later ones
   follow server restarts, where an in-memory receipt is legitimately absent. F-08/F-09's "still
   firing" reading was wrong — corrected here.
4. `GeneratedRegionConflictError` → **found already fixed** in commit `443aead9` (in HEAD), with
   test coverage.
5. `memory_line` partition → **evicted**: 78 rows from `items` + `items_vec`, comment cleaned.
6. Fact-maintenance yield (F-14/F-15) → **investigated (P-06)**: the maintenance loop is NOT
   degraded — 5 of the 6 facts it reviewed were genuinely distinct, and its one schema-rejected
   decision self-corrected. But the one real duplicate in the window (the OpenAI-applications
   pair, recorded 32 s apart by manual `fact_changes` in area scope and by memory-capture in
   user scope) is invisible to it: the candidate pool filters `fact.scope == target.scope`
   (`maintenance/runner.py:452`). Cross-scope dedup is a surfaced design question, not a bug fix.
   Capture volume drop = backlog drained; capture is if anything slightly over-permissive
   ("timur likes research." as a durable fact).

Server gate after the pass: ruff clean, **2570 tests passed**.

## Decisions

- The window was snapshotted before analysis because `arden.log` rotated mid-pass; all counts are
  against that fixed snapshot rather than the live file.
- User-adopted (2026-08-05): remove Memory Dream rather than repair its scheduling; no
  over-engineering (no blob-root indirection); no defensive code.
- Data repairs ran against the live WAL-mode databases (single statements, busy timeout) rather
  than stopping the user's server; the affected `tool_results` rows were dumped to
  `~/.arden/sessions.tool_results.backup-20260805.sql` first.

**Late finding (F-18/F-19), prompted by the user's "I had issues with fact search":** the
cross-scope dedup blind spot is real but is NOT what the user was feeling. **`fact_search` never
used the hybrid index.** It routed through `FactLedger.search`, a literal substring match over
`normalized_text` — `'openai applications'` returned 0 hits while 4 OpenAI facts existed. The
embeddings themselves are healthy (100% coverage, ready state); they were consumed only by
near-duplicate candidate generation. **Fixed same day (P-07):** `FactIndexProjection` now depends
on the ledger (breaking the service↔projection cycle), exposes `ranked_fact_ids`, and injects
into `FactService` at construction; queries are relevance-ranked top-N (`has_more=False` — no
cursor for ranked order), `status='all'` keeps the exhaustive substring scan, and an unavailable
index surfaces a retryable `search_unavailable` failure instead of degrading silently. Verified
end-to-end: all previously-0-hit queries return relevant facts (V-27); full gate 2574 passed (V-28).

## Open questions

- Should fact maintenance see cross-scope duplicate candidates? Merging across scopes changes the
  surviving fact's visibility domain (area vs user), so it needs a product decision: widen the
  candidate pool, or make one of the two writers defer when the same event is already recorded in
  the other scope. (From P-06.)
- Minor: subject spelling drift (`Timur Ganiev` vs `Timur`) weakens shared-subject candidate
  matching; worth normalizing at capture time if cross-scope dedup is pursued.

## Next action

User review of the uncommitted changes, then a server restart to activate the dream removal
(retired-id sweep) and exercise the repaired blob paths. Decide the cross-scope dedup question
above if the OpenAI-style duplicate pairs bother you.

## Details

- [Research](research.md)
- [Implementation](implementation.md)
- [Verification](verification.md)
