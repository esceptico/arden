# Stable Event System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a stable server-owned event system for ARDEN chat runs so desktop rendering, sub-agent progress, replay, and cancellation are ordered and reliable.

**Architecture:** The server emits normalized wire events exactly once, assigns a monotonic per-session sequence number, and exposes cursor-based replay. Desktop consumes sequenced events as a reducer projection; it does not infer execution ordering from React timing.

**Tech Stack:** Python 3.13, FastAPI, SSE, SQLite/aiosqlite, pytest, React 19, Zustand, Electron, Bun tests.

---

## Scope

In scope:

- Server chat stream events.
- Desktop stream reducer and activity rendering.
- Main run cancellation.
- Backgrounded run drain cancellation.
- Sub-agent progress events for research/spawned agents.
- Replay during active desktop reconnects.

Out of scope:

- Removed client compatibility changes outside desktop.
- Multi-process or remote server clustering.
- Replacing SSE with WebSocket.
- Full Temporal-style durable workflow execution.

## File Structure

- Modify `apps/desktop/src/hooks/useEvents.ts`: consume sequenced events, fix activity result race, handle reconnect cursor, handle task lifecycle events.
- Modify `apps/desktop/src/api.ts`: add envelope fields and task lifecycle event types to `ServerEvent`.
- Modify `apps/desktop/src/store.ts`: add activity item status/progress fields and helper action for applying result patches safely.
- Modify `apps/desktop/src/components/trace/ActivityTrace.tsx`: show explicit running/completed/failed/cancelled state for agent activity rows without changing layout.
- Modify `apps/desktop/electron/main.cjs`: pass `Last-Event-ID` or `after_seq` when reconnecting through the Electron bridge.
- Modify `apps/desktop/tests/streamEvents.test.ts`: reducer tests for result race and task lifecycle.
- Modify `apps/desktop/tests/streamOrdering.test.ts`: sequence and reconnect tests.
- Modify `apps/server/arden/agent/types/events.py`: add explicit text boundary and task lifecycle agent events.
- Modify `apps/server/arden/agent/agent.py`: emit text boundaries from the agent stream.
- Modify `apps/server/arden/core/spawner.py`: emit sub-agent task lifecycle events.
- Modify `apps/server/arden/events/sse.py`: convert new agent events to final wire events and add `StreamRecord` serialization.
- Modify `apps/server/arden/server/bus.py`: own stream sequence assignment and replay by cursor.
- Modify `apps/server/arden/server/routers/chat.py`: remove per-subscriber text boundary synthesis and accept `after_seq`.
- Modify `apps/server/arden/server/state.py`: make cancel return a result and cancel drain/background tasks.
- Modify `apps/server/arden/server/routers/chat.py`: make `/cancel` report unknown runs and return accepted cancellation state.
- Modify `apps/server/arden/services/chat.py`: do not enqueue `RunCompleted` for cancelled runs.
- Modify `apps/server/arden/tools/core/context.py`: expose cancellation of all pending background tasks to the run registry.
- Modify `apps/server/tests/test_session_bus.py`: sequence and replay tests.
- Modify `apps/server/tests/test_streaming_events.py`: text boundary and envelope tests.
- Modify `apps/server/tests/test_run_state.py`: cancel semantics tests.
- Modify `apps/server/tests/test_chat_inject.py`: `/cancel` API tests.

## Event Contract

Use these invariants throughout the implementation:

- `seq` is assigned by the server, never by the client.
- `seq` is monotonic within one chat session stream.
- SSE frames include `id: <seq>`.
- Every event payload includes `session_id`, `seq`, and `timestamp`.
- Every run event includes `run_id`.
- Child/sub-agent activity uses `task_id` and `parent_tool_call_id`.
- Cancel is two-phase: request accepted, then `run_cancelled` after the worker observes cancellation.
- Desktop ignores events whose `seq` is less than or equal to the last applied seq for the current session.

---

### Task 1: Fix Desktop Tool Result Race

**Files:**
- Modify: `apps/desktop/src/store.ts`
- Modify: `apps/desktop/src/hooks/useEvents.ts`
- Test: `apps/desktop/tests/streamEvents.test.ts`

- [ ] **Step 1: Write the failing reducer test**

Add this test to `apps/desktop/tests/streamEvents.test.ts`:

```ts
test("keeps tool results when result arrives before delayed burst item renders", async () => {
  handleServerEvent({ type: "RUN_STARTED", run_id: "run-1", session_id: "session-1", timestamp: 1 });
  handleServerEvent({ type: "TOOL_CALL_START", tool_call_id: "tool-1", tool_call_name: "ReadFile", timestamp: 2 });
  handleServerEvent({ type: "TOOL_CALL_END", tool_call_id: "tool-1", timestamp: 3 });
  handleServerEvent({ type: "TOOL_CALL_START", tool_call_id: "tool-2", tool_call_name: "ListFiles", timestamp: 4 });
  handleServerEvent({ type: "TOOL_CALL_END", tool_call_id: "tool-2", timestamp: 5 });
  handleServerEvent({
    type: "TOOL_CALL_RESULT",
    tool_call_id: "tool-2",
    name: "ListFiles",
    content: "second result",
    preview: "second result",
    timestamp: 6,
  });

  await new Promise((resolve) => setTimeout(resolve, 80));

  const state = getState();
  const activityId = state.order.find((id) => state.messages.get(id)?.role === "activity");
  expect(activityId).toBeTruthy();
  const item = state.messages.get(activityId!)?.activity?.items.find((it) => it.id === "tool-2");
  expect(item?.result).toBe("second result");
});
```

- [ ] **Step 2: Run the failing test**

Run:

```bash
bun test apps/desktop/tests/streamEvents.test.ts
```

Expected: the new test fails because `tool-2` exists after the stagger delay but has no result.

- [ ] **Step 3: Add a safe pending result patch map**

In `apps/desktop/src/hooks/useEvents.ts`, add this module-level map near `pendingToolCalls`:

```ts
const pendingResultPatches = new Map<string, Partial<ActivityItem>>();
```

Replace `enqueueActivityItem` with this complete function:

```ts
function enqueueActivityItem(aid: string, item: ActivityItem) {
  const now = Date.now();
  const queued = nextItemRenderAt + ITEM_STAGGER_MS;
  const ceiling = now + MAX_STAGGER_LAG_MS;
  const renderAt = Math.max(now, Math.min(queued, ceiling));
  nextItemRenderAt = renderAt;
  const delay = renderAt - now;
  const apply = () => {
    const state = getState();
    if (!state.messages.get(aid)?.activity) return;
    const pendingPatch = pendingResultPatches.get(item.id);
    if (pendingPatch) {
      pendingResultPatches.delete(item.id);
      state.appendActivityItem(aid, { ...item, ...pendingPatch });
    } else {
      state.appendActivityItem(aid, item);
    }
  };
  if (delay === 0) apply();
  else setTimeout(apply, delay);
}
```

In the `TOOL_CALL_END` branch, when creating the first activity item, apply the same pending patch before inserting:

```ts
const pendingPatch = pendingResultPatches.get(item.id);
if (pendingPatch) {
  pendingResultPatches.delete(item.id);
  Object.assign(item, pendingPatch);
}
```

In the `TOOL_CALL_RESULT` branch, replace the direct merge with:

```ts
const merged = s.mergeActivityItem(event.tool_call_id, patch);
if (!merged) pendingResultPatches.set(event.tool_call_id, patch);
return;
```

This requires `mergeActivityItem` to return `boolean`, implemented in the next step.

- [ ] **Step 4: Make `mergeActivityItem` report whether it touched state**

In `apps/desktop/src/store.ts`, change the action type:

```ts
mergeActivityItem: (itemId: string, patch: Partial<ActivityItem>) => boolean;
```

Replace the action implementation with this complete function:

```ts
  mergeActivityItem: (itemId, patch) => {
    let didTouch = false;
    set((s) => {
      let touched = false;
      const messages = new Map(s.messages);
      for (const [mid, msg] of messages) {
        if (!msg.activity) continue;
        const idx = msg.activity.items.findIndex((it) => it.id === itemId);
        if (idx < 0) continue;
        const items = msg.activity.items.slice();
        items[idx] = { ...items[idx], ...patch };
        messages.set(mid, { ...msg, activity: { ...msg.activity, items } });
        touched = true;
        break;
      }
      didTouch = touched;
      return touched ? { messages } : s;
    });
    return didTouch;
  },
```

- [ ] **Step 5: Reset the pending map on disconnect**

In `resetStreamState`, add:

```ts
pendingResultPatches.clear();
```

- [ ] **Step 6: Run the desktop stream tests**

Run:

```bash
bun test apps/desktop/tests/streamEvents.test.ts apps/desktop/tests/streamOrdering.test.ts apps/desktop/tests/turnLayout.test.ts
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

Run:

```bash
git add apps/desktop/src/store.ts apps/desktop/src/hooks/useEvents.ts apps/desktop/tests/streamEvents.test.ts
git commit -m "fix: preserve fast tool result updates"
```

---

### Task 2: Add Explicit Server Text Boundaries

**Files:**
- Modify: `apps/server/arden/agent/types/events.py`
- Modify: `apps/server/arden/agent/agent.py`
- Modify: `apps/server/arden/events/sse.py`
- Modify: `apps/server/arden/server/routers/chat.py`
- Test: `apps/server/tests/test_streaming_events.py`

- [ ] **Step 1: Write tests for explicit text boundaries**

Append these tests to `apps/server/tests/test_streaming_events.py`:

```python
from arden.agent import TextDelta, TextEnded, TextStarted
from arden.events.sse import TextMessageEndEvent, TextMessageStartEvent


def test_text_boundary_events_convert_to_sse():
    (start,) = agent_events_to_sse(TextStarted(message_id="text-1"))
    (content,) = agent_events_to_sse(TextDelta(message_id="text-1", content="hello"))
    (end,) = agent_events_to_sse(TextEnded(message_id="text-1", content="hello"))

    assert isinstance(start, TextMessageStartEvent)
    assert start.message_id == "text-1"
    assert content.message_id == "text-1"
    assert isinstance(end, TextMessageEndEvent)
    assert end.message_id == "text-1"
    assert end.content == "hello"
```

Add this async test near the existing `_event_stream` test:

```python
@pytest.mark.asyncio
async def test_event_stream_does_not_synthesize_text_boundaries():
    buses = BusRegistry()
    bus = buses.get_or_create("sess-1")
    await bus.emit(TextMessageStartEvent(message_id="text-1"))
    await bus.emit(TextDeltaEvent(message_id="text-1", delta="hello"))
    await bus.emit(TextMessageEndEvent(message_id="text-1", content="hello"))

    stream = _event_stream("sess-1", buses, RunRegistry(), stream=True)
    try:
        chunks = [await anext(stream), await anext(stream), await anext(stream)]
    finally:
        await stream.aclose()

    payloads = [json.loads(chunk.split("data: ", 1)[1].strip()) for chunk in chunks]
    assert [payload["type"] for payload in payloads] == [
        "TEXT_MESSAGE_START",
        "TEXT_MESSAGE_CONTENT",
        "TEXT_MESSAGE_END",
    ]
    assert [payload["message_id"] for payload in payloads] == ["text-1", "text-1", "text-1"]
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
cd apps/server && uv run pytest tests/test_streaming_events.py -q
```

Expected: failure because `TextStarted` and `TextEnded` do not exist yet.

- [ ] **Step 3: Define text boundary agent events**

In `apps/server/arden/agent/types/events.py`, add these dataclasses after `AgentEventBase`:

```python
@dataclass(frozen=True, kw_only=True)
class TextStarted(AgentEventBase):
    message_id: str


@dataclass(frozen=True, kw_only=True)
class TextEnded(AgentEventBase):
    message_id: str
    content: str = ""
```

Update `apps/server/arden/agent/types/__init__.py` exports if that file explicitly exports event classes. The complete import addition is:

```python
from arden.agent.types.events import TextEnded, TextStarted
```

- [ ] **Step 4: Emit text boundaries from the agent**

In `apps/server/arden/agent/agent.py`, import `TextStarted` and `TextEnded`. In `_call_llm`, replace the string delta handling block with:

```python
                if isinstance(item, str):
                    if not text_started:
                        text_started = True
                        yield TextStarted(
                            depth=self.current_depth,
                            parent_id=self.parent_id,
                            message_id=text_id,
                        )
                    yield TextDelta(
                        depth=self.current_depth,
                        parent_id=self.parent_id,
                        message_id=text_id,
                        content=item,
                    )
```

After `assistant_msg = normalize_assistant_message(response.choices[0].message)`, add:

```python
        if text_started:
            final_text = response.choices[0].message.content or ""
            yield TextEnded(
                depth=self.current_depth,
                parent_id=self.parent_id,
                message_id=text_id,
                content=final_text,
            )
