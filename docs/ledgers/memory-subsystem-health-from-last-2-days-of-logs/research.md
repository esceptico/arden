# Research

## Surface research

- **Scope**: Arden server behaviour 2026-08-03 09:29 UTC → 2026-08-04 23:43 UTC (the whole retained
  window at the time of the pass), restricted to the memory subsystem: facts ledger, wiki pages,
  fact/wiki automations, search indexing, and the memory-adjacent tool surface.
- **Sources inspected**
  - `~/.arden/logs/arden.log` + `arden.log.1` — 55 711 JSON lines. The file rotated (8 MB cap)
    mid-pass, so the window was snapshotted to
    `<scratchpad>/window.log` and every log count below is against that snapshot.
  - `~/.arden/sessions.db` — `scheduled_tasks`, `automation_runs`, `tool_calls`,
    `tool_results`, `session_messages_fts` catalog.
  - `~/.arden/memory.db` — `fact_consumer_watermarks`, `session_consumer_watermarks`,
    `wiki_maintenance_watermarks`, `fact_retention_checkpoints`.
  - `~/.arden/memory/facts/records/{2026-07,2026-08}.jsonl` — the canonical fact event ledger.
  - `~/.arden/memory/wiki/pages/` — 80 managed Markdown pages.
  - `~/.arden/search.db` — hybrid-search `items` table and `meta`.
  - Code at `31dff5a4`: `automation/{scheduler,builtins}.py`, `wiki/{service.py,maintenance/agent.py}`,
    `tools/{wiki,area}.py`, `context/store.py`, `search/store.py`.
- **Log level census (window)**: 54 903 info / 155 warning / 321 error.
- **Negative evidence**
  - No `Completed automation builtin-memory-dream` line anywhere in the window.
  - No fact event with `op` in {`amend`, `supersede`, `retract`} dated 2026-08-03 or later.
  - `memory_line` appears nowhere in the server source except one comment
    (`apps/server/arden/search/store.py:399`); it is not a live index source.
  - No wiki-maintenance failure after 2026-08-03 19:43 UTC.
  - No `file_search_text` FTS error outside 2026-08-03 13:32:08–13:59:04 UTC.

## Consolidated findings

