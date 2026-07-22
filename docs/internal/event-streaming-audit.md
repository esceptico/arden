# Event Streaming Audit

Date: 2026-05-20

Scope: server event production, replay, desktop propagation, UI projection, and prior-art comparison with Letta, Codex, Hermes, and Claude Code leaked.

## Current Pipeline

The server currently has a real event ledger plus a live SSE fanout.

`apps/server/arden/events/sse.py` defines the public event vocabulary. It mixes AG-UI-style run/text/tool events (`RUN_STARTED`, `TEXT_MESSAGE_CONTENT`, `TOOL_CALL_RESULT`) with arden-specific events (`background_task`, `stream_reset`, `compaction_*`, `automation_*`, `goal_*`). `agent_events_to_sse()` is the main adapter from internal agent events to SSE events.

`apps/server/arden/server/bus.py` is the live event bus. `SessionBus.emit()` assigns a monotonic per-session `seq`, writes a `StreamRecord`, appends to the in-memory replay buffer, optionally persists through `record_event`, and fans out to subscribers. `subscribe_with_replay(after_seq)` returns buffered events after the cursor unless a checkpoint or buffer boundary forces a replay gap.

`apps/server/arden/context/store.py` persists `session_events` as the durable stream ledger. It reconstructs `StreamRecord` objects from stored event JSON. `chat_runs.last_seq` is also used as a checkpoint watermark for recovery.

`apps/server/arden/server/routers/chat.py` exposes the session SSE endpoint. It subscribes to the bus, replays buffered records, emits `stream_reset` on replay gap, and sends keepalive comments with the latest seq.

`apps/desktop/electron/main.cjs` reads the SSE response and forwards only `data:` lines over IPC as `events:data`. Comment keepalives are ignored. Browser fallback in `apps/desktop/src/hooks/useEvents.ts` behaves similarly through `EventSource`/stream parsing.

`apps/desktop/src/store/chat-stream.ts` is the desktop stream coordinator. It tracks per-session event cursors, replay-gap blocks, transient reload state, and dispatches events into the transcript projection.

`apps/desktop/src/store/transcript-projection.ts` is the UI reducer for streamed events. It owns module-level transient projection state for active assistant messages, pending tool calls, delayed activity rows, and replay timing. It builds visible chat state from run/text/tool/background/goal events plus durable history reloads.

## Evidence Matrix

| Area | Evidence | Meaning |
| --- | --- | --- |
| Event vocabulary | `apps/server/arden/events/sse.py:33` defines mixed AG-UI and arden-specific `EventType` values | The stream is one shared channel for transcript, lifecycle, tools, background, automations, compaction, and goals |
| Text final event | `apps/server/arden/events/sse.py:157` carries cumulative `TextMessageEndEvent.content` | Server already has a final reconciliation payload |
| Agent adapter | `apps/server/arden/events/sse.py:479` maps internal agent events to SSE events | Internal agent events are not the desktop contract; SSE is the boundary |
| Dropped cumulative block | `apps/server/arden/events/sse.py:512` drops `TextBlock` to avoid duplicate UI text | Correct for streaming, but it increases importance of `TEXT_MESSAGE_END` reconciliation |
| Live bus | `apps/server/arden/server/bus.py:81` assigns seq, stores recent records, persists, then fans out | `SessionBus` is both live fanout and volatile replay buffer |
| Replay floor | `apps/server/arden/server/bus.py:102` filters replay by `max(after_seq, checkpoint_seq)` | Checkpoint decides whether desktop should reload history |
| Replay gap detection | `apps/server/arden/server/bus.py:119` treats future cursors, below-checkpoint cursors, and evicted buffers as gaps | The server already distinguishes safe replay from reset-required replay |
| Durable ledger | `apps/server/arden/context/store.py:141` creates `session_events(session_id, seq, event_json)` | The durable event log exists, but is mostly used as cursor ledger |
| Event persistence | `apps/server/arden/context/store.py:1407` stores payload JSON and `run_id` | Persisted event schema is currently payload-centric, not typed envelope-centric |
| Cursor restoration | `apps/server/arden/server/routers/chat.py:40` restores bus cursor from `session_events` and `chat_runs.last_seq` | Recreated buses recover seq/checkpoint state from disk |
| Reset emission | `apps/server/arden/server/routers/chat.py:77` emits synthetic `stream_reset` on replay gap | Desktop must respond by reloading history |
| Keepalive | `apps/server/arden/server/routers/chat.py:29` emits comment frame `: seq=...` | Server intends to communicate seq while idle |
| Electron parser | `apps/desktop/electron/main.cjs:235` forwards only `data:` lines | Electron ignores keepalive comments |
| Browser parser | `apps/desktop/src/hooks/useEvents.ts:113` also ignores non-`data:` lines | Browser path has the same keepalive blind spot |
| Desktop cursor | `apps/desktop/src/store/chat-stream.ts:108` accepts/drops events by seq | Cursor logic exists but only advances on typed data events |
| Replay-gap client path | `apps/desktop/src/store/chat-stream.ts:210` reloads history and clears replay block | Recovery is asynchronous and spread across stream/history/projection code |
| Global stream state | `apps/desktop/src/store/chat-stream.ts:52` stores `chatStreamState` as a module singleton | Projection/cursor state is outside normal app store lifecycle |
| End ignored | `apps/desktop/src/store/transcript-projection.ts:207` ignores `TEXT_MESSAGE_END` | Final content is not used to correct streamed deltas |
| History rebuild | `apps/desktop/src/store/transcript-projection.ts:373` rebuilds visible transcript from durable messages | Reload path is a separate projection mode from stream path |
| Automation stream | `apps/server/arden/server/routers/automation.py:88` uses live-only `bus.subscribe()` | Automation events do not have the chat stream replay contract |
| Automation keepalive | `apps/server/arden/server/routers/automation.py:83` emits plain `: keepalive` comments | Automation stream has even less cursor/liveness information |

