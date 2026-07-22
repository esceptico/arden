# Backend protocols

This document describes the backend wiring after the generic in-process channel bus was removed. The important distinction is that the backend now uses several explicit protocols instead of one catch-all pub/sub primitive.

## Protocol map

| Protocol | Owner | Transport | Durable | Main producers | Main consumers |
| --- | --- | --- | --- | --- | --- |
| Session SSE stream | `arden.server.bus` and `arden.server.routers.chat` | In-process `asyncio.Queue` | No | Chat and agent run loop | `/chat/events/{session_id}` clients |
| Automation SSE stream | `arden.automation.scheduler` | In-process `asyncio.Queue` under `automation:events` | No | Scheduler run progress | `/automations/events` clients |
| Outbox | `arden.outbox` | SQLite table `outbox_events` | Yes | Chat, operator, CLI, memory service | `OutboxWorker` handlers in `RuntimeOutbox` |
| Scheduler triggers | `arden.automation` | Scheduler methods and SQLite state | Mixed | Time tick, run completion, monitor events, manual run | Automation runner and built-in handlers |
| Monitor events | `arden.monitor` | Direct async callback | No, then scheduler-owned persistence | Calendar monitor | `Scheduler.fire_event` |

The rule is simple: UI streaming is transient, cross-workflow side effects go through the outbox, and automation triggering is owned by the scheduler.

## Persistence ownership

The tables below are implementation details of their owning store. Other modules should depend on the store methods, not on the table names or SQL shape.

| Table | Owner module | Public protocol |
| --- | --- | --- |
| `outbox_events` | `arden.outbox.store` | `OutboxStore` enqueue, claim, status, replay, prune methods |
| `scheduled_tasks` | `arden.automation.store` | `AutomationStore` task CRUD and scheduler state methods |
| `automation_event_dedupe` | `arden.automation.store` | `AutomationStore.claim_event()` and event dedupe helpers |
| `automation_event_queue` | `arden.automation.store` | `AutomationStore` event queue methods |
| `automation_count_state` | `arden.automation.store` | `AutomationStore` count trigger methods |
| `monitor_state` | `arden.monitor.store` | `MonitorStateStore` namespace state methods |

There is an architecture test that checks these table names do not appear in production modules outside their owner files. Tests may still inspect tables directly when they are exercising a store as a persistence adapter.

## Startup wiring

`arden.server.app.lifespan` is the composition root for backend runtime protocols. HTTP endpoint behavior lives in routers; `arden.server.app` owns process lifecycle, middleware, and router registration.

1. `Runtime.connect()` initializes stores, search, memory, skills, notifiers, automation, MCP, and tools.
2. `BusRegistry()` is created for SSE streams only.
3. `runtime.scheduler.set_bus_registry(bus_registry)` gives automation runs a way to publish UI progress.
4. `runtime.start_scheduler()` seeds built-ins, starts the scheduler, and starts the outbox worker.
5. `runtime.start_monitor()` creates `Monitor(self.scheduler.fire_event)` and registers monitor providers such as `CalendarMonitor`.

Shutdown is the reverse ownership order: active runs are cancelled, monitor/outbox/scheduler/indexer background tasks stop, then external resources and stores close.

## Session SSE stream

Source files:

- `apps/server/arden/server/bus.py`
- `apps/server/arden/server/routers/chat.py`
- `apps/server/arden/services/chat.py`

The session stream exists only to deliver live UI events to connected clients. It is not a backend event bus.

Data flow:

1. `POST /chat/message` creates or finds a session run.
2. The chat router gets a `SessionBus` from `BusRegistry` for that session.
3. `run_chat(ctx, bus)` publishes `SSEEvent` objects while the agent runs.
4. `GET /chat/events/{session_id}` subscribes to the same `SessionBus` and serializes events as SSE.
5. When the stream closes and no active run remains, the bus is removed.

Agent input injection:

- `RunState` owns the in-memory injection queue for a run.
- `POST /chat/message` queues an injection on the active `RunState` when a session already has a running agent.
- `DELETE /chat/inject/{client_id}` asks the active `RunState` to cancel a queued client-stamped injection.
- `AgentHooks.get_pending_messages` drains queued injections through `RunState.drain_injections()` at agent loop boundaries.
- Client-stamped entries emit `message_ingested` before delivery to the LLM; `client_id` is stripped from the message passed to the agent.

Operational endpoint:

- `GET /chat/runs/status`: content-free run lifecycle visibility. It reports retained/active run counts, active run timestamps, pending injection counts, approval channel state, task/drain flags, and background task session counts. It does not return prompt text, message bodies, tool arguments, or injection contents.

Properties:

- In-memory only.
- Per session.
- Best effort.
- Replayable from the in-memory recent-record buffer while records remain available.
- Shutdown sends a `None` sentinel to unblock subscribers.
- Keepalives are generated by the HTTP stream, not by producers.

Session SSE events are normalized before they enter the bus. The bus assigns the per-session `seq`, stores recent records for replay, and serializes SSE frames with `id: <seq>`. Route handlers do not synthesize text boundaries per subscriber; they only replay records and stream live records.

Reconnect contract:

1. Desktop tracks the last applied `seq` per session.
2. Desktop reconnects with `after_seq=<last applied seq>`.
3. Server replays records with greater sequence numbers when they are still buffered.
4. If the requested cursor is older than the buffer, or newer than the server's known generation, server emits `stream_reset` and desktop reloads session history before applying more events for that session.

Cancellation contract:

1. `POST /cancel` is a request, not the terminal state.
2. The endpoint returns `202` when the run exists and cancellation was accepted.
3. The worker emits `run_cancelled` when it observes cancellation and finishes teardown.
4. Desktop only treats terminal events as active-run terminal events when their `run_id` matches the current run.

Use this for live UI updates only. Do not attach domain side effects to this path.

## Automation SSE stream

Source files:

- `apps/server/arden/automation/scheduler.py`
- `apps/server/arden/server/routers/automation.py`
- `apps/server/arden/server/bus.py`

Automation progress uses the same queue primitive as chat streaming, but with the shared key `automation:events`.

Data flow:

1. Scheduler starts an automation run.
2. `_emit_automation_event()` publishes `AutomationProgressEvent` and `AutomationFinishedEvent`.
3. `/automations/events` subscribes to `automation:events` and streams SSE to the client.

Properties:

- In-memory only.
- Shared across automation UI clients.
- Best effort.
- Not replayable.
- Not involved in scheduling correctness.

If the stream fails, the automation still runs and final state is persisted through `AutomationStore`.

## Outbox

Source files:

- `apps/server/arden/outbox/store.py`
- `apps/server/arden/outbox/worker.py`
- `apps/server/arden/outbox/events.py`
- `apps/server/arden/server/runtime/outbox.py`
- `apps/server/arden/server/runtime/automation.py`

The outbox owns side effects that should survive a process crash or should not run inline with the producer.

Current event types:

| Event type | Producers | Runtime handler |
| --- | --- | --- |
| `run.completed` | Chat runs, operator runs, CLI runs | Call `Scheduler.handle_run_completed()`, then capture source/evidence/episode rows in `memory_items` |

Search indexing now scans active `memory_items` through `MemorySearchSource`; the old `memory.fact.index.*` outbox events were removed with the fact/observation service surface.

Processing model:

1. Producer inserts a row into `outbox_events` with `status='pending'`.
2. `OutboxWorker` claims ready rows by changing them to `running`.
3. The registered handler executes.
4. Success marks the row `completed`.
5. Failure returns the row to `pending` with retry backoff.
6. Too many failures move the row to `dead`.
7. Startup releases stale `running` rows back to `pending`.

Operational endpoints:

- `GET /outbox/status`: detailed counts, backlog age, worker state, and recent dead rows.
- `POST /outbox/dead/replay`: moves explicit dead row IDs back to `pending`.
- `DELETE /outbox/completed`: deletes old completed rows with an age cutoff and limit.

These endpoints live in `apps/server/arden/server/routers/ops.py`.