```

Keep the existing `assistant_msg["client_id"] = text_id` logic unchanged.

- [ ] **Step 5: Convert boundary agent events to SSE**

In `apps/server/arden/events/sse.py`, import `TextStarted` and `TextEnded`, then add cases before `TextDelta()`:

```python
        case TextStarted():
            return (
                TextMessageStartEvent(
                    message_id=event.message_id,
                    role="assistant",
                ),
            )
        case TextEnded():
            return (
                TextMessageEndEvent(
                    message_id=event.message_id,
                    content=event.content,
                ),
            )
```

- [ ] **Step 6: Remove subscriber-local boundary synthesis**

In `apps/server/arden/server/routers/chat.py`, simplify `_event_stream` so it only replays/yields event strings. Replace the body after subscribe with:

```python
    try:
        for event in snapshot:
            yield event.to_sse_string()
            await asyncio.sleep(0)

        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=0.5)
            except TimeoutError:
                if time.monotonic() - last_event_at >= KEEPALIVE_INTERVAL:
                    last_event_at = time.monotonic()
                    yield SSE_KEEPALIVE
                continue

            if event is None:
                break

            if not stream and isinstance(event, TextDeltaEvent):
                last_event_at = time.monotonic()
                continue

            last_event_at = time.monotonic()
            yield event.to_sse_string()
            await asyncio.sleep(0)
    except asyncio.CancelledError:
        pass
```

Remove imports for boundary classes that are no longer needed by the router transform.

- [ ] **Step 7: Run server stream tests**

Run:

```bash
cd apps/server && uv run pytest tests/test_streaming_events.py tests/test_agent.py tests/test_agent_lib.py -q
```

Expected: all tests pass.

- [ ] **Step 8: Commit**

Run:

```bash
git add apps/server/arden/agent apps/server/arden/events/sse.py apps/server/arden/server/routers/chat.py apps/server/tests/test_streaming_events.py
git commit -m "feat: emit explicit text stream boundaries"
```

---

### Task 3: Add Sequenced Stream Records

**Files:**
- Modify: `apps/server/arden/events/sse.py`
- Modify: `apps/server/arden/server/bus.py`
- Modify: `apps/server/arden/server/routers/chat.py`
- Test: `apps/server/tests/test_session_bus.py`
- Test: `apps/server/tests/test_streaming_events.py`

- [ ] **Step 1: Write sequence serialization tests**

Append this test to `apps/server/tests/test_streaming_events.py`:

```python
from arden.events.sse import StreamRecord, ThinkingEvent


def test_stream_record_adds_sequence_and_sse_id():
    record = StreamRecord(
        session_id="sess-1",
        seq=7,
        event=ThinkingEvent(status="processing"),
    )

    frame = record.to_sse_string()
    assert frame.startswith("id: 7\n")
    payload = json.loads(frame.split("data: ", 1)[1].strip())
    assert payload["seq"] == 7
    assert payload["session_id"] == "sess-1"
    assert payload["type"] == "thinking"
```

Append this test to `apps/server/tests/test_session_bus.py`:

```python
@pytest.mark.asyncio
async def test_session_bus_assigns_monotonic_sequence_numbers():
    bus = SessionBus(session_id="sess-1")
    await bus.emit(ThinkingEvent(status="one"))
    await bus.emit(ThinkingEvent(status="two"))

    snapshot, _queue = bus.subscribe_with_replay(after_seq=0)

    assert [record.seq for record in snapshot] == [1, 2]
    assert [record.event.status for record in snapshot] == ["one", "two"]
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
cd apps/server && uv run pytest tests/test_session_bus.py tests/test_streaming_events.py -q
```

Expected: failure because `StreamRecord` and `after_seq` do not exist.

- [ ] **Step 3: Add `StreamRecord`**

In `apps/server/arden/events/sse.py`, add this dataclass after `SSEEvent`:

```python
@dataclass(frozen=True)
class StreamRecord:
    session_id: str
    seq: int
    event: SSEEvent

    def to_sse(self) -> dict:
        sse = self.event.to_sse()
        payload = json.loads(sse["data"])
        payload["session_id"] = self.session_id
        payload["seq"] = self.seq
        return {
            "id": str(self.seq),
            "event": sse["event"],
            "data": json.dumps(payload),
        }

    def to_sse_string(self) -> str:
        sse = self.to_sse()
        return f"id: {sse['id']}\nevent: {sse['event']}\ndata: {sse['data']}\n\n"
```

- [ ] **Step 4: Sequence events in `SessionBus`**

In `apps/server/arden/server/bus.py`, change the queue and buffer types from `SSEEvent` to `StreamRecord`.

Add this field to `SessionBus`:

```python
    _next_seq: int = 1
```

Replace `emit` with:

```python
    async def emit(self, event: SSEEvent) -> None:
        record = StreamRecord(session_id=self.session_id, seq=self._next_seq, event=event)
        self._next_seq += 1
        self._recent.append(record)
        for queue in tuple(self._subscribers):
            try:
                queue.put_nowait(record)
            except asyncio.QueueFull:
                self._close_slow_subscriber(queue)
```

Replace `subscribe_with_replay` with:

```python
    def subscribe_with_replay(
        self,
        *,
        after_seq: int = 0,
    ) -> tuple[list[StreamRecord], asyncio.Queue[StreamRecord | None]]:
        snapshot = [record for record in self._recent if record.seq > after_seq]
        queue: asyncio.Queue[StreamRecord | None] = asyncio.Queue(maxsize=self.subscriber_queue_size)
        self._subscribers.append(queue)
        return snapshot, queue
```

Update `subscribe` and `_close_queue` annotations to `StreamRecord | None`.

- [ ] **Step 5: Update `_event_stream` to use records**

In `apps/server/arden/server/routers/chat.py`, change `_event_stream` signature:

```python
async def _event_stream(
    session_id: str,
    bus_registry: BusRegistry,
    run_registry: RunRegistry,
    stream: bool = False,
    after_seq: int = 0,
) -> AsyncGenerator[str]:
```

Subscribe with:

```python
    snapshot, queue = bus.subscribe_with_replay(after_seq=after_seq)
```

When filtering text deltas, inspect `record.event`:

```python
            if not stream and isinstance(record.event, TextDeltaEvent):
                last_event_at = time.monotonic()
                continue
```

Yield records with:

```python
            yield record.to_sse_string()
