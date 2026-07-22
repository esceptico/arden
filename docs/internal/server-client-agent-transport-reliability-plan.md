# Server-Client Agent Transport Reliability Plan

Date: 2026-05-22

Status: execution plan

Scope: chat/session agent runs, server-to-client event delivery, desktop reconnection, idempotent message submission, durable replay, run recovery, and typed event contract hygiene.

This plan comes from a comparison of the current `arden` server/client transport with Letta, Vercel AI SDK UI, AG-UI, LangGraph, and OpenAI Agents SDK. The immediate goal is to make server <-> client transport reliable under disconnects, retries, process restarts, slow consumers, and ambiguous POST failures.

## Executive Summary

The current transport is a decent SSE-based design, but it sits halfway between a live feed and a durable stream. That middle zone creates the exact reliability bugs we are seeing.

Current architecture:

- Server sends chat events over SSE.
- `SessionBus` assigns per-session monotonic `seq` values.
- Desktop tracks cursors and drops stale events.
- Server has an in-memory replay buffer.
- Server persists `session_events`, but mostly uses it as a cursor/checkpoint ledger rather than as the replay source.
- `client_id`/OTID-style dedupe exists, but only in memory and only for 30 seconds.
- Active runs and queued injections are mostly in memory.

Target architecture:

- Agent run production is independent from client stream connections.
- POST starts or attaches to a durable run.
- Events are appended to a durable stream before being published to live subscribers.
- GET/SSE connections are readers with cursors.
- Disconnect does not cancel work.
- Explicit cancel endpoint cancels work.
- Duplicate POST with the same client action id reattaches instead of duplicating the run/message.
- If replay cannot be served, the server emits `stream_reset` and the client reloads canonical history.

The three highest-value fixes are:

1. Durable idempotency for `client_id` / OTID.
2. Append event before publish and replay from `session_events`.
3. Durable run status plus startup reconciliation.

## Repositories And Source Areas

Current code was inspected as:

- Repo root: `/Users/escept1co/src/arden`
- Server: `/Users/escept1co/src/arden/apps/server`
- Desktop: `/Users/escept1co/src/arden/apps/desktop`

Key current files:

- `apps/server/arden/events/sse.py` — public SSE event vocabulary and adapter from internal agent events.
- `apps/server/arden/server/bus.py` — per-session live event bus, `seq` assignment, in-memory replay buffer.
- `apps/server/arden/server/routers/chat.py` — `/chat/events/{session_id}` SSE endpoint.
- `apps/server/arden/server/stream.py` — agent loop to SSE bridge.
- `apps/server/arden/server/state.py` — in-memory run registry and short-lived OTID dedupe.
- `apps/server/arden/context/store.py` — `session_events`, chat run/message persistence, queued messages.
- `apps/server/arden/services/chat.py` — chat submit/run/checkpoint lifecycle.
- `apps/desktop/src/hooks/useEvents.ts` — desktop chat SSE loop and reconnect behavior.
- `apps/desktop/electron/main.cjs` — Electron SSE stream bridge.
- `apps/desktop/src/api.ts` — TypeScript event union.
- `apps/desktop/src/store/chat-stream.ts` — cursor/replay-gap/projection coordination.
- `apps/desktop/src/store/transcript-projection.ts` — visible transcript projection from events/history.

Related existing docs:

- `docs/internal/event-streaming-audit.md`
- `docs/internal/event-streaming-implementation-notes.md`
- `docs/superpowers/plans/2026-05-09-event-system-next.md`

## Prior Art Summary

### Letta

Local source: `/Users/escept1co/src/letta`

Relevant files:

- `letta/server/rest_api/routers/v1/agents.py`
- `letta/services/streaming_service.py`
- `letta/server/rest_api/redis_stream_manager.py`
- `letta/server/rest_api/streaming_response.py`
- `letta/schemas/letta_request.py`
- `letta/schemas/letta_response.py`
- `letta/schemas/letta_message.py`

Useful patterns:

