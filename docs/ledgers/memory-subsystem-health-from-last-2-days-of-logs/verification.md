# Verification

## Status

Two phases. **Research verification** (V-01 – V-13) established the findings on 2026-08-05 before
any change. **Implementation verification** (V-14 – V-19) covers the remediation pass later the
same day. Each row is a command run against live state with its observed output.

## Evidence

| ID | Related work | Check | Expected | Observed | Result | Evidence and time |
| --- | --- | --- | --- | --- | --- | --- |
| V-01 | F-01 | Tally automation run outcomes in the window | Memory builtins run and complete | capture 18 completed; synthesize 15 completed; wiki-maintenance 15 completed / 4 failed; consolidate 3 completed; retention 2 completed | pass | `sqlite3 ~/.arden/sessions.db "select task_id,status,count(*) from automation_runs where started_at>='2026-08-03' group by 1,2"` — 2026-08-05T03:5x+04:00 |
| V-02 | F-02 | Replay the fact ledger and compare to the search index | live fact count == indexed fact count | ledger: 239 fact_ids, terminal ops 104 supersede / 25 retract / 99 amend / 11 create ⇒ 110 live. index: `fact\|110`. wiki: 80 files, `wiki_page\|80` | pass | ledger replay over `~/.arden/memory/facts/records/*.jsonl`; `sqlite3 ~/.arden/search.db "select source,count(*),max(indexed_at) from items group by 1"` — 2026-08-05T03:5x+04:00 |
| V-03 | F-03 | Read all memory consumer watermarks | current to end of window | `memory.synthesis` 2026-08-04T18:42:38Z, `memory.maintenance` 23:37:46Z, `wiki.maintenance` 23:39:30Z, `wiki.projection` 23:41:35Z, retention checkpoint 2026-08-04T08:59:26Z | pass | `sqlite3 ~/.arden/memory.db` over the four watermark tables — 2026-08-05T03:5x+04:00 |
| V-04 | F-04 | Look for any dream run in the window | at least one nightly run | zero run rows after 2026-08-02T00:00:26Z; zero `Completed automation builtin-memory-dream` lines; 9 `Missed run` warnings | **fail — issue confirmed** | `select started_at,result from automation_runs where task_id='builtin-memory-dream'`; grep of window.log — 2026-08-05T03:5x+04:00 |
| V-05 | F-05 | Check whether the scheduler's advance survives a reboot | later boots report a later due time | all 9 boots report `(was due 2026-08-03T00:00:00+00:00)` despite prior advances to 08-04 and 08-05, each immediately preceded by `Updated builtin automation defaults: Memory Dream` | **fail — reset loop confirmed** | window.log line pairs; `builtins.py:232-235` — 2026-08-05T03:5x+04:00 |
| V-06 | F-06 | Count tool-result rows pointing at the pre-rename root | 0 | 935 total, **495** under `~/.ntrp/`, 440 under `~/.arden/`; `ls -d ~/.ntrp` → No such file or directory | **fail — issue confirmed** | `sqlite3 ~/.arden/sessions.db "select count(*), sum(blob_path like '%/.ntrp/%'), sum(blob_path like '%/.arden/%') from tool_results"` — 2026-08-05T04:0x+04:00 |
| V-07 | F-07 | Test whether a prefix rewrite recovers the stale blobs | most resolve | **200/200** sampled stale paths exist after rewriting `/.ntrp/` → `/.arden/` | pass — fully recoverable | sampled 200 rows, `sed` + `os.path.exists` — 2026-08-05T04:0x+04:00 |
| V-08 | F-08 | Aggregate memory tool-call outcomes for the window | write paths succeed | `wiki_edit_page` 21 success / **15 error** (all preview `Read required`); `area_page_patch` 51 success / 3 error (`Read Area page first`); `wiki_read_page` 91 success | **fail — issue confirmed** | `select tool_name,status,count(*) from tool_calls where started_at>='2026-08-03' … group by 1,2`; `select result_preview … where status='error'` — 2026-08-05T04:0x+04:00 |
| V-09 | F-09 | Locate the read-gate receipt lookup | session-scoped | `execution.ctx.run.resource_observation(...)` — run-scoped | **fail — cause confirmed** | `apps/server/arden/tools/wiki.py:200`; `apps/server/arden/tools/area.py:107` — 2026-08-05T04:0x+04:00 |
| V-10 | F-10 | Trace the 30 `Unhandled tool execution failed` errors | structured failure, not a traceback | all 30 on `wiki_maintenance_review`; first raises at `wiki/service.py:516 → :967`, remaining 29 re-raise the stored task exception from `_wait_for_state`'s `self._task.result()` | **fail — issue confirmed** | window.log tracebacks; `select started_at,error from automation_runs where task_id='builtin-wiki-maintenance' and status='failed'` — 2026-08-05T03:5x+04:00 |
| V-11 | F-12 | Check the `memory_line` index partition against the code | either fresh, or absent | 78 rows, `max(indexed_at)=2026-07-24T23:46:19Z`; `rg memory_line apps/server/arden` → one comment at `search/store.py:399`, no writer | **fail — dead partition confirmed** | `sqlite3 ~/.arden/search.db`; ripgrep — 2026-08-05T04:0x+04:00 |
| V-12 | F-13 | Confirm the FTS schema fault is repaired | canonical triggers | live `session_messages_fts` is `fts5(search_text, content='session_messages')`; all three triggers reference `search_text` only, no `file_search_text`; all 44 log errors and 22 failed area runs fall inside 2026-08-03 13:32:08–13:59:04Z | pass — self-repaired | `sqlite3 ~/.arden/sessions.db "select sql from sqlite_master where name like 'session_messages%'"`; run/log timestamp bounds — 2026-08-05T03:4x+04:00 |
| V-13 | F-17 | Rule out `Tools hidden (missing fact_capture)` as a capture defect | capture has the scope it needs | `builtin-memory-capture.tool_scope = 'fact_capture'`; `fact_capture_review` succeeded 53× in the window | pass — ruled out | `select task_id,tool_scope from scheduled_tasks where builtin=1`; `tool_calls` aggregate — 2026-08-05T04:0x+04:00 |