| ID | Type | Claim | Evidence | Implication | Confidence | Last checked |
| --- | --- | --- | --- | --- | --- | --- |
| F-01 | fact | The memory subsystem is running and writing. In the window: capture 18 runs, synthesize 15, wiki-maintenance 15 (+4 failed), consolidate 3, retention 2 — all `completed` except the wiki failures. | `sqlite3 ~/.arden/sessions.db "select task_id,status,count(*) from automation_runs where started_at>='2026-08-03' group by 1,2"` | Baseline health is good; issues below are localized, not systemic. | high | 2026-08-05 |
| F-02 | fact | The fact ledger and the search index agree exactly. Replaying `records/*.jsonl` gives 239 distinct `fact_id`s whose terminal ops are 104 supersede / 25 retract / 99 amend / 11 create ⇒ 110 live facts. `search.db` holds exactly 110 rows with `source='fact'`. Wiki: 80 pages on disk, 80 rows with `source='wiki_page'`. | ledger replay script; `select source,count(*),max(indexed_at) from items group by 1` → `fact\|110\|2026-08-04T18:36:21Z`, `wiki_page\|80\|2026-08-04T23:40:37Z` | Indexing is not dropping or duplicating memory. Retrieval coverage is complete. | high | 2026-08-05 |
| F-03 | fact | All consumer watermarks are current to the end of the window: `memory.synthesis` 2026-08-04T18:42:38Z, `memory.maintenance` 23:37:46Z, `wiki.maintenance` 23:39:30Z, `wiki.projection` 23:41:35Z. | `sqlite3 ~/.arden/memory.db "select * from fact_consumer_watermarks; select * from wiki_maintenance_watermarks;"` | No consumer is stalled behind the ledger head. | high | 2026-08-05 |
| F-04 | fact | **Memory Dream has not run since 2026-08-02T00:00:26Z.** Its last two successful runs (2026-08-01, 2026-08-02) each reported `memory dream: 5 insight(s); published`; there is no run row and no completion line on 08-03, 08-04 or 08-05. | `select started_at,result from automation_runs where task_id='builtin-memory-dream'`; grep of window.log for `builtin-memory-dream` yields only 9 `Missed run` warnings | Cross-domain dream insight — the differentiator per prior design work — has been dark for 3 days. | high | 2026-08-05 |
| F-05 | inference | Dream's stall has two compounding causes. (a) `seed_builtins` rewrites `next_run_at` on every boot to `trigger.next_run(last_run_at or created_at)`, which for a builtin whose last run is already in the past resolves back into the past — the log shows `Updated builtin automation defaults: Memory Dream` immediately followed by `Missed run of automation builtin-memory-dream (was due 2026-08-03T00:00:00+00:00)` on **every one of the 9 boots**, i.e. the previous boot's advance to 08-04/08-05 was discarded. (b) `_should_catch_up_missed` covers only the synthesis/retention backstops and maintenance builtins, so dream's missed slot is advanced forward rather than run. | `apps/server/arden/automation/builtins.py:232-235`; `apps/server/arden/automation/scheduler.py:262-285,302-320`; 9× paired log lines in window.log | Dream only fires if the process happens to be alive at exactly 04:00 local (00:00 UTC). The server was offline at that instant on 08-03 and 08-04. Every other builtin either has a catch-up policy or a short `every` interval, which is why only dream is affected. | high | 2026-08-05 |
| F-06 | fact | **495 of 935 `tool_results` rows carry a `blob_path` under the dead `~/.ntrp/` root**; `~/.ntrp` does not exist. This produced 34 `Failed to rehydrate tool result file` warnings in the window, every one a `FileNotFoundError` on `/Users/escept1co/.ntrp/blobs/tool-results/…`. | `select count(*), sum(blob_path like '%/.ntrp/%') from tool_results` → `935\|495`; `ls -d ~/.ntrp` → no such file; tracebacks in window.log at 23:37:03–23:37:10 | Resuming any pre-rename session silently loses its offloaded tool results — the agent sees truncated context with no error surfaced to the user. | high | 2026-08-05 |
| F-07 | fact | The lost blobs are fully recoverable. Rewriting the path prefix `~/.ntrp/` → `~/.arden/` resolves to an existing file for **200/200** sampled stale rows; the content-addressed layout (`blobs/tool-results/<xx>/<sha256>.txt.gz`) is identical under both roots. | sampled 200 stale `blob_path`s, `sed 's|/.ntrp/|/.arden/|'`, `os.path.exists` → 200/200 | A single `UPDATE tool_results SET blob_path = replace(...)` recovers all 495. Nothing was deleted by the rename — only the recorded absolute path went stale. | high | 2026-08-05 |
| F-08 | fact | **The wiki read-gate rejects legitimate edits across run boundaries.** 15 of 36 `wiki_edit_page` calls failed with preview `Read required`, and 3 `area_page_patch` calls with `Read Area page first`, while `wiki_read_page` succeeded 91 times in the same window. | `select tool_name,status,count(*) from tool_calls where started_at>='2026-08-03' … group by 1,2` | ~42% of wiki edit attempts are wasted round-trips; the agent must re-read a page it already read. | high | 2026-08-05 |
| F-09 | inference | The read-gate failure is the previously diagnosed run-scoping defect: `_require_page_observation` resolves the receipt via `execution.ctx.run.resource_observation(...)`, so evidence is per-**run**, while the model's belief that it read the page is per-**session**. Any Stop/re-dispatch between the read and the edit invalidates the receipt. | `apps/server/arden/tools/wiki.py:200-217`; `apps/server/arden/tools/area.py:107` | Matches the standing `project_wiki_read_gate_run_scope_bug` diagnosis; still unfixed and still firing at ~9/day. | high | 2026-08-05 |
| F-10 | fact | `GeneratedRegionConflictError` killed 3 wiki-maintenance runs on 2026-08-03 (19:35:58, 19:41:00, 19:43:16 UTC) and produced **30** `Unhandled tool execution failed` errors on `wiki_maintenance_review`. First traceback originates at `wiki/service.py:516 → :967`; the other 29 are the same stored task exception re-raised from `_wait_for_state`'s `self._task.result()`. | window.log tracebacks; `select started_at,error from automation_runs where task_id='builtin-wiki-maintenance' and status='failed'` | The designed structured failure (`wiki_generated_region_conflict` + "Stop this review" recovery, `wiki/maintenance/agent.py:109-137`) never reached the agent — it saw 30 raw tracebacks and kept retrying. | high | 2026-08-05 |
| F-11 | fact | `wiki_maintenance_review` failed 43 of 233 calls (18%) and `wiki_create_page` 3 of 10 (`Wiki path already exists`); `fact_plan_changes` failed 3 of 7 (`Validation error`). | `tool_calls` aggregate for the window | Memory *write* paths are the noisy ones; read paths are clean. | high | 2026-08-05 |
| F-12 | fact | A stale, dead `memory_line` partition still occupies the search index: 78 rows, `max(indexed_at) = 2026-07-24T23:46:19Z`, titles like `fact line` / `lesson line` / `directive line`. | `select count(*),max(indexed_at) from items where source='memory_line'`; `rg memory_line apps/server/arden` → one comment only | 78 rows from a retired memory model still compete in the RRF merge, 11 days stale, with no writer to refresh them. | high | 2026-08-05 |
| F-13 | fact | **Resolved inside the window**: `table session_messages_fts has no column named file_search_text` caused 44 log errors, 22 failed `area:*` automation runs and 22 `Failed to save mid-run progress` warnings — all confined to 2026-08-03 13:32:08–13:59:04 UTC (one boot). The live `sessions.db` triggers are now canonical (`search_text` only, no `file_search_text`). | `select started_at,error from automation_runs where status='failed' and task_id like 'area%'` (all 22 on 08-03); `select sql from sqlite_master where name like 'session_messages%'` | The healing migration at `context/store.py:1471-1509` did its job. Historical context only — no action needed. | high | 2026-08-05 |
| F-14 | inference | Fact **maintenance** produced no mutations in the window. All 10 August fact events are `op='create'`; consolidate reported `reviewed 4; amended 0; merged 0` and `reviewed 6; amended 0; merged 0`; retention reported "No canonical facts are currently due" both times. Historically the ledger carries 104 supersedes, 99 amends and 25 retracts. | ledger replay by day/op; `select started_at,result from automation_runs where task_id in ('builtin-memory-consolidate','builtin-memory-retention')` | Cannot distinguish "nothing needed changing" from "the maintenance agent no longer proposes changes" from logs alone. Flagged, not concluded. | medium | 2026-08-05 |
| F-15 | fact | Fact write volume dropped sharply: 521 events in July (peaks of 60–181/day) vs 10 in August (08-03: 4, 08-04: 6), of which 6 came from `automation:memory.capture` and 4 from manual `tool.fact_changes`. Capture run results are mostly `fact capture idle` or `reviewed N session(s); no durable facts`. | ledger replay grouped by `occurred_at[:10]` and `actor`/`origin`; `automation_runs.result` for `builtin-memory-capture` | Consistent with a drained backlog (the July spikes are bulk ingests), but capture yield is now ~0.33 facts/run. Worth a look if the user expects more. | medium | 2026-08-05 |
| F-16 | fact | Retrieval is barely exercised by chat: 14 `fact_search` and 6 `search_transcripts` calls in two days, against 91 `wiki_read_page` and 46 `area_page_read`. | `tool_calls` aggregate for the window | Agents reach memory by direct page read, not by hybrid search. Not a defect, but it means F-12's stale partition has low blast radius today. | high | 2026-08-05 |
| F-17 | fact | The `Tools hidden (missing fact_capture): fact_capture_review` lines (25×) are **not** a capture defect. `builtin-memory-capture` carries `tool_scope='fact_capture'` and `fact_capture_review` succeeded 53 times in the window; the hidden-tool lines come from other agents whose scope legitimately excludes the terminal review tools. | `select task_id,tool_scope from scheduled_tasks where builtin=1`; `tool_calls` shows `fact_capture_review\|success\|53` | Ruled out as a cause of low capture yield. | high | 2026-08-05 |