- FastAPI HTTP API with SSE for streaming responses.
- Robust mode uses background streams written to Redis.
- Stream recovery uses `run_id`, `otid`, and `starting_after` cursor.
- OTIDs dedupe duplicate requests and let retries attach to an existing run.
- Redis stream chunks have `seq_id` and TTL.
- Pings include `run_id` and last `seq_id`.
- Mid-stream errors are converted into typed error events and terminal `[DONE]`.
- Background processor defensively appends terminal `[DONE]`.

Main lesson: split run production from stream reading. The client connection is just a reader; the run continues independently.

Caveats:

- Foreground SSE is still connection-bound.
- The robust path is background + Redis.
- Native SSE `Last-Event-ID` support was not found; Letta uses app-level cursors.

### Vercel AI SDK UI

Sources:

- `https://github.com/vercel/ai`
- `content/docs/04-ai-sdk-ui/50-stream-protocol.mdx`
- `content/docs/04-ai-sdk-ui/03-chatbot-resume-streams.mdx`

Useful patterns:

- HTTP/SSE stream protocol with typed UI message parts.
- Resumability is application-owned: persist messages, active stream ids, and stream data.
- GET endpoint resumes an active stream.
- If no active stream exists, return `204`.
- Client-side disconnect/abort is explicitly not the same as stopping generation.
- Explicit stop endpoint is separate from stream disconnect.

Main lesson: browser connection lifetime must not define agent run lifetime.

### AG-UI

Sources:

- `https://github.com/ag-ui-protocol/ag-ui`
- `https://docs.ag-ui.com/sdk/js/core/events`

Useful patterns:

- Open event protocol for agent-to-frontend communication.
- Transport-agnostic: works over SSE, WebSocket, webhooks, etc.
- Canonical typed events:
  - `RUN_STARTED`
  - `RUN_FINISHED`
  - `RUN_ERROR`
  - `TEXT_MESSAGE_START`
  - `TEXT_MESSAGE_CONTENT`
  - `TEXT_MESSAGE_END`
  - `TOOL_CALL_START`
  - `TOOL_CALL_ARGS`
  - `TOOL_CALL_END`
  - `TOOL_CALL_RESULT`
  - `STATE_SNAPSHOT`
  - `STATE_DELTA`
  - `MESSAGES_SNAPSHOT`

Main lesson: use AG-UI-like typed event vocabulary, but add our own durable replay semantics. AG-UI is protocol shape, not a persistence strategy.

### LangGraph

Sources:

- `https://github.com/langchain-ai/langgraph`
- `https://docs.langchain.com/oss/python/langgraph/overview`
- `https://docs.langchain.com/oss/python/langgraph/persistence`
- `https://docs.langchain.com/oss/python/langgraph/durable-execution`

Useful patterns:

- Durable execution for long-running stateful agents.
- Threads/checkpoints below the streaming transport.
- Resume/interrupt/time-travel are graph/runtime concerns, not just UI stream concerns.

Main lesson: agent execution state should be durable below transport. Stream loss should be a UI/replay issue, not a run-corruption issue.

### OpenAI Agents SDK Python

Local source: `/Users/escept1co/src/openai-agents-python`

Relevant files:

- `src/agents/stream_events.py`
- `src/agents/result.py`
- `docs/streaming.md`

Useful patterns:

- Clean split between raw model events, semantic run item events, and agent-updated events.
- Queue-backed event iterator.
- Explicit cancellation clears streaming tasks and queues.

Main lesson: expose semantic run/tool/message events publicly; keep raw/provider events optional/debug-only.

## Current System Strengths

The current code has a good base:

- SSE transport is simple and appropriate; no immediate need to migrate to WebSockets.
- Per-session monotonic `seq` exists.
- SSE frames already use native `id: {seq}`.
- Desktop tracks event cursor and drops stale events.
- `stream_reset` exists for replay-gap recovery.
- Keepalives exist.
- `session_events` table exists.
- `chat_runs.last_seq` / checkpoint watermark exists.
- Event model is already AG-UI-inspired.
- Server has tests around `SessionBus` replay and replay-gap behavior.

The problem is not the basic direction. The problem is that the implementation is only partially durable.

## Findings And Fixes

