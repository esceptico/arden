# Verification

## Status

Research remains verified. I-01 through I-06 and I-08 are implemented; I-07 is partial; I-09 and I-10 remain.

## Evidence

| ID | Related work | Check | Expected | Observed | Result | Evidence and time |
| --- | --- | --- | --- | --- | --- | --- |
| V-01 | F-04 | Correlate server start, runtime-ready, scheduler, and application-ready log anchors. | Determine real startup latency and automation overlap. | 09:29 start: 54s to runtime / 55s ready. 20:14 start: 58s / 107s. 13:29 start: 127s / 235s; catch-up automations began 3s after scheduler and 104s before readiness. | Pass | `rg -n 'Started server process|Runtime ready|Scheduler started|Application startup complete|Catch-up' ~/.arden/logs/arden.log*`; 2026-08-03 |
| V-02 | F-05 | Measure live SQLite allocation read-only. | Identify dominant tables. | 1,954,904 × 4096-byte pages; 1,017 free pages. `session_events` 5.0 GiB, `session_messages` 1.2 GiB, `outbox_events` 0.7 GiB, `sessions` 0.3 GiB. | Pass | `sqlite3 -readonly ~/.arden/sessions.db 'PRAGMA ...; SELECT ... FROM dbstat ...'`; 2026-08-03 |
| V-03 | F-06 | Find sessions exceeding the 10,000 event policy. | Historical rows should reveal whether retention reconciles. | At least 12 sessions exceed the cap; maximum observed 72,660. | Pass | `SELECT session_id, COUNT(*) ... GROUP BY session_id HAVING n > 10000`; 2026-08-03 |
| V-04 | F-07 | Group outbox bytes by status/type. | Quantify transcript duplication. | Completed `run.completed`: 1,790 rows / 678.3 MiB; dead: 47 / 11.8 MiB. | Pass | `SELECT status,event_type,COUNT(*),SUM(length(payload)) ...`; 2026-08-03 |
| V-05 | F-08 | Inspect live FTS schema and canonical column coverage. | Determine next-boot migration work. | Both `search_text` and legacy `file_search_text` exist; 216,183 rows, zero NULLs in either. | Pass | `.schema session_messages`; `SELECT COUNT(*), SUM(search_text IS NULL), ...`; 2026-08-03 |
| V-06 | F-09 | Explain startup query plans. | Recovery filters should use indexes. | SQLite reports scans for `sessions`, `chat_runs`, and `background_agent_runs`; matching status indexes are absent. | Pass | `EXPLAIN QUERY PLAN ...`; 2026-08-03 |
| V-07 | Scope | Confirm no live process/data mutation. | Research remains read-only. | No Arden/uvicorn process found; no database writes or server startup performed. Only this ledger was created. | Pass | `pgrep -af 'arden|uvicorn|apps/server'`; `git status --short`; 2026-08-03 |
| V-08 | F-12-F-15 | Refresh four local harnesses at current HEAD and measure local footprints read-only. | Compare current persistence, compaction, payload bounds, and cleanup. | Letta `bd06074d` (12 KiB local); Codex `95637f70` (13 GiB total; sessions 4.2 GiB, archives 7.6 GiB; 1,373 plain/0 zstd); Hermes `cb06017b` (140 KiB local); OpenCode `17544802` (396 KiB DB). | Pass | `git rev-parse HEAD`; targeted `rg`/`nl`; `du -sh`; `find ... '*.jsonl.zst'`; 2026-08-03 |
| V-09 | F-16 | Search and read the prior Codex discussion arc. | Recover prior conclusions rather than rediscover them. | Verified the database-space task and its local-tooling/messaging predecessors; all distinguish canonical history from bounded transport replay. | Pass | `list_threads(limit=50)` and `read_thread(turnLimit=10)` for the three task IDs; 2026-08-03 |
| V-10 | F-17-F-18 | Inventory current Arden bytes/config/blob manifests read-only. | Establish quota scope and whether blobs already exist. | 12 GiB total; 7.5 GiB sessions DB; 3.4 GiB explicit archives; 827 tool-result manifests, 386.8 MiB raw / 122.2 MiB gzip, zero expiry values; no total-space setting found. | Pass | `du -sh ~/.arden/*`; `df -h ~/.arden`; targeted `rg`/`nl`; read-only SQLite aggregate; 2026-08-03 |
| V-11 | F-19 | Inspect Harbor ATIF documentation, RFC, Pydantic models, and validator. | Determine archival fidelity, extensibility, and native payload controls. | ATIF-v1.7 models complete trajectories and validates external image paths; it has no native compression, arbitrary blob-ref, truncation, or compact-history profile. | Pass | Official Harbor sources linked in F-19; 2026-08-03 |
| V-12 | F-20 | Inspect Letta Trajectory article, v1 schema, bounds, filters, and adapter contracts. | Determine what produces the claimed compactness. | Strict five-role record schema; harness noise dropped; default 20k argument/2.5k result bounds; optional result omission; no generic extension/blob field. | Pass | Official Letta sources linked in F-20; 2026-08-03 |
| V-13 | F-22-F-26 | Inspect current native export/import and trajectory paths in Letta Code, Codex, Hermes Agent, and OpenCode; search each for ATIF/trajectory adoption. | Determine whether any harness treats normalized trajectories as authoritative cold storage. | Letta alone has first-class trajectory export, as a replaceable `/tmp` memory-review dataset. Codex has native rollouts plus external import; Hermes has full JSONL backup/import plus Markdown; OpenCode has full JSON export/import. None uses ATIF/trajectory as canonical storage. | Pass | Local HEADs: Letta `bd06074d`, Codex `95637f70`, Hermes `cb06017b`, OpenCode `17544802`; targeted `rg`/`nl`; 2026-08-03 |
| V-14 | I-01-I-04 | Start Arden against a fresh temporary data root and inspect phase logs plus `/health`. | API readiness under 2s; warmup observable and independent. | API ready in 763ms; `/health` returned 200 with warmup capabilities and storage status; clean shutdown settled scheduler/workers. | Pass | `ARDEN_DIR=/tmp/arden-startup-benchmark.* uv run arden-server serve --port 16877`; `curl /health`; 2026-08-03 |
| V-15 | I-02-I-08 | Focused server regression suite. | Startup, recovery, outbox, schema, retention, curator, quota, and config tests pass. | 165 passed. | Pass | `uv run pytest` with 10 focused test modules; 2026-08-03 |
| V-16 | I-04/I-08 | Desktop type, lint, and behavior checks. | Warmup banner/polling and storage setting compile without regressions. | Typecheck and ESLint pass; 1,038 tests pass. | Pass | `bun run typecheck`; `bun run lint`; `bun test`; 2026-08-03 |
| V-17 | I-01-I-08 | Full server regression suite. | No server regression. | 2,476 tests passed. | Pass | `cd apps/server && uv run pytest`; 2026-08-03 |

## Failures and gaps

- A live-data cold-start benchmark remains useful because the temporary fresh-data run cannot reproduce the 8 GB database's I/O profile.
- Archive format selection is blocked on the non-destructive I-09 benchmark, not on further conceptual research. The initial role is settled: trajectories are derived until restore/reconstruction proof justifies anything stronger.
- I-07 still needs independent blob expiry and reference-GC proof.

## Outcome

The API/warmup split and bounded transport/storage controls are implemented and verified on temporary data. No live Arden database migration, deletion, or compaction was performed.
