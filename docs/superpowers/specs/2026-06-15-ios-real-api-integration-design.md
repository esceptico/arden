# iOS Real-API Integration — Design

**Date:** 2026-06-15
**Status:** Approved (scope chosen: "everything real" + QR pairing)
**Scope:** Connect the iOS app to the real arden FastAPI server — real chat,
streaming, approvals, cancel, **real workflows/subagents**, **real automations**,
and **QR/pairing** for the API key. Grounded in the API mapping in
`tasks/wawvqdpqz` (server vs iOS client gap analysis).

Artifacts stay a **mock-only demo** (no server `render_html`/artifact SSE event
exists). The mock-data toggle is **kept** as an offline/demo mode.

---

## Server API (verified, source of truth)

- Auth: **Bearer token** (`Authorization: Bearer <key>`) on **every** endpoint
  except `GET /health`. Key generated at `arden-server serve` (hash in
  `runtime.config.api_key_hash`). 401 if missing/blank.
- REST: `GET /health`, `GET/POST /sessions`, `GET /session/history`,
  `POST /chat/message` → `{run_id}`, `GET /chat/events/{session_id}?stream=&after_seq=`
  (SSE), `POST /tools/result`, `POST /cancel`,
  `GET /chat/child-agents/{id}/result?wait=`, `POST /chat/child-agents/{id}/cancel`,
  automations under `server/routers/automation.py`.
- Error bodies: `detail` is often an **object** (`{code, message, ...}`), not a
  string. `409` on `/chat/message` = idempotency conflict (already in flight).
- SSE events (per-session bus): `RUN_STARTED/FINISHED/ERROR`, `run_backgrounded`,
  `TEXT_MESSAGE_START/CONTENT/END`, `REASONING_*`, `TOOL_CALL_START/ARGS/END/RESULT`,
  `approval_needed`, `input_needed`, `token_usage`, `workflow_started/finished`,
  `task_started/progress/finished`, `background_task`, `todo_updated`,
  `goal_updated/cleared`, `stream_reset` (`reason: replay_gap|future_cursor` + seq),
  `stream_keepalive` (every 5s). Resume via `after_seq` / `Last-Event-ID`.
- Workflow/subagent reconstruction: `workflow_started`(name, description, phases[])
  + `workflow_finished`(status, summary, agent_count); agents arrive as
  `task_started/progress/finished` tagged with `workflow_id`, `phase`,
  `agent_type`, `status`, `summary`, `depth`; tokens via `token_usage`
  (`scope`/`workflow_id`/`phase`). Standalone subagents = `task_*`/`background_task`
  with **no** `workflow_id`. Child final result via `child-agents/{id}/result`.
  Children do **not** live-stream their own session (deep trace = load child
  `/session/history`).

---

## Phases

### Phase 1 — Real chat foundation (iOS)
- **Auth gate:** `needsConfiguration` requires non-empty `apiKey` too; always send
  the bearer header in non-mock mode; surface `401` as a distinct "Auth failed"
  state (don't trust `/health.auth == nil`).
- **Streaming connection:** dedicated `URLSessionConfiguration` for SSE with a
  large/infinite `timeoutIntervalForRequest` (the 30s default kills the stream).
- **Errors:** parse `detail` as object (`detail.message`); treat `409` as
  benign "already sent" (no toast, no double-send); `404` → refresh session list.
- **Events:** handle `TOOL_CALL_END` (stop streaming spinner) + accumulate
  `TOOL_CALL_ARGS`; handle `REASONING_*` (mirror TEXT_MESSAGE_*) into a reasoning
  surface; ensure `activeRunID` is set from the POST `run_id` before approvals.
- **Reconnect:** on SSE end/throw, reconnect with backoff, re-anchoring
  `after_seq` from a fresh `history.runtime.latest_event_seq`; on `stream_reset`
  re-read `reset_seq`/`latest_seq` to re-anchor.

### Phase 2 — Real workflows + subagents (iOS)
- Extend `StreamEvent` + CodingKeys with `task_id`, `workflow_id`, `child_run_id`,
  `child_session_id`, `parent_task_id`, `agent_type`, `status`, `summary`, `depth`,
  `phase`, `phases`, `description`, `command`, `detail`, `terminal`, `wait`,
  `reset_seq`/`latest_seq`.
- An **event→store reducer** that builds `MockWorkflow`/`MockSubagent`-shaped live
  models from `workflow_*` + `task_*` (filtered by `workflow_id` per server note —
  standalone `task_*`/`background_task` with no `workflow_id` → subagents). Mirror
  the desktop's event→workflow reducer shape. The real path appends these to the
  transcript instead of the mock trigger words.
- Add `GET /chat/child-agents/{id}/result` (+`wait`) and `POST .../cancel` to
  `ArdenAPIClient`; wire into `AgentDetailSheet`/`SubagentList` (final result +
  cancel; deep trace via child `/session/history`).

### Phase 3 — Real automations (iOS)
- Add automation REST calls (`server/routers/automation.py`: list/toggle/run) to
  `ArdenAPIClient`; subscribe to the `__automation__` bus events
  (`automation_progress/finished/suggestions_updated`). `AutomationsView` reads
  real data in non-mock mode (keeps mock list in mock mode).

### Phase 4 — QR pairing (server + iOS)
- **Server:** emit connection info as a QR / deep link `arden://connect?url=<lan>&key=<key>`
  (a `arden-server pair` command or QR printed at `serve`; the plaintext key is
  known at startup).
- **iOS:** "Scan to connect" in Settings → camera (AVFoundation) scans the QR →
  parse the deep link → fill `serverURL` + `apiKey` → connect. Add
  `NSCameraUsageDescription` to Info.plist. Manual paste remains as fallback.

### Deferred
- **Artifacts:** no server event → stay mock-only (or later derive from
  HTML-producing `TOOL_CALL_RESULT` + `raw_ref`).

---

## Verification
- Each phase: `xcodebuild` compiles clean; iOS unit tests
  (`Tests/ArdenCoreTests`) updated with real `task_*`/`workflow_*`/`background_task`
  SSE fixtures.
- End-to-end is **on-device** (user runs `uv run arden-server serve` with a real
  key): basic chat, a tool-approval run, a workflow/subagent-spawning prompt,
  automations, and QR pairing. The mock toggle stays for offline/demo.
