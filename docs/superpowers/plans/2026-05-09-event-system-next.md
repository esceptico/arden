# Event System — Next Phase

Builds on `2026-05-08-stable-event-system.md`. Goal: durable, resumable, multi-tab safe streaming with clean background behavior.

## Current state (May 2026)

Server: SSE on `GET /chat/events/{session_id}?after_seq=N&stream=true`. `SessionBus` assigns monotonic per-session `seq`, keeps a `deque(maxlen=10000)` ring, fans out to subscriber queues, clears buffer at every `on_step_finish` checkpoint and final save (disk + buffer non-overlapping). `BusRegistry` preserves `next_seq` across bus recreation. Cancellation invariant: `TEXT_MESSAGE_END` always before `run_cancelled`. Gap detection emits synthetic `stream_reset` when cursor is stale or in the future.

Desktop: tracks cursor in module-level `Map`, dedupes by seq, handles `stream_reset` by reloading history then replaying queued live events.

Removed client compatibility note: the legacy terminal client no longer exists.

History/live merge: keys mismatch (UUID vs `history-${idx}`), causes remount churn. Five reverted commits at top of branch tried to suppress animations; rolled back.

## What other systems do (research summary)

- **Letta**: Redis Streams as durable buffer, `seq_id` per run, `starting_after` cursor on reconnect, OTID-based duplicate-request recovery (retry attaches as reader instead of starting new run), keepalive carries `last_seq_id`.
- **Vercel `resumable-stream`**: Redis append log + `startIndex` GET reconnect, negative indices for "last N", `x-workflow-run-id` header ties POST to GET.
- **Anthropic/OpenAI public APIs**: SSE with structural `index` on content blocks, no global seq — replay handled at SDK/app layer.
- **LangGraph**: `client.threads.join_stream(thread_id, last_event_id=...)` — cursor-based resume native to SDK.
- **Claude Code (CCR)**: WebSocket with REST history paginator (`anchor_to_latest`, `before_id`), `SubagentStart`/`SubagentStop` first-class hook events, control-plane WS messages for interrupts.
- **Cloudflare DO + WS Hibernation**: durable + multi-subscriber fan-out at edge.

## Priorities (impact × tractability)

(Non-desktop client parity dropped from scope.)

### P1 — Persist event log to SQLite (durability across restart)
Replace pure in-memory ring with append-only SQLite table per session, keyed by `(session_id, seq)`, with TTL by run completion + N hours. Buffer becomes a hot read cache; cold reads from SQLite. Server restart no longer drops in-flight runs. Schema:
```sql
CREATE TABLE event_log (
  session_id TEXT NOT NULL,
  seq        INTEGER NOT NULL,
  run_id     TEXT,
  ts         REAL NOT NULL,
  payload    TEXT NOT NULL,
  PRIMARY KEY (session_id, seq)
);
CREATE INDEX event_log_run ON event_log(run_id);
```
Retention: drop rows where `run_id` is in completed runs older than 24h.

### P2 — Background streaming UX (tab/view switch)
Today desktop unmounts `useEvents` when leaving the chat view → SSE closes → reconnect on return relies on server buffer or `stream_reset`+history. Fix:
- Hoist SSE connection above the chat-view component (hook lives at app root, keyed by active session).
- Keep cursor + buffered live events in store; UI mount just renders from store.
- On view return, no reconnect needed; state already current.

This is the user's stated #1 want ("see sane content: previous done content + streaming irl").

### P3 — Idempotent send via OTID
- Client generates `otid` per message send.
- Server stores `otid → run_id` (in-memory or SQLite). Duplicate POST attaches as reader to existing run rather than creating a new run.
- Solves: network retry storms, double-clicks, mobile reconnects.

### P4 — Keepalive carries last seq (Letta pattern)
SSE comment-frame keepalive currently bare. Add `: seq=<N>` so a client subscribed during a long silence can refresh its cursor without waiting for an event. Zero-cost.

### P5 — History/live key unification
Root cause of reverted animation work. Both history and live should use the same stable key (canonical message id from server, generated at message creation, persisted to SQLite). On `RUN_FINISHED`, no remount because keys match. Removes the need for animation suppression altogether.

### P6 — Sub-agent lifecycle envelope
ARDEN already streams sub-agent task lifecycle. Formalize: `task_started { task_id, parent_run_id, agent_type, prompt_preview }` + `task_finished { task_id, result_preview, status }`. UI gets typed nesting without parsing.

### P7 — Race fix: `BusRegistry.remove()`
The check at `chat.py:87-88` is unguarded. Wrap in registry lock; double-check active run + subscribers under lock before removing.

## Sequencing

1. P4 (keepalive seq) — 15 min, additive.
2. P7 (registry race) — 30 min, surgical.
3. P2 (desktop SSE persistence) — half day, frontend refactor.
4. P3 (OTID) — half day, server + client.
5. P5 (key unification) — 1 day, touches messages everywhere.
6. P1 (SQLite event log) — 1-2 days, biggest design surface.
7. P6 (sub-agent envelope) — pair with whatever sub-agent UI work is next.

## Out of scope for this round
- Redis (single-user app; SQLite is enough).
- WebSocket migration (SSE works, no compelling driver).
- Multi-tab BroadcastChannel coordination (server fan-out via SSE is simpler and works).