## Implementation evidence (remediation pass, 2026-08-05)

| ID | Related work | Check | Expected | Observed | Result | Evidence and time |
| --- | --- | --- | --- | --- | --- | --- |
| V-14 | P-02 | Stale-path count after blob rewrite | 0 stale rows | `UPDATE` reported 495 changes; recount → `935\|0`; 400/400 sampled `blob_path`s exist on disk | pass | `sqlite3 ~/.arden/sessions.db` update + recount + `os.path.exists` sweep — 2026-08-05T04:2x+04:00 |
| V-15 | P-05 | `memory_line` rows in items and items_vec | 0 in both | delete of 78 ids; `items` now `fact\|112, wiki_page\|80`; `items_vec` leftover count 0 | pass | `sqlite_vec`-loaded connection, delete + recount — 2026-08-05T04:2x+04:00 (fact count 112 vs research's 110: the live server indexed 2 new facts between passes) |
| V-16 | P-01R | No dream reference left in server source | zero matches | `rg "BUILTIN_MEMORY_DREAM_ID\|memory_dream\|FactDream\|completion_dream" apps/server --type py` → no matches | pass | ripgrep sweep after edits — 2026-08-05T04:3x+04:00 |
| V-17 | P-01R | Affected test files pass after removal | all pass | `pytest tests/test_automation_store.py tests/test_runtime_wiki_health.py tests/test_prompt_consistency.py` → 48 passed | pass | 2026-08-05T04:3x+04:00 |
| V-18 | P-03 | Read-gate failures split by fix time (fix landed 2026-08-04 ~17:44 UTC) | post-fix failures explained | 17/19 window failures predate the fix; the 2 after (2026-08-04T18:17:41Z, 2026-08-05T00:42:53Z) each fall within ~1 min of a server restart, where in-memory receipts are legitimately empty | pass — fix confirmed effective | `tool_calls` timestamps × boot list; wiki/observation test subset → 357 passed — 2026-08-05T04:4x+04:00 |
| V-19 | P-04 | Structured-failure mapping in HEAD + coverage | mapped, tested | `git show 443aead9` shows `_task.result()` → `_completed_state()`; `tests/test_wiki_maintenance_agent.py:154` asserts `wiki_generated_region_conflict`; suite 6 passed | pass | 2026-08-05T04:4x+04:00 |
| V-20 | gate | Full server gate: ruff check, ruff format, pytest | all green | ruff check "All checks passed!", format clean after 2 test files reformatted; **2570 passed in 160.67s**, 0 failures | pass | 2026-08-05T04:5x+04:00 |
| V-21 | P-06 | Locate consolidate run transcripts | readable transcript | `automation_runs.chat_session_id` NULL for all consolidate runs; ephemeral session `20260804_233711_146` has 0 messages, 0 events, 0 tool_results | n/a — transcripts don't exist; pivoted to reviewed-set reconstruction | 2026-08-05T05:0x+04:00 |
| V-22 | P-06 | Judge the 23:37 run's 6 reviewed facts for mergeability | amended 0/merged 0 justified? | 5/6 genuinely distinct; 1 real near-duplicate pair (OpenAI applications, 17:13:20 vs 17:13:52) is **cross-scope** (`area:area_7ae5c98cc338` vs `user`) and excluded from the candidate pool by `fact.scope == target.scope` at `maintenance/runner.py:452` | maintenance loop correct; scope filter makes the one real dupe invisible by construction | 2026-08-05T05:0x+04:00 |
| V-23 | P-06 | Check whether the run's `Validation error` indicates a broken decision path | agent recovers | one `invalid_arguments` on the 2nd of 8 review calls, all subsequent calls succeed, run completes | pass — constrained interface self-corrected | 2026-08-05T05:0x+04:00 |
| V-24 | F-19 | Embedding coverage and state | 100% coverage, ready | items 112 fact + 80 wiki; items_vec identical per source; 0 missing; `embedding_state=ready`, model gemini-embedding-001 dim 3072 | pass — embeddings healthy | 2026-08-05T05:5x+04:00 |
| V-25 | F-19 | Live hybrid retrieval probe (vec=True connection, router-initialized Gemini key) | vector leg returns ranked results | vec ranks populate; "model internals research" → model-native-concepts fact vec=1, postconcept vec=6 — semantically correct | pass — hybrid stack works when actually called | 2026-08-05T06:0x+04:00 |
| V-26 | F-18 | `fact_search` path behavior on natural queries | relevant hits | `FactLedger.search`: `'openai applications'` → 0 hits, `'applications to openai'` → 0, `'model internals'` → 0; `'openai'` → 4. Code: substring match at `ledger.py:218-228`; hybrid index consumed only by `memory/facts/index.py:114` (duplicate candidates) | **fail — root cause of user-felt fact-search issues confirmed** | 2026-08-05T06:0x+04:00 |

| V-27 | P-07 | End-to-end ranked fact search through the wired `FactService` (real search.db, real embedder) | previously-0-hit queries return relevant facts | `'openai applications'` → 4 hits (all OpenAI application facts top-3); `'applications to openai'` → 4; `'model internals'` → 3 (model-native-concepts fact rank 2); `'job hunt at the maker of chatgpt'` → 4 | pass | 2026-08-05T06:3x+04:00 |
| V-28 | P-07 | Full server gate after the wiring rework (ledger-based projection, constructor injection) | green | ruff clean; **2574 passed in 156.85s**, 0 failures | pass | 2026-08-05T06:4x+04:00 |

## Failures and gaps

- **Six checks failed by design** (V-04 – V-06, V-08 – V-11): these are the confirmations of the
  reported issues, not defects in the research method.
- **F-14 and F-15 have no verification row.** Neither "fact maintenance amends nothing" nor "capture
  yield dropped 60×" can be settled from logs and DB state alone — both need a run transcript or a
  seeded test. They are recorded as flagged observations, not conclusions. P-06 in
  `implementation.md` is the check that would close them.
- **F-05(a) is inferred, not directly traced.** The 9-boot log pairing plus `builtins.py:232-235`
  admits no other reading, but a `set_next_run` → re-read trace across a restart would be direct proof.
- **Evidence horizon.** Log rotation is size-based (8 MB, 5 backups ≈ 3 days) and `arden.log` rotated
  during this pass. Findings were computed against a fixed snapshot
  (`<scratchpad>/window.log`); re-running the same greps against the live file will not reproduce
  the counts once rotation advances.
- Not verified at all: dream insight quality, `items_vec` embedding freshness, and the 232
  `Exception in ASGI application` errors (no structured exception field; out of memory scope).

## Outcome

Research scope verified: the health claim (F-01 – F-03) and each of the six live issues rest on a
reproducible command with recorded output.

Remediation pass verified: P-01R (dream removal), P-02 (blob repair), P-05 (`memory_line`
eviction) and P-07 (`fact_search` → hybrid index) are implemented and checked; P-03 and P-04 were
found already fixed (2026-08-04) and their effectiveness confirmed against the failure timeline;
P-06 concluded (maintenance loop healthy; cross-scope dedup blind spot surfaced as a design
question). Final full server gate green: ruff clean, **2574 tests passed** (V-28), with live
end-to-end ranked-search proof (V-27). Remaining accepted gaps: the cross-scope dedup design
decision, and runtime-visible effects that need the next server restart — the retired-id sweep
deleting the stored dream automation, rehydration exercising the rewritten blob paths, and the
ranked fact search going live in the running process.