### P0. Durable Idempotency For Chat Submit

Current evidence:

- `apps/server/arden/server/state.py`
- `OTID_DEDUP_TTL = timedelta(seconds=30)`
- `RunRegistry._otid_runs` is in memory.
- `apps/server/arden/services/chat.py` checks `run_registry.lookup_otid(session_id, client_id)` before creating/injecting work.
- `apps/desktop/src/actions/messages.ts` sends `client_id` from the user message id.

Problem:

A duplicate POST after server restart, process crash, laptop sleep, network ambiguity, or more than 30 seconds can create duplicate user turns or duplicate runs.

Target behavior:

- `client_id` is a durable idempotency key.
- Duplicate POST with same `(session_id, client_id, request_hash)` returns the existing run/message result.
- Same `(session_id, client_id)` with a different request body returns `409 Conflict`.
- Idempotency TTL should be hours/days, not 30 seconds.

Proposed schema:

```sql
CREATE TABLE chat_idempotency_keys (
  session_id   TEXT NOT NULL,
  client_id    TEXT NOT NULL,
  request_hash TEXT NOT NULL,
  run_id       TEXT,
  message_id   TEXT,
  status       TEXT NOT NULL,
  created_at   TEXT NOT NULL,
  updated_at   TEXT NOT NULL,
  expires_at   TEXT,
  PRIMARY KEY (session_id, client_id)
);

CREATE INDEX chat_idempotency_run_idx
  ON chat_idempotency_keys(run_id);
```

Status values:

- `accepted`
- `queued`
- `running`
- `completed`
- `failed`
- `cancelled`

Implementation notes:

1. Compute `request_hash` from stable fields only:
   - session id
   - client id
   - text/content
   - attachments/tool references if any
   - selected mode/options that affect execution
2. Insert idempotency row before creating a new run or queue entry.
3. If row already exists:
   - matching hash: return existing `{run_id, message_id, status}`
   - mismatched hash: raise `409`
4. Update row when run/message status changes.
5. Keep the in-memory registry as a hot cache only, not source of truth.

Acceptance tests:

- Duplicate POST within same process returns same run id.
- Duplicate POST after process restart returns same run id/message id.
- Duplicate POST after >30s returns same run id/message id.
- Same client id with different text returns `409`.
- Duplicate queued injection does not create another queued row.

### P0. Append Event Before Publish

Current evidence:

- `apps/server/arden/server/bus.py::SessionBus.emit()` assigns seq, appends to `_recent`, pushes to subscriber queues, then schedules persistence asynchronously.
- Persistence failure is logged but does not fail live delivery.

Problem:

A client can observe `seq=N`, then the server can crash before `session_events` commits. On reconnect, the client cursor points at an event the durable system cannot account for.

Target behavior:

For reliability-sensitive chat/session events:

1. assign seq
2. append event to durable store
3. commit
4. add to hot replay buffer
5. publish to live subscribers

Implementation options:

#### Option A: direct append-before-publish

Simpler and likely good enough for local SQLite:

```txt
agent event
  -> SessionBus.emit()
  -> assign seq
  -> await event_store.record_session_event(...)
  -> append to _recent
  -> fan out to subscribers
```

#### Option B: transactional outbox

More robust but more work:

```txt
transaction:
  insert event log row
  insert outbox row
commit
publisher dispatches pending outbox rows
```

Recommendation:

Start with Option A for chat/session events. Revisit outbox only if write latency becomes a problem.

Important detail:

Synthetic events generated inside the SSE endpoint, such as `stream_reset`, do not necessarily need to be durable domain events. They can remain control frames unless we want diagnostics.

Acceptance tests:

- Event persistence failure prevents publishing the event.
- Client never receives a seq that is absent from `session_events`.
- Reconnect after crash can replay all committed events after cursor.

### P0. Replay From Durable `session_events`

Current evidence:

- `apps/server/arden/context/store.py::list_session_events()` can list persisted events after a cursor.
- `/chat/events/{session_id}` currently uses in-memory `bus.subscribe_with_replay(after_seq)`.
- On bus recreation, server seeds latest seq/checkpoint from DB but does not hydrate/replay the durable event log.

