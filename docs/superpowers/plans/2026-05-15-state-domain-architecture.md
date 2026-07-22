# State Domain Architecture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make arden state replay-safe and reload-stable by separating cached history, live streams, run lifecycle, automations, background agents, and UI shell into explicit domains with clear ownership.

**Architecture:** Keep Zustand as the app store, but replace hidden module state and mixed mutations with small domain reducers/controllers. Server history remains canonical; SSE is only the live tail; cache is preview only; UI is a rebuildable projection.

**Tech Stack:** TypeScript, React, Zustand, Bun, Vite, Electron, Python, FastAPI, pytest.

---

## File Structure

Create:

```text
apps/desktop/src/store/domains.ts
apps/desktop/src/store/session-view.ts
apps/desktop/src/store/chat-stream.ts
apps/desktop/src/store/transcript-projection.ts
apps/desktop/src/store/run-lifecycle.ts
apps/desktop/src/store/automation-domain.ts
apps/desktop/src/store/background-agent-domain.ts
apps/desktop/tests/stateDomains.test.ts
apps/desktop/tests/chatStreamDomain.test.ts
```

Modify:

```text
apps/desktop/src/store/types.ts
apps/desktop/src/store/index.ts
apps/desktop/src/store/session-cache.ts
apps/desktop/src/actions/history.ts
apps/desktop/src/actions/sessions.ts
apps/desktop/src/actions/messages.ts
apps/desktop/src/hooks/useEvents.ts
apps/desktop/src/hooks/useActiveRuns.ts
apps/desktop/src/hooks/useAutomationEvents.ts
apps/desktop/tests/sessionCache.test.ts
apps/desktop/tests/streamEvents.test.ts
apps/server/arden/server/routers/chat.py
apps/server/tests/test_chat_inject.py
```

---

## Phase 1: Test Harness And Contracts

- [ ] Normalize focused desktop test imports so Bun can execute the state tests directly.

  Target files:

  ```text
  apps/desktop/tests/sessionCache.test.ts
  apps/desktop/tests/streamEvents.test.ts
  ```

  Replace local `.js` imports with direct `.ts` or `.tsx` source imports only in the tests touched by this plan.

  Verify:

  ```bash
  cd apps/desktop
  bun test tests/sessionCache.test.ts tests/streamEvents.test.ts
  ```

  Expected before domain work: tests execute, even if assertions fail from current behavior.

- [ ] Add domain state types in `apps/desktop/src/store/domains.ts`.

  Required shape:

  ```ts
  export type HistoryPhase = "idle" | "cached-preview" | "loading-history" | "live-tail" | "replay-gap";
  export type ConnectionPhase = "idle" | "connecting" | "connected" | "reconnecting" | "disconnected" | "failed";
  export type RunPhase = "idle" | "queued" | "running" | "waiting-approval" | "completed" | "failed" | "cancelled";
  ```

  Include domain states:

  ```text
  sessionView
  chatStream
  runLifecycle
  automationStream
  backgroundAgents
  uiShell
  ```

- [ ] Add pure reducer tests in `apps/desktop/tests/stateDomains.test.ts`.

  Cover:

  ```text
  cached-preview cannot enable SSE rendering
  loading-history blocks replayed live tail
  live-tail starts only after server history is loaded
  replay-gap forces canonical history reload
  terminal run states clear active running state
  ```

  Verify:

  ```bash
  cd apps/desktop
  bun test tests/stateDomains.test.ts
  ```

---

## Phase 2: Session View Domain

- [ ] Implement `apps/desktop/src/store/session-view.ts`.

  Responsibilities:

  ```text
  selected session id
  cache preview restore
  canonical history loading phase
  pagination flags
  history loaded/reloading session id
  ```

  Public helpers:

  ```ts
  createInitialSessionViewState()
  reduceSessionSelected(...)
  reduceCachePreviewRestored(...)
  reduceHistoryLoadStarted(...)
  reduceHistoryLoadSucceeded(...)
  reduceHistoryLoadFailed(...)
  reduceReplayGapDetected(...)
  ```

- [ ] Wire `sessionView` into `apps/desktop/src/store/types.ts` and `apps/desktop/src/store/index.ts`.

  Keep legacy fields temporarily if components still read them:

  ```text
  currentSessionId
  historyLoadedFor
  historyReloadingFor
  historyHasMoreBefore
  historyHasMoreAfter
  historyLoadingBefore
  historyLoadingAfter
  ```

  They must be derived or updated by the sessionView helpers, not independently mutated.

- [ ] Update `apps/desktop/src/store/session-cache.ts`.

  Cache may restore transcript preview, but must not mark canonical history as loaded.

  Add/keep test:

  ```text
  restored cache enters cached-preview, not live-tail
  reload preserves visible preview while canonical history replaces it
  ```

---

## Phase 3: Server Replay Boundary

- [ ] Keep `apps/server/arden/server/routers/chat.py` aligned with the replay contract.

  Required behavior:

  ```text
  persisted session_events are cursor/checkpoint evidence only
  no persisted assistant/tool event replay into fresh UI on reload
  active in-memory bus may replay only recent live events after a valid cursor
  replay_gap tells client to reload canonical /session/history
  ```

- [ ] Update server tests in `apps/server/tests/test_chat_inject.py`.

  Cover:

  ```text
  old persisted streaming events do not replay after reload
  checkpoint seq survives and advances cursor
  replay gap path is explicit
  ```

  Verify:

  ```bash
  cd apps/server
  uv run pytest tests/test_chat_inject.py
  ```

---