| F-18 | fact | **`fact_search` never touches the hybrid index.** The tool (and the desktop `/admin/facts/search` endpoint) route through `FactService.search_page` → `FactLedger.search` → `needle in fact.normalized_text` — a literal substring match (`memory/facts/ledger.py:218-228`). The embeddings, FTS and RRF stack in `search/` are wired only into `memory/facts/index.py:114` for near-duplicate candidate generation. | code trace; live proof: `ledger.search('openai applications')` → **0 hits** while 4 OpenAI facts exist; `'applications to openai'` → 0; `'model internals'` → 0; single-word `'openai'` → 4 | Any multi-word natural-language fact query silently returns nothing or garbage. This — not embeddings — is the user-felt "fact search is broken". Contradicts the documented architecture ("Retrieval: hybrid search only", CLAUDE.md). | high | 2026-08-05 |
| F-19 | fact | The embedding pipeline itself is healthy: 192/192 items have vectors (112 fact + 80 wiki), `embedding_state=ready`, model `gemini-embedding-001` dim 3072 with a model-change rebuild guard, zero embedding errors and zero "Vector search failed" lines in the window. A live hybrid probe (router-initialized key, `vec=True` connection) returns semantically strong results — e.g. "what does timur think about model internals research" → the model-native-concepts and postconcept facts at vec ranks 1 and 6. | coverage counts; window.log greps; live `HybridRetriever.search` probe output | Embeddings are exonerated. The felt issue is the unwired retrieval path (F-18), not vector quality. | high | 2026-08-05 |