Problem:

After restart, the server can know the latest cursor but still cannot serve replay from memory. Client often gets `stream_reset` even when `session_events` has the needed events.

Target behavior:

Reconnect flow:

```txt
client connects with after_seq / Last-Event-ID
server resolves effective cursor
server replays durable session_events after cursor when available
server attaches to live queue atomically enough to avoid gaps
if durable replay is unavailable or unsafe, emit stream_reset
```

Implementation notes:

1. Add a store-backed replay path in `chat.py` before or inside bus subscription.
2. Keep `SessionBus._recent` as hot cache for same-process reconnects.
3. For cold replay after restart, use `list_session_events(session_id, after_seq, limit)`.
4. Avoid missing events between DB replay and live subscription:
   - subscribe to bus first, snapshot live next seq, then replay DB up to that boundary, then drain live queue; or
   - hold a bus/registry lock while attaching and computing replay boundary.
5. If `after_seq` is below a compaction/checkpoint boundary where raw events are no longer sufficient, emit `stream_reset` and force canonical history reload.

Acceptance tests:

- Client reconnect after server restart receives events from `session_events`, not just `stream_reset`.
- Replay is ordered by seq.
- No duplicate events when DB replay overlaps with live buffer.
- Old cursor below checkpoint produces `stream_reset`.
- Future cursor produces `stream_reset` or clear cursor error.

### P0. Durable Run Records And Startup Reconciliation

Current evidence:

- `apps/server/arden/server/state.py::RunRegistry` stores active runs in memory.
- `_runs`, `_active_by_session`, and `_otid_runs` disappear on process restart.
- `chat_runs.last_seq` exists but run lifecycle is not a complete durable active-run source of truth.

Problem:

If the server restarts during an active run, the UI can reload history but cannot reliably know whether the run completed, failed, was interrupted, or should be resumed.

Target behavior:

Persist every run lifecycle transition.

Proposed schema/table extension:

```sql
CREATE TABLE chat_runs (
  run_id       TEXT PRIMARY KEY,
  session_id   TEXT NOT NULL,
  status       TEXT NOT NULL,
  client_id    TEXT,
  started_at   TEXT,
  updated_at   TEXT NOT NULL,
  completed_at TEXT,
  last_seq     INTEGER,
  error_code   TEXT,
  error_message TEXT
);

CREATE INDEX chat_runs_session_status_idx
  ON chat_runs(session_id, status);
```

If `chat_runs` already exists, extend it instead of replacing it.

Status values:

- `queued`
- `running`
- `completed`
- `failed`
- `cancelled`
- `interrupted`

Startup reconciliation:

1. Find stale `running`/`queued` runs from previous process.
2. Mark them `interrupted` unless true durable execution/resume exists.
3. Emit or synthesize a safe terminal state for UI reload.
4. Clear or recover related queued injections.
5. Ensure sessions are not locked forever by dead runs.

Acceptance tests:

- Process restart during run marks run `interrupted`.
- Session reload shows no infinite spinner.
- Reconnect after restart gets terminal/interrupted state or canonical history reset.
- New message after interrupted run is accepted.

### P1. Recover Or Fail Persisted Queued Chat Injections

Current evidence:

- Active-run injection uses `RunState.inject_queue`, which is in memory.
- `_record_queued_message()` persists queued messages.
- `list_chat_queued_messages()` exists but appears unused for recovery.

Problem:

If process dies after recording a queued message but before the active run consumes it, DB says queued but the in-memory queue is gone.

Target behavior:

No zombie queued messages.

Recovery policy options:

- Conservative: mark queued messages for missing/interrupted runs as `failed_retryable`.
- Aggressive: requeue them into a new run on session resume.
- UX-first: surface them as unsent/retryable in the client.

Recommendation:

Start conservative. Mark as `failed_retryable` and let the user/client resubmit with the same `client_id`, which durable idempotency can resolve safely.

Acceptance tests:

- Queued message survives process death as a visible retryable state.
- Queued message is not silently lost.
- Queued message is not duplicated into a new active run without explicit policy.