## Phase 4: Chat Stream Domain

- [ ] Implement `apps/desktop/src/store/chat-stream.ts`.

  Move hidden `useEvents.ts` module state into explicit state:

  ```text
  pendingResultPatches
  replayGapReloadingSessions
  replayGapBlockedSessions
  pendingToolCalls
  activeAssistantMessageId
  lastEventSeqBySession
  replay mutation timers
  connection phase
  ```

  Public API:

  ```ts
  createInitialChatStreamState()
  reduceStreamConnecting(...)
  reduceStreamConnected(...)
  reduceStreamDisconnected(...)
  reduceReplayGap(...)
  reduceEventCursor(...)
  clearReplayBlock(...)
  ```

- [ ] Add `apps/desktop/tests/chatStreamDomain.test.ts`.

  Cover:

  ```text
  stale event seq is ignored
  replay gap blocks mutation until history reload finishes
  clear replay block is scoped to session id
  reconnect keeps cursor but does not replay visual animations
  ```

- [ ] Refactor `apps/desktop/src/hooks/useEvents.ts`.

  Hook should own transport only:

  ```text
  open EventSource/fetch stream
  dispatch raw events
  update connection phase
  close on session change/unmount
  ```

  It must stop owning transcript mutation state through module globals.

---

## Phase 5: Transcript Projection

- [ ] Implement `apps/desktop/src/store/transcript-projection.ts`.

  This module owns event-to-message projection:

  ```text
  assistant deltas
  reasoning deltas
  tool call lifecycle
  patch result application
  done/error terminal events
  usage updates
  ```

  Public API:

  ```ts
  applyChatEventToTranscript(state, event)
  rebuildTranscriptFromHistory(historyMessages)
  ```

- [ ] Replace direct projection logic inside `useEvents.ts` with calls into `transcript-projection.ts`.

  Invariant:

  ```text
  same history + same accepted live events = same transcript
  ```

- [ ] Extend `apps/desktop/tests/streamEvents.test.ts`.

  Cover:

  ```text
  no animation replay after reload with persisted events
  live deltas still render during active stream
  tool updates remain ordered
  terminal done event finalizes running state once
  ```

---

## Phase 6: Run Lifecycle Domain

- [ ] Implement `apps/desktop/src/store/run-lifecycle.ts`.

  Own:

  ```text
  running
  currentRunId
  activeRunSessionIds
  unreadDoneSessionIds
  approvals
  pendingResume
  queued messages
  stop state
  ```

  Public helpers:

  ```ts
  reduceRunStarted(...)
  reduceRunStatus(...)
  reduceRunCompleted(...)
  reduceRunFailed(...)
  reduceApprovalRequested(...)
  reduceApprovalResolved(...)
  ```

- [ ] Update `apps/desktop/src/actions/messages.ts` and `apps/desktop/src/hooks/useActiveRuns.ts`.

  Mutations must go through run lifecycle helpers.

  Verify:

  ```text
  sending message enters queued/running deterministically
  stop clears only the active run
  approval state survives history refresh
  completed run cannot be resurrected by stale status poll
  ```

---

## Phase 7: Automation And Background Agent Domains

- [ ] Implement `apps/desktop/src/store/automation-domain.ts`.

  Own automation stream connection and status projection currently spread through `useAutomationEvents.ts`.

  Required phases:

  ```text
  idle
  connecting
  connected
  reconnecting
  stale
  failed
  ```

- [ ] Refactor `apps/desktop/src/hooks/useAutomationEvents.ts`.

  Hook should transport events and dispatch domain updates. It should not be the source of truth for automation status.

- [ ] Implement `apps/desktop/src/store/background-agent-domain.ts`.

  Own:

  ```text
  background agent run rows
  open item ids
  unread/completed flags
  refresh status
  ```

- [ ] Update components/selectors that read background agent state.

  Keep UI shape unchanged. This phase is structural, not a visual redesign.

---

## Phase 8: Integration And Cleanup

- [ ] Remove obsolete module globals from `apps/desktop/src/hooks/useEvents.ts`.

  `rg` must find no remaining hidden state for stream replay:

  ```bash
  rg -n "pendingResultPatches|replayGapReloadingSessions|replayGapBlockedSessions|pendingToolCalls|activeAssistantMessageId|lastEventSeqBySession" apps/desktop/src/hooks/useEvents.ts
  ```

  Expected: no matches.

- [ ] Audit store mutation ownership.

  Search:

  ```bash
  rg -n "set\\(\\(state\\)|setState\\(|useStore\\.setState" apps/desktop/src
  ```

  Expected: direct mutation sites are either domain helpers or small action wrappers.

- [ ] Run focused verification.

  ```bash
  cd apps/desktop
  bun test tests/stateDomains.test.ts tests/chatStreamDomain.test.ts tests/sessionCache.test.ts tests/streamEvents.test.ts
  bun run typecheck
  bun run build
  ```

  ```bash
  cd apps/server
  uv run pytest tests/test_chat_inject.py
  ```

- [ ] Manual browser verification at current app URL.

  Scenarios:

  ```text
  open a session with cached messages
  reload while a stream is active
  confirm old deltas do not animate again
  confirm current live stream continues after canonical history load
  switch sessions during stream
  return to previous session
  trigger/observe automation status updates
  open background agent list and confirm completed/running states remain stable
  ```

---

## Non-Goals

```text
No UI redesign
No source-focus work
No new generic state framework
No server database migration unless tests prove the cursor contract needs one
No change to message visual styling beyond necessary loading/error state accuracy
```