## Conflicts and gaps

- **F-14 is unresolved.** "Reviewed N, amended 0, merged 0" is indistinguishable in the logs from a
  healthy no-op. Settling it needs either a run transcript for a consolidate run or a deliberate
  test with a known-mergeable fact pair.
- **F-15 likewise.** The July → August volume drop has a plausible benign explanation (backlog
  drained; the 181-event 07-29 spike is clearly a bulk ingest) but no positive evidence either way.
- **F-05(a) is inferred from the log pairing, not from a direct read of `next_run_at` across boots.**
  The 9 consecutive `(was due 2026-08-03T00:00:00)` warnings after advances to 08-04 and 08-05 admit
  no other reading given `builtins.py:232-235`, but a `set_next_run` → re-read trace would nail it.
- **Log retention is ~3 days.** Rotation is size-based at 8 MB with 5 backups
  (`arden.log.5` is dated 08-02), and the file rotated during this pass. Anything older than the
  window is unrecoverable from logs.
- Not examined: dream insight *quality*, embedding freshness in `items_vec`, and the 232
  `Exception in ASGI application` errors (they carry no structured exception field and are outside
  the memory scope).

## Supporting material

- Window snapshot: `<scratchpad>/window.log` (55 711 lines, `arden.log.1` + `arden.log` concatenated).
- Non-memory noise deliberately excluded: 57 DuckDuckGo web-search HTTP warnings, 11 `Outbox handler
  failed` (`DetachedRunBindingPending`, area run binding), 7 judgeval version-upgrade notices,
  9 `Connection pool is full` warnings.
