# Arden State Domain Architecture Design

## Goal

Make arden state simple to reason about and robust under streaming, replay, reconnects, session switches, automations, and background agents.

The core mental model is:

> Server history is truth. Streams apply the live tail. Cache is preview. UI renders a rebuildable projection.

## Current Problem

State ownership is implicit across several layers:

- Desktop Zustand global slots hold the active session projection.
- `sessionCache` stores per-session snapshots and can restore stale state.
- `useEvents.ts` owns hidden module-level stream state such as event cursors, pending tool calls, replay-gap reloads, and active assistant IDs.
- Server `SessionBus`, `session_events`, checkpoints, and `/session/history` all participate in replay/recovery.
- Automations and background agents have separate stream/polling paths with their own partial state.

This makes failures hard to reason about. A corrupted client projection can survive until Cmd+R, and replay/reconnect logic can accidentally rebuild old checkpointed state instead of rehydrating from canonical history.

## Prior Art Reviewed

Local agent repos suggest useful patterns:

- Codex uses a clear `thread -> turn -> item` event model, with `item.started`, `item.updated`, and `item.completed` terminality.
- Claude Code keeps explicit connection statuses and uses selector-based state access. Its state comments distinguish process-local, session-only, and persisted state.
- Hermes Agent has a stream consumer that owns segment state, fallback state, finalization, and transport degradation in one place.
- Letta normalizes raw provider stream fragments into stable message events with IDs and indices before consumers render them.

The design should borrow the shape: stable domain events, explicit phases, and small controllers/reducers. It should not copy a broad framework.

## Architecture

Keep Zustand as the app store, but split state into explicit domains:

- `sessionView`: selected session, cache preview, history load phase, pagination.
- `chatStream`: SSE/Electron bridge connection, per-session cursor, replay gap, pending tool-call assembly, active assistant ID.
- `runLifecycle`: active run ID, running/cancelling/error phase, approvals, queued messages.
- `automationStream`: automation event connection, progress rows, refresh triggers.
- `backgroundAgents`: task state and task result updates.
- `uiShell`: modals, sidebar, command palette, prefs, and other local UI state.

Components should select state and dispatch actions. Domain controllers/reducers should apply events and enforce ordering.

## State Phases

Session/chat should use named phases:

- `idle`
- `cached-preview`
- `loading-history`
- `live-tail`
- `replay-gap`
- `failed`

The important rule: cached messages may render immediately, but chat SSE must not connect until canonical history reconciliation completes. If a replay gap happens, tail events stay blocked until history reload succeeds.

Stream domains should also expose connection phases:

- `closed`
- `connecting`
- `live`
- `reconnecting`
- `blocked`
- `failed`

These phases replace inference from combinations of booleans like `historyLoadedFor`, `running`, `currentRunId`, and module globals.

## Invariants

1. Persisted server history is authoritative after reload/checkpoint.
2. Client cache is preview only and never proves correctness.
3. Streams append or patch only the live tail after canonical history is loaded.
4. Replay gaps force canonical reload before more tail events apply.
5. Runs, tools, automations, and background agents have explicit terminal states.
6. One domain owns each mutation.
7. Transcript projection can be discarded and rebuilt from history plus live tail at any time.

## Data Flow

Session open:

1. `sessionView` switches to `cached-preview` if a cache snapshot exists.
2. `sessionView` enters `loading-history`.
3. `/session/history` replaces the transcript projection.
4. `chatStream` connects only after history load succeeds.
5. Live events apply through the projection reducer.

Replay gap:

1. `chatStream` receives `stream_reset`.
2. `chatStream` enters `blocked`.
3. `sessionView` reloads canonical history.
4. On success, `chatStream` forgets the stale cursor and reconnects.
5. On failure, the stream remains blocked and the UI shows the reload error.

Automations/background agents:

1. Their event/polling streams own connection state.
2. Events update domain records with explicit phases.
3. Sidebar/UI reads derived records, not raw event details.

## Error Handling

- History load failure keeps the session in a failed or blocked state; it must not silently resume event application.
- Stream reconnect failure is visible as connection state, not only an error toast.
- Future cursors, missing sessions, and buffer gaps all route through the same replay-gap recovery path.
- If server and client disagree, canonical history wins.

## Testing

Add focused tests for:

- Session transition: cache preview -> history load -> live tail.
- Replay gap blocks tail until history reload succeeds.
- Replay gap remains blocked after reload failure.
- Stream reconnect uses the stored cursor only when canonical state is loaded.
- Run lifecycle terminal states do not regress to running.
- Automation/background-agent events transition through explicit phases.
- Projection reducer can rebuild transcript from history plus live tail.

Existing `bun run typecheck`, `bun run build`, and focused server pytest suites remain required.

## Migration Plan

Implement incrementally:

1. Define domain state types and invariant tests.
2. Move session view state out of implicit `historyLoadedFor` logic into `sessionView`.
3. Move `useEvents.ts` module globals into `chatStream`.
4. Route transcript updates through a small projection reducer.
5. Move run lifecycle state into `runLifecycle`.
6. Move automation stream and background-agent state into their domains.
7. Leave visual UI unchanged unless a state invariant requires a small adjustment.

## Non-Goals

- No new generic state framework.
- No broad UI redesign.
- No decorative motion or interaction changes.
- No persistence migration unless required by an invariant.
- No large rewrite before tests lock the current behavior.
