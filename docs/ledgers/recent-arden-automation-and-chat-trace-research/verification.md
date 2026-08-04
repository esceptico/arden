# Verification

## Status

Research evidence and the adopted implementation slice are verified. Explicit deferrals remain open.

## Evidence

| ID | Check | Observed | Result |
| --- | --- | --- | --- |
| V-01 | Live SQLite integrity | `ok` | pass |
| V-02 | Window outbox state | 219 total; 219 completed; 18 retried; 0 unresolved | pass |
| V-03 | Automation event queue/dead letter | 0 / 0 | pass |
| V-04 | Current FTS shape | `session_messages.search_text`; 3 canonical `search_text` triggers | pass |
| V-05 | Post-repair area runs | affected agents later completed | pass |
| V-06 | Current focused tests | 110 passed in 8.18s | pass |
| V-07 | Terminal consistency | failed child rows paired with completed terminal events; open tools leaked until restart | fail |
| V-08 | Wiki Maintenance conflict shaping | expected conflicts become opaque `internal_error` and retry | fail |
| V-09 | Compaction inventory | 4 automatic events absent from `chat_compactions` | fail |
| V-10 | General exact-call duplication | 9 excess calls in automation runs; 2 minor human-root repeats | pass: not primary cost |
| V-11 | Automation behavioral spend | 611 model responses; 870 tools; 27.64M terminal-run tokens | concern |
| V-12 | No-progress stop | defaults: 200 iterations, no tool/wall ceiling; no structured no-progress rule | fail |
| V-13 | Search payload bound | 109 recent results / 269.1 MiB raw; max 52.6 MB | fail |
| V-14 | Raw-result expiry | 908 manifests; all `expires_at=NULL` | fail |
| V-15 | Child performance attribution | child events retain child fields but overload parent `run_id` | fail |
| V-16 | Five comparison repositories | revisions and agent-facing search surfaces verified in `search-tool-comparison.md` | pass |
| V-17 | Cross-harness producer bound | all comparisons bound some layer; post-hoc-only designs still buffer 10–20 MB | concern |
| V-18 | Arden prompt/continuation parity | no modes, cursor, per-line/byte limit, or scope-first workflow | fail |
| V-19 | Arden aggregate model projection | newest full tool results bounded to 80k chars; older results stubbed | pass |
| V-20 | Wiki Maintenance no-progress trace | 4 successful reviews, then 14 opaque `internal_error` + 2 rejected decisions after one terminal generated-region conflict | fail |
| V-21 | Stateful completion correction | `helpful-swallow`: 24 decision-required states, completion on call 25; no repeated completion | pass |
| V-22 | Multi-tool preload surface | backend supports comma/names; native prompt advertises singular; 0/212 trace calls used multi-select | fail |
| V-23 | Recent per-step call distribution | 1,851 tool-bearing steps; observed maximum 9; no step above proposed limit 10 | pass |
| V-24 | Five-harness fan-out comparison | no general per-response cap in any; execution controls vary and do not replace whole-run budgets | pass |
| V-25 | Durable cascade | stored edges/completion/suspension exist, but descendant traversal still starts from in-memory registry and timed-out child tools leaked until restart | fail |
| V-26 | Desktop full suite | `1049 passed` | pass |
| V-27 | Server full suite | `2520 passed` | pass |
| V-28 | Server lint | `uv run ruff check .` | pass |
| V-29 | Desktop typecheck | `tsc --noEmit` | pass |
| V-30 | Desktop build | Vite production build completed; existing CSS Highlight/chunk-size warnings remain | pass with warnings |
| V-31 | Search serialized payload | producer payload `<=256000` bytes, including structured data and discovery evidence | pass |
| V-32 | Fan-out/no-progress | 11-call batch rejected atomically; six active max; three terminal failure-only steps stop | pass |
| V-33 | Durable cascade foundation | three-level stored subtree, idempotent cancellation, preserved restart cause, atomic parent suspension settlement | pass |
| V-34 | Late descendant cancellation | start registration inherits the owning session's durable cancellation and prevents child execution | pass |
| V-35 | Restart cancellation settlement | `cancel_requested` child becomes cancelled and resolves its awaited parent suspension; interrupted work remains eligible for bounded respawn | pass |
| V-36 | Search branch/input bounds | oversized query is rejected; every response branch remains `<=256000` serialized bytes | pass |
| V-37 | File discovery evidence | successful create/replace/edit records the exact path; immediate read bypasses the miss gate | pass |
| V-38 | Missing-path inspection | inaccessible ancestor returns typed `permission_denied` rather than throwing during candidate enrichment | pass |
| V-39 | Explicit conflict domains | filesystem mutations serialize together; unrelated internal resources remain parallel; external tools remain provider-grouped | pass |