### P1. Support Native SSE `Last-Event-ID`

Current evidence:

- SSE frames already include `id: {seq}`.
- Client mostly passes `after_seq` manually.
- Server does not currently rely on the `Last-Event-ID` header.

Problem:

Native SSE clients automatically send `Last-Event-ID` on reconnect. We should support that instead of only custom query cursors.

Target behavior:

`/chat/events/{session_id}` resolves cursor from both query and header:

```py
query_after_seq = after_seq or 0
header_after_seq = parse_int(request.headers.get("last-event-id")) or 0
effective_after_seq = max(query_after_seq, header_after_seq)
```

Keep `after_seq` for Electron/fetch-based clients.

Acceptance tests:

- Reconnect with only `Last-Event-ID` resumes after that seq.
- Reconnect with both header and query uses the larger safe cursor.
- Invalid `Last-Event-ID` is ignored or treated as `400`; choose one and test it.

### P1. Better Reconnect Backoff And Transport State

Current evidence:

- `apps/desktop/src/hooks/useEvents.ts` uses fixed-ish 1500ms reconnect delay.
- `apps/desktop/src/hooks/useAutomationEvents.ts` similarly uses fixed sleep.

Problem:

Server flap, auth failure, or laptop wake can create repeated synchronized reconnect loops and poor diagnostics.

Target behavior:

Use capped exponential backoff with jitter:

```txt
500ms -> 1s -> 2s -> 5s -> 10s -> 15s max
±20% jitter
reset after successful event or sustained connection
```

Track client transport states:

- `connecting`
- `connected`
- `replaying`
- `reconnecting`
- `resetting`
- `failed`

Acceptance tests:

- Backoff increases after consecutive failures.
- Backoff resets after successful event receipt.
- Abort/unmount does not schedule reconnect.
- Replay-gap state is visible and clears after history reload.

### P1. Explicit Slow-Consumer Handling

Current evidence:

- `SSE_QUEUE_MAXSIZE = 256`.
- Queue overflow closes slow subscriber.
- SSE loop breaks when queue gets `None`.

Problem:

The client sees EOF with no explicit cause. Diagnostics lose the reason.

Target behavior:

Before closing a slow subscriber, attempt to send a typed control event:

```json
{
  "type": "stream_reset",
  "reason": "slow_consumer",
  "seq": 123
}
```

If the queue is already full, log structured reason and rely on reconnect.

Acceptance tests:

- Slow subscriber closure logs `slow_consumer`.
- Client reconnects with last seq.
- If replay is available, client catches up.
- If replay is unavailable, client receives `stream_reset` and reloads.

### P1. Structured Error Surfaces

Current evidence:

- Some HTTP paths return `detail=str(e)`.
- Some SSE paths emit `RunErrorEvent(message=str(e))`.

Problem:

Raw exception strings leak internals and give the client no stable handling semantics.

Target behavior:

Use structured safe errors:

```json
{
  "type": "RUN_ERROR",
  "code": "tool_failed",
  "message": "Tool execution failed.",
  "recoverable": false,
  "debug_id": "err_..."
}
```

Raw exception details stay in server logs with `debug_id`.

Suggested codes:

- `llm_timeout`
- `llm_rate_limited`
- `tool_failed`
- `approval_required`
- `cancelled`
- `transport_error`
- `internal_error`
- `run_interrupted`
- `idempotency_conflict`

Acceptance tests:

- Client receives stable error code.
- Raw filesystem paths/provider stack traces are not shown in UI.
- Server logs include `debug_id` and original exception.

### P1. Resolve Session Write Race

Current evidence:

`apps/server/arden/server/app.py` has a TODO to extend lock acquisition into `submit_chat_message` to fully serialize session-message writes and cover residual post-vs-chat race during agent run.

Problem:

Concurrent POST/write paths can interleave with active run persistence and cause inconsistent session state.

Target behavior:

For each session, choose and enforce one concurrency model:

1. Reject concurrent run.
2. Queue user message into active run.
3. Cancel/replace active run.
4. Fork/branch thread.

