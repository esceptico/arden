# Event System Intel - 2026-05-08

Scope: server and desktop event streaming for ARDEN.

## External Systems

The stable pattern across OpenAI Responses, OpenAI Agents SDK, LangGraph, Claude Agent SDK, Letta, Mastra, CrewAI, and Temporal is consistent:

- Server-owned execution identity: `session_id` or `thread_id`, plus `run_id`.
- Server-owned ordering: monotonic `seq`, `sequence_number`, `seq_id`, event history id, or replay cursor.
- Explicit execution hierarchy: child run, subgraph namespace, parent tool use id, task id, or child workflow id.
- Explicit terminal states: completed, failed, cancelled, timed out, interrupted, killed.
- Cancellation is a request, then an observed terminal state after the worker acknowledges it.
- Reconnect uses a cursor or event history, not timestamps or client render order.
- UI renders a projection from the event stream. It does not invent run state.

Useful references:

- OpenAI Responses streaming: https://developers.openai.com/api/docs/guides/streaming-responses
- OpenAI background mode: https://developers.openai.com/api/docs/guides/background
- OpenAI cancel response: https://developers.openai.com/api/reference/resources/responses/methods/cancel
- LangGraph streaming: https://docs.langchain.com/langsmith/streaming
- LangGraph subgraphs: https://docs.langchain.com/oss/python/langgraph/use-subgraphs
- Claude Agent SDK streaming: https://code.claude.com/docs/en/agent-sdk/streaming-output
- Claude Agent SDK subagents: https://code.claude.com/docs/en/agent-sdk/subagents
- Letta streaming: https://docs.letta.com/guides/core-concepts/messages/streaming
- Letta Code architecture: https://docs.letta.com/letta-code/how-it-works
- Mastra events: https://mastra.ai/docs/streaming/events
- Temporal event history: https://docs.temporal.io/workflow-execution/event

## Local Reference Repos

### Hermes Agent

Path: `/Users/escept1co/src/hermes-agent`

Hermes has the strongest sub-agent UI vocabulary. Its gateway emits JSON-RPC style events and has explicit `subagent.*` events with `subagent_id`, `parent_id`, `depth`, status, progress, and usage. It also keeps interrupt and control handling isolated from long-running work so user control remains responsive.

Useful files:

- `/Users/escept1co/src/hermes-agent/tui_gateway/server.py:192` - gateway `_emit` envelope.
- `/Users/escept1co/src/hermes-agent/tui_gateway/server.py:845` - sub-agent event shape.
- `/Users/escept1co/src/hermes-agent/tui_gateway/server.py:1749` - spawn tree snapshot.
- `/Users/escept1co/src/hermes-agent/run_agent.py:3544` - interrupt propagation.

Weakness to avoid: Hermes does not provide a durable sequence/replay contract. Good terminal interaction model, weaker recovery model.

### Letta

Path: `/Users/escept1co/src/letta`

Letta has the best ordering and replay model. Stream messages include `message_type`, `run_id`, `seq_id`, `step_id`, and `otid`. Redis streams provide replay with a `starting_after` cursor. The exact Redis machinery is heavier than ARDEN needs right now, but the contract is right.

Useful files:

- `/Users/escept1co/src/letta/letta/schemas/letta_message.py:68` - typed streaming message union.
- `/Users/escept1co/src/letta/letta/services/streaming_service.py:196` - streaming service.
- `/Users/escept1co/src/letta/letta/server/rest_api/redis_stream_manager.py:463` - replay cursor.
- `/Users/escept1co/src/letta/letta/schemas/letta_request.py:227` - `starting_after`.
- `/Users/escept1co/src/letta/letta/server/rest_api/streaming_response.py:153` - cancellation-aware stream wrapper.
- `/Users/escept1co/src/letta/letta/services/streaming_service.py:737` - terminal cancelled stop.

What to copy: `run_id`, `seq_id`, `step_id`, cursor replay, terminal invariants.

### Claude Code Leaked

Path: `/Users/escept1co/src/claude-code-leaked`

Claude has the clearest control envelope and task vocabulary. Control messages distinguish request, response, and cancel request. Nested work carries `parent_tool_use_id`, `task_id`, task progress, task notifications, and explicit terminal task states.

Useful files:

- `/Users/escept1co/src/claude-code-leaked/src/remote/SessionsWebSocket.ts:40` - websocket subscriptions.
- `/Users/escept1co/src/claude-code-leaked/src/entrypoints/sdk/controlSchemas.ts:578` - control envelope.
- `/Users/escept1co/src/claude-code-leaked/src/entrypoints/sdk/coreSchemas.ts:1272` - parent tool use id.
- `/Users/escept1co/src/claude-code-leaked/src/entrypoints/sdk/coreSchemas.ts:1648` - tool progress.
- `/Users/escept1co/src/claude-code-leaked/src/entrypoints/sdk/coreSchemas.ts:1735` - task events.
- `/Users/escept1co/src/claude-code-leaked/src/Task.ts:15` - task state enum.

What to copy: control request/response/cancel request as a shape, task lifecycle events, parent tool use id.

## Pre-Implementation ARDEN Findings

These findings describe the state before the stable event-system implementation pass later in this file.

### High Confidence Bugs Found