## Commands and observations

Primary read-only database checks used `sqlite3 -readonly /Users/escept1co/.arden/sessions.db` over:

- `automation_runs JOIN scheduled_tasks` for counts, durations, errors, and live builtin state;
- `chat_runs JOIN sessions` for recent human/channel outcomes;
- `background_agent_runs`, `session_events`, and `tool_invocations` for timeout settlement;
- `outbox_events` for status, attempts, age, and dead history;
- `sqlite_master` / `PRAGMA table_info` for FTS schema and triggers.

Representative queries:

```sql
SELECT a.builtin,a.task_id,a.name,r.status,count(*)
FROM automation_runs r JOIN scheduled_tasks a USING(task_id)
WHERE r.started_at >= '2026-07-30T00:00:00+00:00'
  AND r.started_at < '2026-08-04T00:00:00+00:00'
GROUP BY a.builtin,a.task_id,a.name,r.status;
```

```sql
SELECT session_id,run_id,tool_name,status,error_code,created_at,updated_at
FROM tool_invocations
WHERE status='uncertain' AND error_code='server_restart'
  AND created_at >= '2026-07-30T00:00:00';
```

```sql
SELECT event_type,created_at,run_id,
       json_extract(event_json,'$.messages_before'),
       json_extract(event_json,'$.messages_after')
FROM session_events
WHERE session_id='20260720_094827_005'
  AND event_type LIKE 'compaction_%';
```

Behavioral checks:

```sql
-- Same arguments repeated inside one automation run.
WITH automation_chat_runs AS (
  SELECT run_id
  FROM chat_runs
  WHERE started_at >= '2026-07-30T00:00:00+00:00'
    AND started_at < '2026-08-04T00:00:00+00:00'
    AND json_extract(metadata_json,'$.automation_id') IS NOT NULL
)
SELECT tool_name,args_hash,run_id,count(*)
FROM tool_calls JOIN automation_chat_runs USING(run_id)
WHERE args_hash IS NOT NULL
GROUP BY tool_name,args_hash,run_id
HAVING count(*) > 1;
```

```sql
SELECT tool_name,count(*) results,
       sum(content_bytes) raw_bytes,max(content_bytes) max_bytes,
       sum(expires_at IS NULL) without_expiry
FROM tool_results
GROUP BY tool_name
ORDER BY raw_bytes DESC;
```

```sql
WITH batches AS (
  SELECT json_array_length(json_extract(message_json,'$.tool_calls')) AS n
  FROM session_messages
  WHERE role='assistant'
    AND created_at >= '2026-07-30T00:00:00+00:00'
    AND created_at < '2026-08-04T00:00:00+00:00'
    AND json_type(message_json,'$.tool_calls')='array'
)
SELECT n,count(*) FROM batches GROUP BY n ORDER BY n;
-- 1..9 calls; maximum 9
```

```sql
-- Native tool-search selections in the same window:
-- 212 calls total; zero query values matched select:%,%.
```

Focused current-code verification:

```text
cd apps/server
uv run pytest tests/test_transcript_search.py tests/test_wiki_maintenance_updates.py tests/test_scheduler_loops.py -q
110 passed in 8.18s
```

Post-review regression verification:

```text
cd apps/server
uv run pytest -q
2520 passed in 161.89s

cd apps/desktop
bun test
1049 pass, 0 fail

uv run ruff check .
All checks passed

bun run typecheck
passed

bun run build
passed with existing CSS Highlight and chunk-size warnings
```

## Failures and gaps

- Historical cancellation actor/cause cannot be recovered from current durable rows.
- Rotated log filenames provide ordering, but individual log entries lack a date.
- Full causal cascade coverage for timeout/supersede/automation/shutdown remains open.
- Compaction and model/context amplification remain explicitly deferred.
- `ruff format --check .` still reports pre-existing formatting drift in `arden/storage_budget.py` and unrelated lines in large touched files; lint passes.

## Outcome

The adopted search, behavioral-budget, preload, path-recovery, maintenance, retention, and cascade-foundation changes are implemented and verified. Remaining proposals stay explicit and unchecked.