```

- [ ] **Step 6: Add `after_seq` to the route**

Change `chat_events` signature:

```python
async def chat_events(
    session_id: str,
    stream: bool = False,
    after_seq: int = 0,
    buses: BusRegistry = Depends(get_bus_registry),
    run_registry: RunRegistry = Depends(require_run_registry),
):
```

Pass `after_seq=after_seq` into `_event_stream`.

- [ ] **Step 7: Run focused server tests**

Run:

```bash
cd apps/server && uv run pytest tests/test_session_bus.py tests/test_streaming_events.py tests/test_chat_inject.py -q
```

Expected: all tests pass.

- [ ] **Step 8: Commit**

Run:

```bash
git add apps/server/arden/events/sse.py apps/server/arden/server/bus.py apps/server/arden/server/routers/chat.py apps/server/tests/test_session_bus.py apps/server/tests/test_streaming_events.py
git commit -m "feat: sequence chat stream events"
```

---

### Task 4: Make Desktop Consume Sequence IDs

**Files:**
- Modify: `apps/desktop/src/api.ts`
- Modify: `apps/desktop/src/store.ts`
- Modify: `apps/desktop/src/hooks/useEvents.ts`
- Modify: `apps/desktop/electron/main.cjs`
- Test: `apps/desktop/tests/streamOrdering.test.ts`

- [ ] **Step 1: Write duplicate and stale event tests**

Append this test to `apps/desktop/tests/streamOrdering.test.ts`:

```ts
test("ignores duplicate or stale sequenced events", () => {
  handleServerEvent({
    type: "RUN_STARTED",
    session_id: "session-1",
    run_id: "run-1",
    seq: 1,
    timestamp: 1,
  });
  handleServerEvent({
    type: "TEXT_MESSAGE_START",
    session_id: "session-1",
    message_id: "assistant-1",
    seq: 2,
    timestamp: 2,
  });
  handleServerEvent({
    type: "TEXT_MESSAGE_CONTENT",
    session_id: "session-1",
    message_id: "assistant-1",
    delta: "hello",
    seq: 3,
    timestamp: 3,
  });
  handleServerEvent({
    type: "TEXT_MESSAGE_CONTENT",
    session_id: "session-1",
    message_id: "assistant-1",
    delta: " duplicate",
    seq: 3,
    timestamp: 4,
  });

  const state = getState();
  expect(state.messages.get("assistant-1")?.content).toBe("hello");
  expect(state.lastEventSeqBySession.get("session-1")).toBe(3);
});
```

- [ ] **Step 2: Run test to verify failure**

Run:

```bash
bun test apps/desktop/tests/streamOrdering.test.ts
```

Expected: failure because the store does not track last event seq.

- [ ] **Step 3: Add envelope fields to `ServerEvent`**

In `apps/desktop/src/api.ts`, change the timestamp helper type to:

```ts
type WithEnvelope = {
  timestamp?: number;
  session_id?: string;
  run_id?: string;
  seq?: number;
};
```

Then change:

```ts
export type ServerEvent = WithTs & (
```

to:

```ts
export type ServerEvent = WithEnvelope & (
```

- [ ] **Step 4: Add sequence state**

In `apps/desktop/src/store.ts`, add to `State`:

```ts
  lastEventSeqBySession: Map<string, number>;
```

Add to initial state:

```ts
  lastEventSeqBySession: new Map(),
```

In `setCurrentSession`, keep the map; do not reset it. The map is per session and survives navigation.

- [ ] **Step 5: Gate duplicate events in `handleServerEvent`**

In `apps/desktop/src/hooks/useEvents.ts`, add this helper above `handleServerEvent`:

```ts
function shouldApplySequencedEvent(event: ServerEvent): boolean {
  if (!event.session_id || typeof event.seq !== "number") return true;
  const state = getState();
  const last = state.lastEventSeqBySession.get(event.session_id) ?? 0;
  if (event.seq <= last) return false;
  const next = new Map(state.lastEventSeqBySession);
  next.set(event.session_id, event.seq);
  setState({ lastEventSeqBySession: next });
  return true;
}
```

At the top of `handleServerEvent`, add:

```ts
  if (!shouldApplySequencedEvent(event)) return;
```

- [ ] **Step 6: Use the cursor when opening SSE in browser mode**

In `useEvents`, before `fetch`, compute:

```ts
          const afterSeq = getState().lastEventSeqBySession.get(sessionId) ?? 0;
```

Change the URL to:

```ts
            `${config.serverUrl}/chat/events/${encodeURIComponent(sessionId)}?stream=true&after_seq=${afterSeq}`,
```

- [ ] **Step 7: Use the cursor in Electron mode**

In `apps/desktop/electron/main.cjs`, update `streamEvents` so the URL includes `after_seq` from the request. Add this before creating the URL:

```js
    const afterSeq = Number(request.afterSeq || 0);
```

Then build:

```js
    const url = new URL(`/chat/events/${encodeURIComponent(sessionId)}?stream=true&after_seq=${afterSeq}`, config.serverUrl);
```

In `apps/desktop/electron/preload.cjs`, add `afterSeq` to the `events.connect` request payload. In `apps/desktop/src/hooks/useEvents.ts`, call:

```ts
        .connect(config, sessionId, getState().lastEventSeqBySession.get(sessionId) ?? 0)
```

If the preload typing requires an update, add:

```ts
connect(config: AppConfig, sessionId: string, afterSeq?: number): Promise<string>;
```

- [ ] **Step 8: Run desktop tests and typecheck**

Run:

```bash
bun test apps/desktop/tests/streamOrdering.test.ts apps/desktop/tests/streamEvents.test.ts
bun run --cwd apps/desktop typecheck
```

Expected: tests and typecheck pass.

- [ ] **Step 9: Commit**

Run:

```bash
git add apps/desktop/src/api.ts apps/desktop/src/store.ts apps/desktop/src/hooks/useEvents.ts apps/desktop/electron/main.cjs apps/desktop/electron/preload.cjs apps/desktop/src/global.d.ts apps/desktop/tests/streamOrdering.test.ts
git commit -m "feat: consume sequenced desktop events"
```

---

### Task 5: Fix Cancel Terminal Semantics

**Files:**
- Modify: `apps/server/arden/server/state.py`
- Modify: `apps/server/arden/server/routers/chat.py`
- Modify: `apps/server/arden/services/chat.py`
- Modify: `apps/server/arden/tools/core/context.py`
- Test: `apps/server/tests/test_run_state.py`
- Test: `apps/server/tests/test_chat_inject.py`

- [ ] **Step 1: Write run registry cancellation tests**

Append to `apps/server/tests/test_run_state.py`:

```python
def test_cancel_run_reports_missing_run():
    registry = RunRegistry()

    result = registry.cancel_run("missing")

    assert result["found"] is False
    assert result["cancel_requested"] is False


def test_cancel_run_marks_running_run_cancelled():
    registry = RunRegistry()
    run = registry.create_run("sess-1")
    run.status = RunStatus.RUNNING

    result = registry.cancel_run(run.run_id)

    assert result["found"] is True
    assert result["cancel_requested"] is True
    assert run.cancelled is True
    assert run.status == RunStatus.CANCELLED
    assert registry.get_active_run("sess-1") is None
```

- [ ] **Step 2: Write API cancellation tests**

Append to `apps/server/tests/test_chat_inject.py`:

```python
def test_cancel_returns_404_for_unknown_run(client_no_active_run):
    resp = client_no_active_run.post("/cancel", json={"run_id": "missing"})

    assert resp.status_code == 404
    assert resp.json()["detail"] == "Run not found"


def test_cancel_returns_202_for_running_run(client_with_active_run):
    c, run = client_with_active_run

    resp = c.post("/cancel", json={"run_id": run.run_id})

    assert resp.status_code == 202
    assert resp.json()["status"] == "cancelling"
    assert run.cancelled is True
```

- [ ] **Step 3: Run tests to verify failure**

Run:

```bash
cd apps/server && uv run pytest tests/test_run_state.py tests/test_chat_inject.py -q
```

Expected: failure because `cancel_run` returns `None` and the route always returns 200.

- [ ] **Step 4: Make `RunRegistry.cancel_run` return a result**

Replace `cancel_run` in `apps/server/arden/server/state.py` with:

```python
    def cancel_run(self, run_id: str) -> dict[str, bool]:
        run = self._runs.get(run_id)
        if not run:
            return {"found": False, "cancel_requested": False}

        run.cancelled = True
        run.status = RunStatus.CANCELLED
        run.updated_at = datetime.now(UTC)
        self._active_by_session.pop(run.session_id, None)

        cancel_requested = False
        if run.task and not run.task.done():
            run.task.cancel()
            cancel_requested = True
        if run.drain_task and not run.drain_task.done():
            run.drain_task.cancel()
            cancel_requested = True

        registry = self._bg_registries.get(run.session_id)
        if registry:
            for _task_id, _command in registry.cancel_all():
                cancel_requested = True

        self.cleanup_old_runs()
        return {"found": True, "cancel_requested": cancel_requested}
```

- [ ] **Step 5: Make `/cancel` report accepted or missing**

Replace the route in `apps/server/arden/server/routers/chat.py` with:

```python
@router.post("/cancel", status_code=202)
async def cancel_run(request: CancelRequest, run_registry: RunRegistry = Depends(require_run_registry)):
    result = run_registry.cancel_run(request.run_id)
    if not result["found"]:
        raise HTTPException(status_code=404, detail="Run not found")
    return {"status": "cancelling", **result}
```

- [ ] **Step 6: Stop emitting `RunCompleted` for cancelled runs**

In `apps/server/arden/services/chat.py`, in the `finally` block before creating `RunCompleted`, add:

```python
            if run.cancelled:
                return
```

Place it after `bus.clear_buffer()` and before:

```python
            event = RunCompleted(
```

This preserves final session save but prevents memory extraction and count-trigger automations from treating cancellation as completion.

- [ ] **Step 7: Run focused cancellation tests**

Run:

```bash
cd apps/server && uv run pytest tests/test_run_state.py tests/test_chat_inject.py tests/test_runtime_outbox.py -q
```

Expected: all tests pass.

- [ ] **Step 8: Commit**

Run:

```bash
git add apps/server/arden/server/state.py apps/server/arden/server/routers/chat.py apps/server/arden/services/chat.py apps/server/tests/test_run_state.py apps/server/tests/test_chat_inject.py
git commit -m "fix: make chat cancellation terminal-safe"
```

---

### Task 6: Add Sub-Agent Task Lifecycle Events

**Files:**
- Modify: `apps/server/arden/events/sse.py`
- Modify: `apps/server/arden/core/spawner.py`
- Modify: `apps/desktop/src/api.ts`
- Modify: `apps/desktop/src/store.ts`
- Modify: `apps/desktop/src/hooks/useEvents.ts`
- Modify: `apps/desktop/src/components/trace/ActivityTrace.tsx`
- Test: `apps/server/tests/test_streaming_events.py`
- Test: `apps/server/tests/test_spawn_salvage.py`
- Test: `apps/desktop/tests/streamEvents.test.ts`

- [ ] **Step 1: Write server task lifecycle conversion test**

Append to `apps/server/tests/test_streaming_events.py`:

```python
from arden.events.sse import TaskFinishedEvent, TaskStartedEvent


def test_task_lifecycle_events_include_parent_tool_call():
    start = TaskStartedEvent(
        run_id="run-1",
        task_id="call-research",
        parent_tool_call_id="call-research",
        name="Research",
        summary="look up event systems",
        depth=1,
    )
    done = TaskFinishedEvent(
        run_id="run-1",
        task_id="call-research",
        parent_tool_call_id="call-research",
        status="completed",
        summary="done",
        depth=1,
    )

    start_payload = json.loads(start.to_sse()["data"])
    done_payload = json.loads(done.to_sse()["data"])

    assert start_payload["type"] == "task_started"
    assert start_payload["task_id"] == "call-research"
    assert start_payload["parent_tool_call_id"] == "call-research"
    assert done_payload["type"] == "task_finished"
    assert done_payload["status"] == "completed"
```

- [ ] **Step 2: Write desktop task lifecycle reducer test**

Append to `apps/desktop/tests/streamEvents.test.ts`:

```ts
test("updates an agent activity item from task lifecycle events", () => {
  handleServerEvent({ type: "RUN_STARTED", run_id: "run-1", session_id: "session-1", timestamp: 1 });
  handleServerEvent({
    type: "TOOL_CALL_START",
    tool_call_id: "call-research",
    tool_call_name: "research",
    kind: "agent",
    description: "research(task='event systems')",
    timestamp: 2,
  });
  handleServerEvent({ type: "TOOL_CALL_END", tool_call_id: "call-research", timestamp: 3 });
  handleServerEvent({
    type: "task_started",
    run_id: "run-1",
    task_id: "call-research",
    parent_tool_call_id: "call-research",
    name: "Research",
    summary: "event systems",
    depth: 1,
    timestamp: 4,
  });
  handleServerEvent({
    type: "task_finished",
    run_id: "run-1",
    task_id: "call-research",
    parent_tool_call_id: "call-research",
    status: "completed",
    summary: "done",
    depth: 1,
    timestamp: 5,
  });

  const state = getState();
  const activityId = state.order.find((id) => state.messages.get(id)?.role === "activity");
  const item = state.messages.get(activityId!)?.activity?.items.find((it) => it.id === "call-research");
  expect(item?.taskStatus).toBe("completed");
  expect(item?.progress).toBe("done");
});
```

- [ ] **Step 3: Run tests to verify failure**

Run:

```bash
cd apps/server && uv run pytest tests/test_streaming_events.py -q
bun test apps/desktop/tests/streamEvents.test.ts
```

Expected: failures because task events and desktop fields do not exist.

- [ ] **Step 4: Add task SSE events**

In `apps/server/arden/events/sse.py`, add enum values:

```python
    TASK_STARTED = "task_started"
    TASK_PROGRESS = "task_progress"
    TASK_FINISHED = "task_finished"
```

Add dataclasses near `BackgroundTaskEvent`:

```python
@dataclass(frozen=True)
class TaskStartedEvent(SSEEvent):
    type: EventType = field(default=EventType.TASK_STARTED, init=False)
    run_id: str = ""
    task_id: str = ""
    parent_task_id: str | None = None
    parent_tool_call_id: str | None = None
    name: str = ""
    summary: str = ""
    depth: int = 0


@dataclass(frozen=True)
class TaskProgressEvent(SSEEvent):
    type: EventType = field(default=EventType.TASK_PROGRESS, init=False)
    run_id: str = ""
    task_id: str = ""
    parent_task_id: str | None = None
    parent_tool_call_id: str | None = None
    status: str = "running"
    summary: str = ""
    depth: int = 0


@dataclass(frozen=True)
class TaskFinishedEvent(SSEEvent):
    type: EventType = field(default=EventType.TASK_FINISHED, init=False)
    run_id: str = ""
    task_id: str = ""
    parent_task_id: str | None = None
    parent_tool_call_id: str | None = None
    status: str = "completed"
    summary: str = ""
    depth: int = 0
```

- [ ] **Step 5: Emit task lifecycle around sub-agent runs**

In `apps/server/arden/core/spawner.py`, import `TaskFinishedEvent` and `TaskStartedEvent`.

In `spawn_child`, after `parent_emit = calling_ctx.io.emit if not silent else None`, add:

```python
        task_id = parent_id or f"task-{uuid4().hex[:10]}"
        task_summary = task[:120]
```

Before running the foreground child, emit start:

```python
        if parent_emit and not background:
            await parent_emit(
                TaskStartedEvent(
                    run_id=calling_ctx.run.run_id,
                    task_id=task_id,
                    parent_tool_call_id=parent_id,
                    name="Sub-agent",
                    summary=task_summary,
                    depth=current_depth + 1,
                )
            )
```

Wrap the foreground `_stream_to` call so it emits terminal task status:

```python
        if not background:
            try:
                text = await asyncio.wait_for(_stream_to(_foreground_child_events), timeout=timeout)
                if parent_emit:
                    await parent_emit(
                        TaskFinishedEvent(
                            run_id=calling_ctx.run.run_id,
                            task_id=task_id,
                            parent_tool_call_id=parent_id,
                            status="completed",
                            summary="completed",
                            depth=current_depth + 1,
                        )
                    )
                return text
            except asyncio.CancelledError:
                if parent_emit:
                    await parent_emit(
                        TaskFinishedEvent(
                            run_id=calling_ctx.run.run_id,
                            task_id=task_id,
                            parent_tool_call_id=parent_id,
                            status="cancelled",
                            summary="cancelled",
                            depth=current_depth + 1,
                        )
                    )
                raise
            except TimeoutError:
                if parent_emit:
                    await parent_emit(
                        TaskFinishedEvent(
                            run_id=calling_ctx.run.run_id,
                            task_id=task_id,
                            parent_tool_call_id=parent_id,
                            status="failed",
                            summary=f"timed out after {timeout}s",
                            depth=current_depth + 1,
                        )
                    )
                _logger.warning("Sub-agent timed out after %ss, salvaging", timeout)
                summary = await _salvage_summary(
                    child_model, child_messages, f"timed out after {timeout}s", task
                )
                if summary:
                    return f"[partial - sub-agent timed out after {timeout}s]\n\n{summary}"
                return _deterministic_salvage(child_messages, f"timed out after {timeout}s")
```

Remove the old foreground `try/except TimeoutError` block so there is exactly one foreground branch.

- [ ] **Step 6: Add desktop task event types**

In `apps/desktop/src/api.ts`, add these variants to `ServerEvent`:

```ts
  | { type: "task_started"; run_id: string; task_id: string; parent_task_id?: string | null; parent_tool_call_id?: string | null; name?: string; summary?: string; depth?: number }
  | { type: "task_progress"; run_id: string; task_id: string; parent_task_id?: string | null; parent_tool_call_id?: string | null; status?: string; summary?: string; depth?: number }
  | { type: "task_finished"; run_id: string; task_id: string; parent_task_id?: string | null; parent_tool_call_id?: string | null; status: "completed" | "failed" | "cancelled"; summary?: string; depth?: number }
```

In `apps/desktop/src/store.ts`, add to `ActivityItem`:

```ts
  taskStatus?: "running" | "completed" | "failed" | "cancelled";
  progress?: string;
```

- [ ] **Step 7: Handle task events in desktop reducer**

In `apps/desktop/src/hooks/useEvents.ts`, add cases:

```ts
    case "task_started": {
      const patch: Partial<ActivityItem> = {
        taskStatus: "running",
        progress: event.summary ?? "running",
        depth: event.depth || undefined,
        parentToolId: event.parent_tool_call_id ?? event.parent_task_id ?? undefined,
      };
      if (!s.mergeActivityItem(event.task_id, patch)) pendingResultPatches.set(event.task_id, patch);
      return;
    }
    case "task_progress": {
      const patch: Partial<ActivityItem> = {
        taskStatus: event.status === "failed" ? "failed" : "running",
        progress: event.summary ?? event.status ?? "running",
      };
      if (!s.mergeActivityItem(event.task_id, patch)) pendingResultPatches.set(event.task_id, patch);
      return;
    }
    case "task_finished": {
      const patch: Partial<ActivityItem> = {
        taskStatus: event.status,
        progress: event.summary ?? event.status,
      };
      if (!s.mergeActivityItem(event.task_id, patch)) pendingResultPatches.set(event.task_id, patch);
      return;
    }
```

- [ ] **Step 8: Render task status on agent rows**

In `apps/desktop/src/components/trace/ActivityTrace.tsx`, inside `AgentButton`, after `const label = friendlyAgentLabel(item.kind);`, add:

```ts
  const status = item.taskStatus ?? (item.result == null ? "running" : "completed");
  const statusText = item.progress ?? status;
```

After the task span, add:

```tsx
      <span
        className={clsx(
          "text-faint shrink-0",
          status === "failed" && "text-bad",
          status === "cancelled" && "text-bad",
        )}
      >
        {statusText}
      </span>
```

- [ ] **Step 9: Run focused tests**

Run:

```bash
cd apps/server && uv run pytest tests/test_streaming_events.py tests/test_spawn_salvage.py -q
bun test apps/desktop/tests/streamEvents.test.ts
bun run --cwd apps/desktop typecheck
```

Expected: all tests pass.

- [ ] **Step 10: Commit**

Run:

```bash
git add apps/server/arden/events/sse.py apps/server/arden/core/spawner.py apps/server/tests/test_streaming_events.py apps/server/tests/test_spawn_salvage.py apps/desktop/src/api.ts apps/desktop/src/store.ts apps/desktop/src/hooks/useEvents.ts apps/desktop/src/components/trace/ActivityTrace.tsx apps/desktop/tests/streamEvents.test.ts
git commit -m "feat: stream sub-agent task lifecycle"
```

---

### Task 7: Add Cursor Replay Gap Handling

**Files:**
- Modify: `apps/server/arden/server/bus.py`
- Modify: `apps/server/arden/server/routers/chat.py`
- Modify: `apps/desktop/src/hooks/useEvents.ts`
- Test: `apps/server/tests/test_session_bus.py`
- Test: `apps/desktop/tests/streamOrdering.test.ts`

- [ ] **Step 1: Write server gap detection test**

Append to `apps/server/tests/test_session_bus.py`:

```python
def test_replay_reports_gap_when_after_seq_is_older_than_buffer():
    bus = SessionBus(session_id="sess-1")
    bus._recent.clear()
    bus._next_seq = 10

    snapshot, _queue, gap = bus.subscribe_with_replay(after_seq=3)

    assert snapshot == []
    assert gap is True
```

- [ ] **Step 2: Run test to verify failure**

Run:

```bash
cd apps/server && uv run pytest tests/test_session_bus.py -q
```

Expected: failure because `subscribe_with_replay` returns two values and has no gap signal.

- [ ] **Step 3: Return replay gap state**

In `apps/server/arden/server/bus.py`, replace `subscribe_with_replay` with:

```python
    def subscribe_with_replay(
        self,
        *,
        after_seq: int = 0,
    ) -> tuple[list[StreamRecord], asyncio.Queue[StreamRecord | None], bool]:
        oldest_seq = self._recent[0].seq if self._recent else self._next_seq
        gap = after_seq > 0 and after_seq < oldest_seq - 1
        snapshot = [record for record in self._recent if record.seq > after_seq]
        queue: asyncio.Queue[StreamRecord | None] = asyncio.Queue(maxsize=self.subscriber_queue_size)
        self._subscribers.append(queue)
        return snapshot, queue, gap
```

Update tests that unpack `subscribe_with_replay()` to use `_gap`.

- [ ] **Step 4: Emit a stream reset event on gap**

In `apps/server/arden/events/sse.py`, add:

```python
    STREAM_RESET = "stream_reset"
```

and:

```python
@dataclass(frozen=True)
class StreamResetEvent(SSEEvent):
    type: EventType = field(default=EventType.STREAM_RESET, init=False)
    reason: str = "replay_gap"
```

In `_event_stream`, unpack:

```python
    snapshot, queue, replay_gap = bus.subscribe_with_replay(after_seq=after_seq)
```

Before replaying snapshot, add:

```python
        if replay_gap:
            reset_record = StreamRecord(
                session_id=session_id,
                seq=after_seq,
                event=StreamResetEvent(reason="replay_gap"),
            )
            yield reset_record.to_sse_string()
```

- [ ] **Step 5: Make desktop reload history on replay gap**

In `apps/desktop/src/api.ts`, add:

```ts
  | { type: "stream_reset"; reason: "replay_gap" | string }
```

In `apps/desktop/src/hooks/useEvents.ts`, import `loadHistory` from `../actions` if it is not already available. Add this case:

```ts
    case "stream_reset":
      if (getState().currentSessionId) {
        void loadHistory(getState().currentSessionId);
      }
      resetStreamState();
      return;
```

- [ ] **Step 6: Run focused tests**

Run:

```bash
cd apps/server && uv run pytest tests/test_session_bus.py tests/test_streaming_events.py -q
bun test apps/desktop/tests/streamOrdering.test.ts
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

Run:

```bash
git add apps/server/arden/server/bus.py apps/server/arden/server/routers/chat.py apps/server/arden/events/sse.py apps/server/tests/test_session_bus.py apps/desktop/src/api.ts apps/desktop/src/hooks/useEvents.ts apps/desktop/tests/streamOrdering.test.ts
git commit -m "feat: detect chat stream replay gaps"
```

---

### Task 8: Add Final Contract Tests

**Files:**
- Create: `apps/server/tests/test_event_contract.py`
- Create: `apps/desktop/tests/eventContract.test.ts`

- [ ] **Step 1: Add server event contract tests**

Create `apps/server/tests/test_event_contract.py`:

```python
import json

from arden.events.sse import RunCancelledEvent, RunErrorEvent, RunFinishedEvent, RunStartedEvent, StreamRecord


def _payload(event):
    frame = StreamRecord(session_id="sess-1", seq=1, event=event).to_sse_string()
    return json.loads(frame.split("data: ", 1)[1].strip())


def test_terminal_events_identify_run_and_sequence():
    events = [
        RunStartedEvent(session_id="sess-1", run_id="run-1"),
        RunFinishedEvent(run_id="run-1"),
        RunCancelledEvent(run_id="run-1"),
        RunErrorEvent(run_id="run-1", message="failed"),
    ]

    for event in events:
        payload = _payload(event)
        assert payload["session_id"] == "sess-1"
        assert payload["seq"] == 1
        assert payload["run_id"] == "run-1"


def test_stream_record_uses_sse_id_as_cursor():
    frame = StreamRecord(session_id="sess-1", seq=44, event=RunFinishedEvent(run_id="run-1")).to_sse_string()

    assert frame.startswith("id: 44\n")
    assert "event: RUN_FINISHED\n" in frame
```

- [ ] **Step 2: Add desktop contract tests**

Create `apps/desktop/tests/eventContract.test.ts`:

```ts
import { beforeEach, expect, test } from "bun:test";
import { handleServerEvent, resetStreamStateForTest } from "../src/hooks/useEvents.js";
import { getState, setState } from "../src/store.js";

beforeEach(() => {
  resetStreamStateForTest();
  setState({
    messages: new Map(),
    order: [],
    activeActivityId: null,
    running: false,
    currentRunId: null,
    error: null,
    lastEventSeqBySession: new Map(),
  });
});

test("desktop applies terminal event once by sequence", () => {
  handleServerEvent({ type: "RUN_STARTED", session_id: "sess-1", run_id: "run-1", seq: 1 });
  handleServerEvent({ type: "RUN_FINISHED", session_id: "sess-1", run_id: "run-1", seq: 2 });
  handleServerEvent({ type: "RUN_FINISHED", session_id: "sess-1", run_id: "run-1", seq: 2 });

  const state = getState();
  expect(state.running).toBe(false);
  expect(state.currentRunId).toBe(null);
  expect(state.lastEventSeqBySession.get("sess-1")).toBe(2);
});

test("desktop preserves ordered text under sequenced content events", () => {
  handleServerEvent({ type: "RUN_STARTED", session_id: "sess-1", run_id: "run-1", seq: 1 });
  handleServerEvent({ type: "TEXT_MESSAGE_START", session_id: "sess-1", message_id: "text-1", seq: 2 });
  handleServerEvent({ type: "TEXT_MESSAGE_CONTENT", session_id: "sess-1", message_id: "text-1", delta: "a", seq: 3 });
  handleServerEvent({ type: "TEXT_MESSAGE_CONTENT", session_id: "sess-1", message_id: "text-1", delta: "b", seq: 4 });

  expect(getState().messages.get("text-1")?.content).toBe("ab");
});
```

- [ ] **Step 3: Run contract tests**

Run:

```bash
cd apps/server && uv run pytest tests/test_event_contract.py -q
bun test apps/desktop/tests/eventContract.test.ts
```

Expected: all tests pass.

- [ ] **Step 4: Run focused regression suite**

Run:

```bash
cd apps/server && uv run pytest tests/test_session_bus.py tests/test_streaming_events.py tests/test_run_state.py tests/test_chat_inject.py tests/test_event_contract.py -q
bun test apps/desktop/tests/streamEvents.test.ts apps/desktop/tests/streamOrdering.test.ts apps/desktop/tests/turnLayout.test.ts apps/desktop/tests/eventContract.test.ts
bun run --cwd apps/desktop typecheck
```

Expected: all tests and typecheck pass.

- [ ] **Step 5: Commit**

Run:

```bash
git add apps/server/tests/test_event_contract.py apps/desktop/tests/eventContract.test.ts
git commit -m "test: lock chat event stream contract"
```

---

### Task 9: Update Server/Desktop Docs

**Files:**
- Modify: `docs/api-reference/streaming.mdx`
- Modify: `docs/internal/backend-protocols.md`

- [ ] **Step 1: Update API streaming docs**

In `docs/api-reference/streaming.mdx`, add this section near the top:

```markdown
## Event envelope

Every chat stream event includes server-owned ordering metadata:

```json
{
  "type": "TEXT_MESSAGE_CONTENT",
  "session_id": "20260508_120000_000",
  "run_id": "cool-otter",
  "seq": 12,
  "timestamp": 1778241600000
}
```

The SSE `id` field is the same value as `seq`. Clients should reconnect with `after_seq=<last seen seq>` and ignore events whose sequence is not greater than the last applied sequence for that session.
```

Add this under cancellation docs:

```markdown
Cancellation is asynchronous. `POST /cancel` returns `202` when the run exists and cancellation has been requested. The stream emits `run_cancelled` when the worker acknowledges cancellation and the run reaches its terminal cancelled state.
```

- [ ] **Step 2: Update internal backend protocol docs**

In `docs/internal/backend-protocols.md`, update the Session SSE stream section with:

```markdown
Session SSE events are normalized before they enter the bus. The bus assigns the per-session `seq`, stores recent records for replay, and serializes SSE frames with `id: <seq>`. Route handlers do not synthesize text boundaries per subscriber; they only replay records and stream live records.

Reconnect contract:

1. Desktop tracks the last applied `seq` per session.
2. Desktop reconnects with `after_seq=<last applied seq>`.
3. Server replays records with greater sequence numbers when they are still buffered.
4. If the requested cursor is older than the buffer, server emits `stream_reset` and desktop reloads session history.
```

- [ ] **Step 3: Run docs grep for stale cancel claim**

Run:

```bash
rg -n "POST /cancel|cancelled|after_seq|Last-Event-ID|stream_reset" docs/api-reference docs/internal
```

Expected: docs mention asynchronous cancel and `after_seq`.

- [ ] **Step 4: Commit**

Run:

```bash
git add docs/api-reference/streaming.mdx docs/internal/backend-protocols.md
git commit -m "docs: document chat event stream contract"
```

---

## Self-Review

Spec coverage:

- Stable robust server event ordering: Tasks 2, 3, 7, 8.
- Desktop event rendering and ordering: Tasks 1, 4, 6, 8.
- Sub-agent progress: Task 6.
- Cancel behavior: Task 5.
- Non-desktop clients were listed out of scope.
- Intel dump: `docs/internal/event-system-intel-2026-05-08.md`.

Placeholder scan:

- The plan avoids forbidden placeholder markers and vague "add appropriate handling" steps.
- Every code step gives concrete code or a concrete exact command.

Type consistency:

- Server cursor field is `seq`.
- Route query parameter is `after_seq`.
- Desktop store field is `lastEventSeqBySession`.
- Sub-agent lifecycle uses `task_id`, `parent_task_id`, and `parent_tool_call_id`.
- Activity item status field is `taskStatus`.

## Execution Options

Plan complete and saved to `docs/superpowers/plans/2026-05-08-stable-event-system.md`.

Two execution options:

1. **Subagent-Driven (recommended)** - dispatch a fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** - execute tasks in this session using executing-plans, batch execution with checkpoints.