Current behavior is closest to option 2. Make it explicit and serialize writes accordingly.

Acceptance tests:

- Concurrent POSTs with unique client ids are ordered deterministically.
- Duplicate POSTs are deduped.
- Active-run injection cannot race with checkpoint save.
- Session history remains valid after concurrent submit stress test.

### P2. Single Source Of Truth For Event Schemas

Current evidence:

- Server event definitions live in `apps/server/arden/events/sse.py`.
- Client event union lives in `apps/desktop/src/api.ts`.
- Automation events have separate inline typing.

Problem:

Wire schema can drift silently. Python dataclasses are not a generated TS contract, and the desktop casts parsed JSON into types.

Target behavior:

Generate TypeScript event types from server-side schemas, or define a shared schema and generate both sides.

Options:

1. Pydantic models -> JSON Schema -> TS types.
2. OpenAPI schema -> generated client/types.
3. Shared schema DSL -> Python + TS generation.

Also add runtime validation on the desktop before reducing events.

Acceptance tests:

- Missing required event field fails validation in tests.
- New server event type requires TS handling.
- Unknown custom event type is handled safely.

### P2. Align Event Vocabulary With AG-UI

Current evidence:

The code already uses AG-UI-ish names for run/text/tool/reasoning events, plus arden-specific events.

Target behavior:

- Keep canonical AG-UI names where possible.
- Put arden-specific events under a clear custom/control namespace or payload convention.
- Preserve the distinction documented in `events/sse.py`:
  - `seq` is transport cursor
  - `event_id` is domain idempotency
  - `message_id`, `tool_call_id`, `run_id` are domain object ids

Acceptance tests:

- Chat transcript events map cleanly to AG-UI lifecycle/text/tool events.
- arden-specific events do not collide with AG-UI canonical names.

### P2. Automation Event Dead-Lettering

Current evidence:

- Automation queued events have retry/backoff.
- After max retries, failed events are completed/dropped and only logged.

Problem:

Operators cannot inspect or replay dropped automation events except from logs.

Target behavior:

Add dead-letter state/table:

- `dead`
- `last_error`
- `attempt_count`
- `context`
- `failed_at`
- optional replay/admin API

Acceptance tests:

- Event exceeding max retries becomes `dead`, not deleted as completed.
- Dead event is visible in scheduler status/debug API.
- Dead event can be manually replayed or explicitly discarded.

## Target Architecture

### Server Submit Flow

```txt
POST /chat/message
  -> validate request
  -> compute request_hash
  -> durable idempotency lookup/insert
  -> acquire session write lock
  -> if active run: persist queued injection and queue in memory
  -> else: create durable run row
  -> persist user message/progress
  -> return { session_id, run_id, message_id, status }
```

### Agent Event Flow

```txt
agent internal event
  -> convert to SSE/domain event
  -> assign seq
  -> append to session_events
  -> update run last_seq if needed
  -> append to hot replay buffer
  -> publish to subscribers
```

### SSE Reconnect Flow

```txt
GET /chat/events/{session_id}?after_seq=N
  -> parse Last-Event-ID too
  -> effective_after_seq = max(query, header)
  -> attach live subscription / determine live boundary
  -> replay durable events after cursor up to boundary
  -> stream live events
  -> keepalive with latest seq
```

### Replay Gap Flow

```txt
if cursor is too old, below compaction/checkpoint, future, or otherwise unsafe:
  emit stream_reset { reason }
  client reloads canonical session history
  client resumes live stream after reset/reload
```

### Cancellation Semantics

```txt
SSE disconnect: reader disconnected only; run continues.
POST /chat/runs/{run_id}/cancel: explicit cancellation; run transitions to cancelled.
```

Every run should end with exactly one terminal event:

- `RUN_FINISHED`
- `RUN_ERROR`
- `RUN_CANCELLED`
- `RUN_INTERRUPTED`

## Execution Phases

### Phase 1: Transport Hardening

Goal: remove easiest reliability footguns without large refactor.

Tasks:

1. Add native `Last-Event-ID` support.
2. Add exponential reconnect backoff with jitter.
3. Add explicit slow-consumer/reset reasons.
4. Add structured run/transport error codes.
5. Ensure terminal run event invariants are tested.
6. Add basic transport diagnostics/logging.

Expected impact:

- Better reconnect behavior.
- Better diagnostics.
- No semantic change to persistence yet.

### Phase 2: Durable Idempotency

Goal: duplicate POSTs never duplicate work.

Tasks:

1. Add `chat_idempotency_keys` table or equivalent extension.
2. Compute stable `request_hash`.
3. Change `submit_chat_message()` to use durable idempotency before creating work.
4. Update idempotency row through run lifecycle.
5. Keep in-memory `_otid_runs` only as cache or remove it.
6. Add duplicate/restart tests.

Expected impact:

- Fixes duplicate user turns from retry/ambiguous network failure.

### Phase 3: Durable Event Replay

Goal: make `session_events` a real replay source.

Tasks:

1. Change event emission to append before publish.
2. Use `list_session_events()` for replay after cursor.
3. Keep bus buffer as hot cache only.
4. Make replay boundary/gap logic explicit.
5. Add crash/restart/reconnect tests.

Expected impact:

- Client can reconnect after process restart and receive committed events when safe.
- No more ghost seqs observed by client but absent on server.

### Phase 4: Durable Run Lifecycle And Queue Recovery

Goal: no zombie active runs or queued messages.

Tasks:

1. Extend/add durable `chat_runs` lifecycle state.
2. Persist run transitions.
3. On startup, mark stale active runs as `interrupted`.
4. Reconcile queued chat messages for missing/interrupted runs.
5. Resolve session write race TODO.
6. Add startup reconciliation tests.

Expected impact:

- No infinite spinners after server restart.
- No silently lost queued injections.
- Session state remains understandable after crashes.

### Phase 5: Schema Hygiene

Goal: prevent Python/TS event contract drift.

Tasks:

1. Choose schema source of truth.
2. Generate TS types.
3. Add runtime event validation in desktop.
4. Align event names/payloads more tightly with AG-UI.
5. Consolidate chat/automation event typing strategy.

Expected impact:

- Safer long-term event evolution.
- Fewer runtime-only UI failures.

### Phase 6: Automation/Background Follow-Up

Goal: apply same durability principles to automation/global event streams.

Tasks:

1. Decide whether automation stream needs durable replay or snapshot-only recovery.
2. Add dead-lettering for automation queued events.
3. Add replay/admin inspection for dead events.
4. Bring automation reconnect/backoff/state handling in line with chat.

Expected impact:

- Better operational recovery and less silent event loss.

## Suggested Task Breakdown

### Task A: `Last-Event-ID` + reconnect hardening

Files likely touched:

- `apps/server/arden/server/routers/chat.py`
- `apps/desktop/src/hooks/useEvents.ts`
- `apps/desktop/electron/main.cjs`
- `apps/desktop/src/store/chat-stream.ts`

Deliverables:

- Server accepts `Last-Event-ID`.
- Desktop uses capped exponential backoff with jitter.
- Transport state is observable in logs/store.

### Task B: durable idempotency

Files likely touched:

- `apps/server/arden/context/store.py`
- `apps/server/arden/services/chat.py`
- `apps/server/arden/server/state.py`
- `apps/server/tests/...`

Deliverables:

- Durable idempotency table.
- Duplicate POST attaches/returns existing run.
- Conflict on same key with different payload.

### Task C: append-before-publish

Files likely touched:

- `apps/server/arden/server/bus.py`
- `apps/server/arden/server/stream.py`
- `apps/server/arden/context/store.py`
- `apps/server/tests/test_session_bus.py`

Deliverables:

- `SessionBus.emit()` awaits durable append before fanout for persisted buses.
- Tests prove no published event is missing from store.

### Task D: durable replay from `session_events`

Files likely touched:

- `apps/server/arden/server/routers/chat.py`
- `apps/server/arden/server/bus.py`
- `apps/server/arden/context/store.py`
- `apps/server/tests/test_chat_inject.py`

Deliverables:

- SSE reconnect can replay from DB after restart.
- Hot buffer remains optimization.
- Replay-gap behavior remains explicit.

### Task E: run lifecycle and startup reconciliation

Files likely touched:

- `apps/server/arden/context/store.py`
- `apps/server/arden/services/chat.py`
- `apps/server/arden/server/app.py`
- `apps/server/arden/server/state.py`

Deliverables:

- Durable run status transitions.
- Stale running runs become interrupted on startup.
- Queued injections reconciled.

### Task F: schema generation/runtime validation

Files likely touched:

- `apps/server/arden/events/sse.py`
- `apps/desktop/src/api.ts`
- `apps/desktop/src/store/transcript-projection.ts`
- build/codegen scripts

Deliverables:

- Generated TS event types or equivalent shared schema.
- Runtime validation before projection.

## Test Matrix

### Idempotency

- Duplicate POST same process returns same run/message.
- Duplicate POST after restart returns same run/message.
- Duplicate POST after current 30s window still dedupes.
- Same key with different payload returns `409`.
- Duplicate queued injection does not duplicate UI row or run work.

### Event durability

- Published event exists in `session_events`.
- Persistence failure prevents publish or emits structured internal error.
- Crash after append but before live publish is replayable.
- Crash after live publish cannot create ghost cursor.

### Replay

- Reconnect after short disconnect replays missed events.
- Reconnect after server restart replays from DB.
- Old cursor below checkpoint emits `stream_reset`.
- Future cursor emits `stream_reset` or deterministic error.
- Replay preserves ordering and does not duplicate already applied seqs.

### Run lifecycle

- Restart during active run marks it `interrupted`.
- UI does not spin forever after interrupted run.
- New message can be sent after interrupted run.
- Queued message during crash becomes retryable or is safely recovered.

### Client transport

- Backoff increases across failures.
- Backoff resets after successful event.
- Component unmount aborts without reconnect loop.
- Slow consumer closes and recovers via replay/reset.

### Error semantics

- Known provider/tool failures map to stable codes.
- Internal exception details are not shown in UI.
- `debug_id` links user-visible error to server logs.

## Open Decisions

1. SQLite vs Redis for durable stream storage.
   - Recommendation: SQLite first, because this is a local/single-user app and `session_events` already exists.
   - Redis is only needed if we need multi-process/high-throughput fanout.

2. Should `session_events` retain all raw events forever?
   - Recommendation: retain by run/session TTL, compact old events after canonical history checkpoint.
   - Keep enough for debugging recent reliability issues.

3. How should stale queued injections recover?
   - Recommendation: conservative `failed_retryable` first.
   - Auto-requeue only after explicit product decision.

4. Should `stream_reset` itself be persisted?
   - Recommendation: no, treat as transport control frame unless needed for diagnostics.

5. Should reconnect use EventSource or fetch streaming?
   - Recommendation: keep existing fetch/Electron path, but support SSE-native `Last-Event-ID` anyway.

6. Should active runs be truly resumable after process restart?
   - Recommendation: not initially. Mark interrupted cleanly first. True durable execution is a larger LangGraph-style runtime concern.

## Non-Goals For This Plan

- WebSocket migration.
- Multi-user/multi-process distributed event bus.
- Full LangGraph-style durable execution engine.
- Rewriting all event vocabulary at once.
- Making every automation/background event durable in the first pass.

SSE is fine. The reliability problem is not SSE; it is missing durable idempotency, durable event replay, and durable run lifecycle.

## Final Recommendation

Execute in this order:

1. Phase 1: `Last-Event-ID`, reconnect backoff, explicit reset/error reasons.
2. Phase 2: durable idempotency.
3. Phase 3: append-before-publish and DB-backed replay.
4. Phase 4: durable run lifecycle and queued injection recovery.
5. Phase 5: generated event schemas and runtime validation.
6. Phase 6: automation dead-lettering/replay cleanup.

If time is tight, do only these first:

1. Durable idempotency.
2. Append event before publish.
3. Replay from `session_events`.
4. Mark stale active runs interrupted on startup.

Those fix the core reliability failures instead of just making reconnect prettier.