## Existing Tests

The server has meaningful replay tests. `apps/server/tests/test_session_bus.py:111` verifies cursor replay, `apps/server/tests/test_session_bus.py:124` verifies below-checkpoint gaps, and `apps/server/tests/test_session_bus.py:196` verifies no gap when the cursor is at or above checkpoint. `apps/server/tests/test_chat_inject.py:371` verifies reset after bus recreation, `apps/server/tests/test_chat_inject.py:416` verifies persisted checkpoint cursor recovery, and `apps/server/tests/test_chat_inject.py:616` verifies `stream=false` filters text deltas while preserving sequence ids.

The desktop has cursor-domain tests. `apps/desktop/tests/chatStreamDomain.test.ts:15` verifies stale seq drops, `apps/desktop/tests/chatStreamDomain.test.ts:31` verifies replay-gap blocking, and `apps/desktop/tests/chatStreamDomain.test.ts:77` verifies cursor rewind for half-applied tool calls.

Missing coverage: typed keepalive handling, `TEXT_MESSAGE_END.content` reconciliation, session-scoped projection isolation, and automation/background replay semantics.

## Source Of Truth Map

| Surface | Current role | Risk |
| --- | --- | --- |
| `session_events` | Durable raw event ledger with seq | Good base, but not all consumers treat it as the single replay source |
| `chat_runs.last_seq` | Checkpoint/watermark | Overloaded with run progress and replay boundary semantics |
| `session_messages` | Canonical transcript/history | Can diverge from transient stream projection until reload |
| `SessionBus._recent` | Live replay buffer | In-memory only; buffer eviction creates replay gaps |
| `lastEventSeqBySession` | Desktop cursor | Client-only; comments keepalive do not update it |
| Zustand chat messages | Visible UI projection | Mutated by stream reducer and history rebuild paths |
| `transcript-projection` module state | Active run/tool/text projection | Global singleton state, not clearly scoped to session lifecycle |

## Findings

### 1. Keepalive Seq Is Not Consumed

The server emits keepalive comments like `: seq=...`, but the Electron bridge forwards only `data:` frames. The desktop therefore does not learn liveness or cursor progress from keepalives. Letta avoids this by emitting keepalive as a normal data event carrying `seq_id`.

Impact: during long quiet periods, the server believes it is publishing seq-aware keepalives, but desktop cursor state is unchanged.

### 2. Replay-Gap Recovery Is Correct But Too Distributed

Replay gaps trigger `stream_reset`, desktop blocks tail events, reloads history, clears transient state, then retries queued live events. That flow is defensible, but it is spread across router, bus, `chat-stream.ts`, history actions, and projection state.

Impact: bugs during compaction, reconnect, or session switching are hard to reason about because recovery is not one reducer with one input/output contract.

### 3. Desktop Projection State Is A Module Singleton

`transcript-projection.ts` holds active run/tool/text state outside the main store and outside a clear per-session container.

Impact: session switches, history reloads, replay gaps, and background completions can accidentally interact through shared transient state unless every cleanup path is perfect.