Retention:

- `OutboxWorker` automatically prunes completed rows older than 30 days.
- Pruning runs opportunistically during worker polling, at most once per hour, in bounded batches.
- Automatic pruning only deletes `completed` rows. `pending`, `running`, and `dead` rows remain until processed or explicitly repaired.

Guarantees:

- At-least-once delivery to handlers.
- Per-row retry with dead-letter state.
- No global ordering guarantee beyond claim order for ready rows.
- Producers remain decoupled from handler failure.

Handler design requirement: every handler must be idempotent or safe under retry. `run.completed` is deduped at enqueue by run ID; memory index operations are naturally overwrite/delete/clear operations.

Use the outbox when a side effect crosses subsystem boundaries and losing it would leave durable state inconsistent.

`RuntimeOutbox` is the server composition component for the outbox protocol. It owns the `OutboxWorker`, handler registration, outbox health/status, replay, and prune controls. `AutomationRuntime` constructs it and starts/stops it, while the top-level `Runtime` delegates outbox operations to the automation subsystem.

## Scheduler triggers

Source files:

- `apps/server/arden/automation/scheduler.py`
- `apps/server/arden/automation/store.py`
- `apps/server/arden/automation/triggers.py`
- `apps/server/arden/automation/builtins.py`

The scheduler owns automation execution and trigger state. It is not a pub/sub fan-out service; it evaluates triggers against its store and starts automation work.

Trigger paths:

| Trigger path | Entry point | Persistence |
| --- | --- | --- |
| Time trigger | Scheduler polling loop lists due tasks | `scheduled_tasks.next_run_at` and `running_since` |
| Idle trigger | Scheduler polling loop checks last activity | In-memory idle marker plus task state |
| Count trigger | `Scheduler.handle_run_completed()` | `automation_count_state` |
| Event trigger | `Scheduler.fire_event()` | `automation_event_dedupe` and `automation_event_queue` |
| Manual run | `Scheduler.schedule_run()` | Task running state |
| Built-in handler | `register_handler()` | Handler name on `scheduled_tasks` |

Event-triggered automations have their own queue because external monitor events can arrive while a task is already running. The scheduler:

1. Dedupes by `(task_id, event_key)` within a TTL.
2. Enqueues matching event context into `automation_event_queue`.
3. Starts the next queued event only when the task is idle.
4. Retries failed queued events with backoff.
5. Releases claimed event rows on startup reconciliation.

`run.completed` count triggers intentionally enter the scheduler through the outbox. That keeps chat/operator completion durable before count state and chat extraction state are updated.

Operational endpoint:

- `GET /scheduler/status`: scheduler liveness, registered built-in handlers, running task count, automation task summary, event queue summary, count trigger state count, and pending chat extraction count.

This endpoint lives in `apps/server/arden/server/routers/ops.py`.

## Monitor events

Source files:

- `apps/server/arden/monitor/service.py`
- `apps/server/arden/monitor/calendar.py`
- `apps/server/arden/server/runtime/automation.py`

Monitor providers do not publish to a bus. Runtime wires them directly into the scheduler:

```text
CalendarMonitor -> Monitor -> Scheduler.fire_event()
```

`CalendarMonitor` polls upcoming calendar items, turns matching items into `EventApproaching`, and calls the injected async sink for each event. The monitor catches per-event sink failures so one bad event does not stop the whole poll batch.

Durability starts at the scheduler boundary. If `Scheduler.fire_event()` accepts an event, dedupe and queue state are persisted in `AutomationStore`. The monitor itself does not own replay.

## Decision rules

Use these rules when adding a backend flow:

- Live client update: use the SSE stream bus.
- Durable side effect from one subsystem to another: use the outbox.
- Automation trigger or automation queueing: add it to the scheduler protocol.
- External polling source: make it a monitor provider that calls a typed async sink.
- Same-call required behavior: call the target service directly.

Avoid adding another generic bus unless there are multiple independent consumers, a clear ownership boundary, and a concrete delivery guarantee that the current protocols cannot express.
