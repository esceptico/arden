# Server-Client Agent Transport Reliability Notes

Date: 2026-05-22

## Running Notes

- Starting implementation from `docs/internal/server-client-agent-transport-reliability-plan.md`.
- Repo was already dirty with many unrelated memory/UI/tooling changes. This pass stayed scoped to transport/reliability files, tests, and this notes doc.
- Decision: use the existing SQLite `SessionStore` / `session_events` path as the durable stream source instead of adding Redis. This matches the plan recommendation for the local/single-user app.
- Decision: keep the in-memory `RunRegistry` client-id map as a same-process hot cache for now, but durable idempotency is now the source of truth.
- Decision: implement structured error fields additively (`code`, `debug_id`, durable `error_code`/`error_message`) so existing desktop code that only reads `message` keeps working.

## Implementation Decisions / Deviations

- Durable chat idempotency is keyed by `(session_id, client_id)` with a stored `request_hash`.
  - Same body + same `client_id` returns the existing run state instead of re-enqueuing or spawning duplicate work.
  - Same `client_id` + different body returns HTTP `409`.
  - Retention is now bounded: new claims get a 30-day expiry, terminal updates extend expiry to 30 days from terminal transition, and server startup prunes only expired terminal rows (`completed`, `cancelled`, `error`, `failed`, `interrupted`).
  - Non-terminal rows are never pruned by expiry cleanup, even if an old expiry is present, so in-flight/recovered runs are not accidentally forgotten.
- Event persistence is implemented as append-before-publish through `SessionBus(record_event=...)`.
  - `SessionBus.emit()` now assigns a seq, writes to `session_events`, then publishes that exact durable seq to live subscribers.
  - If persistence fails, publish is aborted instead of emitting an event that cannot be replayed.
- Stream replay now prefers durable DB replay via `list_session_events(session_id, after_seq)`.
  - In-memory replay remains a fallback for tests/non-persistent buses.
  - `Last-Event-ID` is supported and combined with query `after_seq` using the max/effective cursor.
  - `STREAM_RESET` is emitted for future cursors or unreplayable gaps; desktop blocks tail mutation until canonical history reload clears the reset.
- Run lifecycle is now mirrored into durable `chat_runs`.
  - Startup marks stale `pending/running` rows as `interrupted` and marks queued messages from those runs as `failed_retryable`.
  - Chat status API can recover active/interrupted state from SQLite when the in-memory registry is empty after restart.
- Queued message ledger is now tied into active-run injection.
  - Active run POSTs with a `client_id` write a durable queued row before enqueueing into the in-memory run.
  - Ingestion marks queued rows as ingested with the ingested event seq.
- Reconnect behavior is client-side exponential backoff with jitter.
  - Defaults: 500ms base, exponential, capped at 15s, ±20% jitter.
  - Successful events reset the retry attempt counter.
  - Transport diagnostics track reconnect cursor, phase, errors, close reason, and reset reasons.
- Slow subscriber handling: kept the existing bounded queue behavior but now tries to enqueue a structured `stream_reset` with reason `slow_consumer` before dropping the subscriber. Durable replay should recover after reconnect.
- Automation queued-event terminal failure now dead-letters instead of silently deleting.
  - Added `automation_event_dead_letter` and status counts.
  - On max retries the scheduler moves the event to the dead-letter table and removes it from the active queue.
- Schema/codegen phase: added an explicit drift test that compares Python backend `EventType` literals against desktop TypeScript event unions (`api.ts` + automation events). Also filled missing desktop union cases for `question` and `run_backgrounded`.
  - I still did not introduce generated schemas; the practical drift failure mode is now caught in CI without adding a bigger codegen toolchain.
- Live fanout boundary: kept the `SessionBus` live subscriber list process-local and documented that the supported server mode is a single uvicorn worker. The CLI already starts uvicorn without multi-worker fanout. Cross-process/shared pub-sub is intentionally not half-added; durable SQLite replay remains the correctness layer after reconnect/restart.