- Desktop can permanently lose tool results in fast bursts. `TOOL_CALL_END` can delay non-first activity rows with `setTimeout`, while `TOOL_CALL_RESULT` immediately tries to merge into a row that may not exist yet. Relevant files:
  - `/Users/escept1co/src/arden/apps/desktop/src/hooks/useEvents.ts:39`
  - `/Users/escept1co/src/arden/apps/desktop/src/hooks/useEvents.ts:220`
  - `/Users/escept1co/src/arden/apps/desktop/src/hooks/useEvents.ts:252`
  - `/Users/escept1co/src/arden/apps/desktop/src/store.ts:583`
- Cancelled runs still enqueue internal `RunCompleted`, which can feed memory extraction and count-trigger automations as if the run completed. Relevant files:
  - `/Users/escept1co/src/arden/apps/server/arden/server/stream.py:36`
  - `/Users/escept1co/src/arden/apps/server/arden/services/chat.py:476`
  - `/Users/escept1co/src/arden/apps/server/arden/services/chat.py:522`
  - `/Users/escept1co/src/arden/apps/server/arden/server/runtime/outbox.py:48`
  - `/Users/escept1co/src/arden/apps/server/arden/automation/scheduler.py:173`
- Legacy terminal streaming is out of scope.
- SSE replay had no event id, no cursor, and no `Last-Event-ID`. The replay buffer was cleared after checkpoint saves, so reconnect could miss events. Relevant files:
  - `/Users/escept1co/src/arden/apps/server/arden/events/sse.py:75`
  - `/Users/escept1co/src/arden/apps/server/arden/server/bus.py:54`
  - `/Users/escept1co/src/arden/apps/server/arden/services/chat.py:451`
  - `/Users/escept1co/src/arden/apps/desktop/src/hooks/useEvents.ts:347`
  - `/Users/escept1co/src/arden/apps/desktop/electron/main.cjs:187`
- `/cancel` returned cancelled even for unknown run ids, only cancelled `run.task`, and did not cancel `drain_task` or background tasks.
- Blocking subprocess tools are not truly abortable. `bash` runs through `asyncio.to_thread`, so cancellation does not necessarily kill the underlying process.

### Architectural Fault Line

ARDEN mixes domain events and UI rendering rules across too many places:

- Agent events are domain-ish.
- `events/sse.py` converts domain-ish events into AG-UI-ish wire events.
- `routers/chat.py` synthesizes text start/end boundaries per subscriber.
- Clients should avoid independently rebuilding activity grouping from roles, timing, and heuristics.

This makes ordering, replay, and sub-agent display fragile. The stable direction is:

1. The server emits final wire events once, with a stable envelope and sequence number.
2. Text start/content/end are produced before events enter the bus.
3. The bus persists or buffers already-normalized events.
4. Desktop applies events in sequence order and renders a projection.
5. Sub-agents have first-class task lifecycle events linked to the parent tool call.

## Target ARDEN Event Envelope

Every streamed event should eventually carry:

```json
{
  "type": "TOOL_CALL_RESULT",
  "session_id": "20260508_120000_000",
  "run_id": "cool-otter",
  "seq": 42,
  "timestamp": 1778241600000,
  "message_id": "text-abc",
  "tool_call_id": "call-123",
  "parent_tool_call_id": "call-parent",
  "task_id": "call-123",
  "parent_task_id": null
}
```

Ordering is by `(session_id, seq)` for the chat stream. `timestamp` is display metadata only.

## Implementation Bias

Do not rewrite the whole chat system. Fix in this order:

1. Desktop reducer race.
2. Server text boundary normalization.
3. Sequence ids and replay cursor.
4. Cancellation terminal contract.
5. Sub-agent task lifecycle events.
6. Durable event store if in-memory cursor replay is not enough for reconnect behavior under real desktop use.

## Implemented Pass - 2026-05-08

This pass intentionally kept the system in-process and server/desktop scoped.

Implemented:

- Server emits explicit text start/content/end events before events enter the bus.
- `SessionBus` owns per-session `seq` assignment and serializes SSE frames with `id: <seq>`.
- Desktop tracks the last applied sequence per session and reconnects with `after_seq`.
- Replay gaps emit `stream_reset`; desktop reloads history, blocks tail events until the reload succeeds, drops old-session tail events after navigation, and recovers the block after a later successful replace history load.
- Desktop fixed fast tool-result races by buffering result patches until delayed activity rows render.
- Cancel is two-phase: `/cancel` accepts the request and `run_cancelled` is emitted only after worker acknowledgement.
- Cancelled runs no longer enqueue `run.completed` outbox work.
- Sub-agent foreground spawns emit `task_started`, `task_progress`, and `task_finished` linked to the parent tool call.
- Terminal desktop events are guarded by `run_id` so stale finish/error/cancel events cannot clear a newer active run.
- Contract tests now lock terminal wire identity, SSE cursor identity, desktop sequence dedupe, stale terminal guards, replay-gap recovery, and ordered text projection.

Remaining risks:

- Replay is still bounded to the in-memory recent-record buffer plus history reload. A durable event table would be the next step if reconnect across process restart needs exact event replay rather than history reconstruction.
- Browser/Electron cancellation of blocking subprocess tools is not fully process-tree-aware yet.
- Removed legacy terminal-client follow-up; desktop is the supported UI target.