### 4. `TEXT_MESSAGE_END.content` Is Ignored

The server sends cumulative final content on `TEXT_MESSAGE_END`, but desktop ignores it.

Impact: there is no final reconciliation point if deltas are dropped, duplicated, or trimmed. Prior art generally keeps a final authoritative message/step artifact or cumulative content to verify stream projection.

### 5. Chat And Automation Streams Have Different Contracts

Chat has seq, persisted event replay, stream reset, and history rebuild. Automation/background surfaces currently look more like side channels.

Impact: the UI can show active automation/background state, but not every surface has the same durable cursor/replay semantics.

### 6. Background Completion Should Be A First-Class Event

The recent background-agent work moved toward explicit completion notification, which matches Codex/Hermes better than polling files. The long-term shape should be an event/result envelope, not a hidden text convention.

Impact: the main agent needs a durable, model-visible notification, while the UI needs a meta/progress event that can be hidden from transcript rendering.

## Prior Art

### Letta

Letta uses a server-client stream with explicit run/job/message identity. The main client stream is `POST /agents/{agent_id}/messages` with `streaming=true`, returning `text/event-stream`. Keepalives are explicit request behavior: `include_pings` defaults true, and the wrapper emits `LettaPing` SSE data with `run_id` plus the last observed `seq_id`.

Letta separates two cursors. Redis stream `seq_id` is for transient SSE replay of background runs (`sse:run:{run_id}`, TTL 3h, max length 10k, `starting_after` cursor). DB message `sequence_id` is for persisted history and run message queries. Duplicate request recovery uses client OTIDs mapped to existing `run_id`, so a retried request can return the existing Redis stream before lock acquisition.

Best practice to copy: keepalive should be typed stream data, transient stream replay and durable transcript history should be separate contracts, and retries should use stable client operation ids.

### Codex

Codex forwards subagent completion through an explicit inter-agent mailbox. Child `TurnComplete` / `TurnAborted` calls `forward_child_completion_to_parent(...)`, formats `<subagent_notification>` JSON, and sends `InterAgentCommunication` to the parent with `trigger_turn=false`.

The notification is model-visible, not UI metadata. It renders as a contextual user fragment with `<subagent_notification>` markers. Delivery is event-driven through session op handling and a mailbox backed by `mpsc` plus watch sequence notifications. `wait_agent` waits on mailbox sequence changes rather than polling files.

Best practice to copy: separate agent-to-agent notifications from visible transcript UI, but keep them model-visible when they should trigger follow-up reasoning.

### Hermes

Hermes tracks background processes in a process registry. `notify_on_complete` causes a process to enqueue a completion object with `session_id`, command, exit code, and output on the first transition to finished. CLI delivery drains that queue while idle/after turns and injects a model-visible synthetic input.

Hermes gateway has a polling watcher variant, but that is a fallback around process state. The stronger pattern is idempotency: `_move_to_finished` only queues if it really moved from running, and poll/read/wait paths mark completion consumed so drains do not duplicate it.

Best practice to copy: background completion needs exactly-once consumption keyed by task/run state, not later filesystem probing.

### Claude Code Leaked

The leaked bridge treats sequence as a transport concern. `SSETransport` seeds `lastSequenceNum`, first connect sends both `from_sequence_num` and `Last-Event-ID`, and the high-water mark updates from SSE frame `id`. Sequence carryover survives transport swaps: the bridge captures the live sequence before close and passes it into the replacement transport.

UUID dedup and echo suppression are separate from sequence resume. Recent posted UUIDs drop echoes; inbound UUID caches defensively drop replayed prompts when stream negotiation fails. It also has explicit `isMeta` semantics: hidden from UI, model-visible. Visible transcript/session-title/search paths filter `isMeta`, while continuation nudges and system injections use it.

For teammate/subagent results, worker notifications are user-role synthetic XML (`<task-notification>`) and routed by agent id. Stopped/evicted agents can be resumed from transcript.

Best practice to copy: use monotonic sequence for SSE resume, UUID/event ids for message identity and dedup, and explicit `isMeta`/origin fields for hidden model-visible messages instead of overloading visible user text.

## Recommended Architecture

Make the stream model explicit:

1. `session_events` is the durable event log.
2. SSE is only the live transport for that log.
3. Desktop reducers project event log records into views.
4. History reload is a snapshot rebuild from durable transcript plus event cursor, not a separate behavioral mode.

Recommended event envelope:

```text
event_id
session_id
run_id
seq
kind
payload
created_at
visibility: ui | meta | model | both
durability: persisted | ephemeral
projection: transcript | activity | run | background | automation | none
```

Recommended reducers:

| Reducer | Input | Output |
| --- | --- | --- |
| Transcript reducer | text/tool/result events | visible chat messages |
| Activity reducer | tool/activity events | collapsible activity rows |
| Run reducer | run lifecycle/token usage/error | run status and usage |
| Background reducer | background task events | right-sidebar state and model notifications |
| Automation reducer | automation events | automation run state |

## Requirements Matrix

| Requirement | Current state | Required change |
| --- | --- | --- |
| Idle stream cursor/liveness survives quiet periods | Server emits comment keepalive; clients ignore it | Use typed data keepalive with seq, or parse comment keepalives in both clients |
| Stream replay is deterministic | Mostly covered by `SessionBus`/chat stream tests | Consolidate replay-gap state transition into one reducer and keep tests at that boundary |
| Final assistant text can be reconciled | Server sends final content; desktop ignores it | On `TEXT_MESSAGE_END`, replace/verify top-level assistant content for that `message_id` |
| Projection state is session-safe | Module singleton stores active message/tool timers | Move projection state into session-scoped state or reducer instances |
| Background agent completion wakes main agent | Completion exists but still behaves like a side channel | Persist a model-visible/UI-hidden background completion event and consume it through wakeup/loop path |
| Automation events match chat reliability | Live-only subscription, no cursor/replay | Either join `session_events`/cursor model or declare automation stream ephemeral and reload from automation store |
| Event identity supports dedup | Seq per session exists; no stable event id in envelope | Add stable `event_id` if events can cross channels, be redelivered, or be merged with background/automation ledgers |

## Design Decision

Use three separate identities, not one overloaded id:

| Identity | Purpose | Source |
| --- | --- | --- |
| `seq` | Transport ordering and resume within one session stream | Current `SessionBus` model, Claude transport resume, Letta Redis stream |
| `event_id` | Dedup/idempotency across delivery paths | Claude UUID dedup, Hermes exactly-once completion |
| `message_id` / `task_id` / `run_id` | Domain object identity | Current arden event payloads, Letta run/message model, Codex subagent ids |

Use two visibility axes:

| Field | Meaning |
| --- | --- |
| `model_visible` | Include in model context / wakeup input |
| `ui_visible` | Render as transcript content |

That avoids the current ambiguity where a background result may need to wake the model but should not appear as a normal user bubble.

## Fix Plan

### Phase 1: Contract And Cursor Hygiene

- Convert keepalive seq into a typed data event, or parse comment keepalives in both Electron and browser paths.
- Add event-contract tests for keepalive, `stream_reset`, duplicate seq, stale seq, and `TEXT_MESSAGE_END` reconciliation.
- Document the event visibility/projection rules next to `EventType`.
- Keep `seq` as transport cursor only; do not use it as domain identity.

### Phase 2: Desktop State Consolidation

- Move projection transient state into per-session store state or a session-scoped reducer object.
- Treat replay-gap recovery as one reducer transition: `healthy -> blocked -> reloading -> replaying -> healthy`.
- Use final `TEXT_MESSAGE_END.content` as a reconciliation point.

### Phase 3: Background And Automation Durability

- Represent background completion as a durable `background_task.finished` event with `task_id`, `run_id`, `event_id`, `model_visible=true`, `ui_visible=false`.
- Make the main agent wakeup consume that event directly instead of asking tools/filesystem where the result is.
- Add exactly-once consumption keyed by `(task_id, final_state_seq)` or `(task_id, event_id)`.
- Decide whether automation runs join `session_events` or get their own equivalent cursor/replay ledger.

### Phase 4: Server Cleanup

- Separate checkpoint meanings: run progress checkpoint, replay cursor, and transcript persistence boundary.
- Add a small server-side event contract test around `SessionBus`, persisted replay, and checkpoint recovery.
- Keep `SessionBus` as fanout only; make durable event log the authoritative replay source.

## TLDR

arden already has the right core primitive: a persisted per-session event log with seq ids. The fragile part is that desktop projection state and recovery logic are split across too many places, and some events are still side-channel-ish. Letta points to typed seq-carrying keepalives and queryable runs/messages. Codex and Hermes point to first-class background completion notifications. Claude points to resume cursors and stable dedup ids. The next serious fix should make event log -> typed reducers -> UI/model notifications the explicit architecture.