## Active Session Runtime Snapshot

- Added the missing durable snapshot layer so active-session open is now `history/state snapshot → explicit event cursor → SSE delta`, instead of making event replay reconstruct the whole intermediate UI.
- `/session/history` now includes `runtime` with:
  - `latest_event_seq`,
  - `checkpoint_seq`,
  - `active_run` (`run_id`, status, timestamps, seqs, stop/error fields),
  - durable `pending_approvals`,
  - durable `queued_messages`.
- Added `GET /sessions/{session_id}/state` for clients that need just the runtime snapshot without transcript history.
- `/sessions` now carries sidebar runtime fields: `active_run_id`, `run_status`, `checkpoint_seq`, `latest_event_seq`, `is_active`, pending approval / queued message counts, and stop/error markers. This gives the sidebar grounded state from the list response instead of pure polling inference.
- Desktop history load hydrates pending approvals and queued messages from the runtime snapshot, marks active/backgrounded runs as active, and seeds the stream cursor from `checkpoint_seq`; the next SSE connection sends `after_seq=<checkpoint_seq>` explicitly and only applies durable delta events.
- Durable replay remains the correctness layer for post-snapshot deltas and reconnects; it is no longer the only mechanism for reconstructing active UI state.

## Verification Performed

- Earlier full pass: server focused suites → 135 passed; server full suite → 712 passed; desktop full tests → 157 passed; desktop typecheck → passed.
- Follow-up focused server suites: `pytest apps/server/tests/test_session_bus.py apps/server/tests/test_session_store.py apps/server/tests/test_chat_inject.py apps/server/tests/test_event_contract.py apps/server/tests/test_chat_runs_status_api.py apps/server/tests/test_run_state.py apps/server/tests/test_automation_store.py -q` → 137 passed.
- Follow-up server full suite: `pytest apps/server/tests -q` → 714 passed.
- Follow-up server lint: `ruff check apps/server/arden/context/store.py apps/server/arden/server/stores.py apps/server/arden/server/bus.py apps/server/tests/test_session_store.py apps/server/tests/test_event_contract.py` → passed.
- Follow-up desktop full tests: `bun test apps/desktop/tests` → 157 passed.
- Follow-up desktop typecheck: `bun run typecheck` from `apps/desktop` → passed.

## Letta GitHub Client Architecture Spot Check

- Checked `letta-ai/letta-oss-ui`: it is an Electron + Vite/React desktop demo built on `@letta-ai/letta-code-sdk`; Electron main broadcasts `server-event` IPC messages to renderer windows, the renderer uses a small IPC hook and Zustand store, and the runner uses SDK sessions that stream JSON from a Letta Code subprocess.
- Checked `letta-ai/letta-code-sdk`: the SDK wraps the Letta Code CLI as a subprocess with `--output-format stream-json --input-format stream-json`, exposes `createSession` / `resumeSession`, and contains its own stream generation filtering and bounded stream buffering.
- Checked `letta-ai/letta-typescript-web-clients`: the web-client packages are thin Next.js/React helpers around the Letta API, with Next middleware proxying `/v1/*` to the Letta backend and an optional identity plugin. This is useful app-auth/proxy precedent but not a desktop transport model.
- Checked Letta Code docs/README: official desktop/browser/channel surfaces exist, and the current architecture centers on long-lived agents plus conversations/sessions. For our current reliability work, the most relevant takeaway is not shared live pub/sub; it is the same split we already chose: durable server-side state plus process-local live streams with client recovery/replay semantics.

## Explicit Boundaries / Non-goals

- Generated Python→TypeScript event codegen is not required for this reliability pass. Backend-vs-desktop event literal drift now fails a focused contract test, which closes the practical drift issue without adding a broader codegen toolchain.
- Multi-process server live fanout is intentionally unsupported unless a real shared pub/sub layer is added. For the current local/single-worker app, this is a documented hard boundary rather than an accidental caveat; durable SQLite replay remains the recovery/correctness layer.
