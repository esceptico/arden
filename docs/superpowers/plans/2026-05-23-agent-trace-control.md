# Agent Trace Control Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make compaction quiet and trace-local, make foreground subagents cancellable with partial summaries, generate concise names for conversations and agents, and remove prompt duplication from subagent trace rows.

**Architecture:** Server events carry the owner of each transient action: top-level run or a specific subagent tool call. Desktop routes subagent-owned events into `ActivityItem` state and keeps global compaction UI non-persistent. Subagent cancellation is handled by a run-local handle registry so cancelling one child summarizes partial work without cancelling the parent run.

**Tech Stack:** Python FastAPI/asyncio server, Arden agent loop, SSE event contracts, React/Zustand desktop, Bun/Vitest-style tests, pytest.

---

## File Map

- Create `apps/server/arden/core/naming.py`: deterministic name helpers for chat sessions, foreground subagents, and background agents.
- Modify `apps/server/arden/events/sse.py`: add `scope` and `parent_tool_call_id` ownership fields to compaction events.
- Modify `apps/server/arden/core/compaction_model_request_middleware.py`: emit compaction events with owner metadata.
- Modify `apps/server/arden/core/spawner.py`: register foreground subagents, support cancel-with-summary, emit trace-local compaction, and use generated agent names.
- Modify `apps/server/arden/server/state.py`: store foreground subagent handles under each active run.
- Modify `apps/server/arden/server/routers/chat.py`: add `POST /chat/subagents/{tool_call_id}/cancel`.
- Modify `apps/server/arden/services/chat.py`: use generated conversation names and keep top-level compaction metadata run-scoped.
- Modify `apps/desktop/src/api.ts`: extend event types and add `cancelSubagentApi`.
- Modify `apps/desktop/src/store/types.ts`: add later task-specific `ActivityItem` fields for agent naming/cancel state.
- Modify `apps/desktop/src/store/chat-stream.ts`: route run-owned compaction to global state and agent-owned compaction into transcript projection.
- Modify `apps/desktop/src/store/session-cache.ts`: do not cache global compaction UI state across tab switches.
- Modify `apps/desktop/src/components/CompactionIndicator.tsx`: remove repeated auto-compaction finish toast; keep manual/status paths separate.
- Modify `apps/desktop/src/components/trace/ActivityTrace.tsx`: render generated subagent name only, not prompt text, and expose stop affordance.
- Modify `apps/desktop/src/components/ToolViewer.tsx`: keep prompt visible only in drill-down and add stop affordance there too.
- Test files: `apps/server/tests/test_streaming_events.py`, `apps/server/tests/test_spawn_salvage.py`, `apps/server/tests/test_chat_inject.py`, `apps/server/tests/test_naming.py`, `apps/desktop/tests/streamEvents.test.ts`, `apps/desktop/tests/activityTraceReplay.test.tsx`, `apps/desktop/tests/sessionCache.test.ts`, `apps/desktop/tests/compactionIndicator.test.ts`.

---

### Task 1: Route Compaction By Owner

**Files:**
- Modify: `apps/server/arden/events/sse.py`
- Modify: `apps/server/arden/core/compaction_model_request_middleware.py`
- Modify: `apps/server/arden/core/spawner.py`
- Modify: `apps/desktop/src/api.ts`
- Modify: `apps/desktop/src/store/chat-stream.ts`
- Modify: `apps/desktop/src/store/transcript-projection.ts`
- Test: `apps/server/tests/test_streaming_events.py`
- Test: `apps/server/tests/test_deferred_tools.py`
- Test: `apps/desktop/tests/streamEvents.test.ts`

- [x] **Step 1: Write backend event contract test**

Add tests proving compaction events serialize `scope="agent"` plus `parent_tool_call_id`, and reject invalid owner combinations.

- [x] **Step 2: Run backend test and verify red**

Expected red: compaction events initially did not accept owner metadata.

- [x] **Step 3: Write desktop projection tests**

Add tests proving agent-owned compaction does not set global `compacting`, updates the agent row progress, and buffers correctly if the compaction event arrives before the row exists.

- [x] **Step 4: Run desktop tests and verify red**

Expected red: compaction events initially only affected global compaction UI.

- [x] **Step 5: Implement server event ownership**

Add a strict owner contract:

```python
scope: Literal["run", "agent"] = "run"
parent_tool_call_id: str | None = None
```

`scope="agent"` requires `parent_tool_call_id`; `scope="run"` cannot include one.

- [x] **Step 6: Wire server owners**

Subagent compaction emits to the parent stream only when a foreground subagent has a parent tool-call row. Background and no-row subagents compact silently instead of creating invalid trace-local events.

- [x] **Step 7: Implement desktop projection**

Type compaction events as run-owned or agent-owned in `api.ts`. Keep run-owned compaction in `chat-stream.ts`; forward agent-owned compaction into `transcript-projection.ts`, where it uses `mergeOrBufferActivityPatch` to update the agent row.

- [x] **Step 8: Run owner-routing tests**

Run:

```bash
uv run pytest tests/test_streaming_events.py -q
uv run pytest tests/test_deferred_tools.py::test_spawned_agent_compaction_refreshes_deferred_schema -q
bun test apps/desktop/tests/streamEvents.test.ts
```

Expected: all pass.

---

### Task 2: Make Global Compaction UI One-Shot And Non-Cached

**Files:**
- Modify: `apps/desktop/src/store/session-cache.ts`
- Modify: `apps/desktop/src/store/chat-stream.ts`
- Modify: `apps/desktop/src/components/CompactionIndicator.tsx`
- Modify: `apps/desktop/src/actions/builtins.ts`
- Test: `apps/desktop/tests/sessionCache.test.ts`
- Test: `apps/desktop/tests/compactionIndicator.test.ts`

- [x] **Step 1: Write cache regression**

Add to `apps/desktop/tests/sessionCache.test.ts`:

```ts
test("session cache does not replay compaction UI state", () => {
  setState({
    currentSessionId: "session-1",
    compacting: true,
  });

  const cached = snapshotSession(getState());

  expect(cached.compacting).toBe(false);
});
```

- [x] **Step 2: Write indicator regression**

Add to `apps/desktop/tests/compactionIndicator.test.ts`:

```ts
test("compaction indicator does not render a finished compaction toast", () => {
  setState({ compacting: false });

  expect(renderToStaticMarkup(createElement(CompactionIndicator))).not.toContain(
    "Conversation compacted",
  );
});
```

- [x] **Step 3: Run tests and verify red**

Run:

```bash
bun test apps/desktop/tests/sessionCache.test.ts apps/desktop/tests/compactionIndicator.test.ts
```

Expected: fails because cache currently stores `compacting`.

- [x] **Step 4: Implement non-cached compaction state**

In `apps/desktop/src/store/session-cache.ts`, change `blankSessionView`, `snapshotSession`, and `normalizeCachedSessionState` so cached views always carry:

```ts
compacting: false,
```

In `apps/desktop/src/store/chat-stream.ts`, top-level `compaction_finished` should only call:

```ts
s.setCompacting(false);
```

Delete stale `lastCompaction` state and setters end-to-end; auto compaction has no stored finish artifact.

- [x] **Step 5: Keep manual compact feedback explicit**

Manual compact already appends status via `apps/desktop/src/actions/builtins.ts`. Keep that path as the user-visible confirmation:

```ts
appendStatus(result.message ?? "Context compacted.");
```

Do not use `CompactionIndicator` as the manual result surface.

- [x] **Step 6: Run tests**

Run:

```bash
bun test apps/desktop/tests/sessionCache.test.ts apps/desktop/tests/compactionIndicator.test.ts
```

Expected: pass.

---

### Task 3: Cancel Foreground Subagents With Partial Summary

**Files:**
- Modify: `apps/server/arden/server/state.py`
- Modify: `apps/server/arden/core/spawner.py`
- Modify: `apps/server/arden/server/routers/chat.py`
- Modify: `apps/server/arden/events/sse.py`
- Modify: `apps/desktop/src/api.ts`
- Modify: `apps/desktop/src/components/trace/ActivityTrace.tsx`
- Modify: `apps/desktop/src/components/ToolViewer.tsx`
- Modify: `apps/desktop/src/store/chat-stream.ts`
- Test: `apps/server/tests/test_spawn_salvage.py`
- Test: `apps/server/tests/test_chat_inject.py`
- Test: `apps/desktop/tests/activityTraceReplay.test.tsx`
- Test: `apps/desktop/tests/streamEvents.test.ts`

- [x] **Step 1: Write server cancellation unit test**

Add to `apps/server/tests/test_spawn_salvage.py`:

```python
@pytest.mark.asyncio
async def test_foreground_subagent_cancel_returns_partial_summary(monkeypatch):
    emitted = []

    async def emit(event):
        emitted.append(event)

    class SlowAgent:
        hooks = SimpleNamespace(on_response=None)

        async def stream(self, messages):
            messages.append({"role": "assistant", "content": "Found useful evidence."})
            await asyncio.sleep(60)

    monkeypatch.setattr(spawner_module, "Agent", lambda **kwargs: SlowAgent())
    monkeypatch.setattr(spawner_module, "_salvage_summary", AsyncMock(return_value="Partial summary."))

    registry = RunRegistry()
    parent_run = registry.create_run("sess-1")
    ctx = ToolContext(
        registry=executor.registry,
        run=RunContext(run_id=parent_run.run_id, current_depth=0, max_depth=3),
        io=IOBridge(emit=emit),
        background_tasks=BackgroundTaskRegistry(session_id="sess-1"),
    )

    spawn = create_spawn_fn(executor=executor, model="test-model", max_depth=3, current_depth=0, run_registry=registry)
    task = asyncio.create_task(spawn(ctx, "research trace replay", system_prompt="sys", tools=[], parent_id="call-research"))
    await asyncio.sleep(0)

    result = registry.cancel_subagent(parent_run.run_id, "call-research")
    assert result == {"found": True, "cancel_requested": True}

    spawn_result = await task
    assert "partial" in spawn_result.text.lower()
    assert "Partial summary." in spawn_result.text
    assert any(getattr(event, "status", None) == "cancelled" for event in emitted)
```

- [x] **Step 2: Run server test and verify red**

Run:

```bash
uv run pytest apps/server/tests/test_spawn_salvage.py::test_foreground_subagent_cancel_returns_partial_summary -q
```

Expected: fails because foreground subagents are not registered or cancellable.

- [x] **Step 3: Implement subagent handles**

In `apps/server/arden/server/state.py`, add:

```python
@dataclass
class SubagentHandle:
    tool_call_id: str
    task: asyncio.Task
    cancel_requested: bool = False
```

Add to `RunState`:

```python
subagents: dict[str, SubagentHandle] = field(default_factory=dict)
```

Add to `RunRegistry`:

```python
def register_subagent(self, run_id: str, tool_call_id: str, task: asyncio.Task) -> None:
    run = self._runs.get(run_id)
    if not run:
        return
    run.subagents[tool_call_id] = SubagentHandle(tool_call_id=tool_call_id, task=task)

def finish_subagent(self, run_id: str, tool_call_id: str) -> None:
    run = self._runs.get(run_id)
    if run:
        run.subagents.pop(tool_call_id, None)

def cancel_subagent(self, run_id: str, tool_call_id: str) -> dict[str, bool]:
    run = self._runs.get(run_id)
    if not run:
        return {"found": False, "cancel_requested": False}
    handle = run.subagents.get(tool_call_id)
    if not handle:
        return {"found": False, "cancel_requested": False}
    if handle.cancel_requested or handle.task.done():
        return {"found": True, "cancel_requested": False}
    handle.cancel_requested = True
    handle.task.cancel()
    return {"found": True, "cancel_requested": True}
```

- [x] **Step 4: Wrap foreground subagent execution**

In `apps/server/arden/core/spawner.py`, run foreground `_stream_to` in a child task, register it, and distinguish parent cancellation from subagent cancellation:

```python
stream_task = asyncio.create_task(_stream_to(_foreground_child_events))
calling_ctx.run_registry.register_subagent(calling_ctx.run.run_id, lifecycle_task_id, stream_task)
try:
    text = await asyncio.wait_for(stream_task, timeout=timeout)
except asyncio.CancelledError:
    run_state = calling_ctx.run_registry.get_run(calling_ctx.run.run_id)
    handle = run_state.subagents.get(lifecycle_task_id) if run_state else None
    if not run_state or run_state.cancelled or not handle or not handle.cancel_requested:
        raise
    summary = await _salvage_summary(child_model, child_messages, "cancelled by user", task)
    text = f"[partial - sub-agent cancelled]\n\n{summary}" if summary else _deterministic_cancel_salvage(child_messages)
    stream_failed = True
finally:
    calling_ctx.run_registry.finish_subagent(calling_ctx.run.run_id, lifecycle_task_id)
```

Return a `SpawnResult` whose `text` is the partial summary so the parent receives that summary as the tool result.

- [x] **Step 5: Add API route**

In `apps/server/arden/server/routers/chat.py`:

```python
@router.post("/chat/subagents/{tool_call_id}/cancel", status_code=202)
async def cancel_subagent(
    tool_call_id: str,
    run_id: str,
    run_registry: RunRegistry = Depends(require_run_registry),
):
    result = run_registry.cancel_subagent(run_id, tool_call_id)
    if not result["found"]:
        raise HTTPException(status_code=404, detail="Subagent not found or already done")
    return {"status": "cancelling", **result}
```

- [x] **Step 6: Add desktop API and UI**

In `apps/desktop/src/api.ts`:

```ts
export async function cancelSubagentApi(
  config: AppConfig,
  runId: string,
  toolCallId: string,
): Promise<void> {
  await apiWithConfig(
    config,
    `/chat/subagents/${encodeURIComponent(toolCallId)}/cancel?run_id=${encodeURIComponent(runId)}`,
    { method: "POST" },
  );
}
```

In `ActivityTrace.tsx`, show a small stop icon button for running agent rows only. Stop propagation so clicking stop does not open the inspector.

In `ToolViewer.tsx`, show the same stop action in the agent header when the agent is running.

- [x] **Step 7: Add desktop cancellation tests**

Add to `apps/desktop/tests/activityTraceReplay.test.tsx`:

```ts
test("running agent row renders a stop control", () => {
  const html = renderToStaticMarkup(
    <ActivityTail
      items={[{
        id: "call-research",
        kind: "research",
        semanticKind: "agent",
        target: "research",
        status: "ongoing",
        taskStatus: "running",
        runId: "run-1",
      }]}
      max={3}
      motionDisabled
    />,
  );

  expect(html).toContain("Stop subagent");
});
```

Also add the inverse test: a generic `status: "ongoing"` agent row without lifecycle `taskStatus: "running"` and row-owned `runId` must not render the stop control.

Add to `apps/desktop/tests/streamEvents.test.ts`:

```ts
test("cancelled subagent lifecycle marks row executed", () => {
  handleServerEvent({ type: "RUN_STARTED", run_id: "run-1", session_id: "session-1" });
  handleServerEvent({ type: "TOOL_CALL_START", tool_call_id: "call-research", tool_call_name: "research", kind: "agent" });
  handleServerEvent({
    type: "task_finished",
    run_id: "run-1",
    task_id: "call-research",
    parent_tool_call_id: "call-research",
    status: "cancelled",
    summary: "partial summary ready",
  });

  const activityId = getState().order.find((id) => getState().messages.get(id)?.role === "activity");
  const item = getState().messages.get(activityId!)?.activity?.items[0];
  expect(item?.status).toBe("executed");
  expect(item?.taskStatus).toBe("cancelled");
  expect(item?.progress).toBe("partial summary ready");
});
```

- [x] **Step 8: Run cancellation checks**

Run:

```bash
uv run pytest apps/server/tests/test_spawn_salvage.py apps/server/tests/test_chat_inject.py -q
bun test apps/desktop/tests/activityTraceReplay.test.tsx apps/desktop/tests/streamEvents.test.ts
```

Expected: pass.

---

### Task 4: Generated Names For Conversations And Agents

**Files:**
- Create: `apps/server/arden/core/naming.py`
- Modify: `apps/server/arden/services/chat.py`
- Modify: `apps/server/arden/core/spawner.py`
- Modify: `apps/server/arden/events/sse.py`
- Modify: `apps/desktop/src/store/transcript-projection.ts`
- Modify: `apps/desktop/src/store/types.ts`
- Test: `apps/server/tests/test_naming.py`
- Test: `apps/desktop/tests/streamEvents.test.ts`

- [x] **Step 1: Write naming tests**

Create `apps/server/tests/test_naming.py`:

```python
from arden.core.naming import agent_name, conversation_name


def test_conversation_name_removes_request_filler():
    assert conversation_name("please research the replay animation root cause in desktop") == "Replay Animation Root Cause"


def test_conversation_name_handles_images():
    assert conversation_name("", has_images=True) == "Image Conversation"


def test_agent_name_prefixes_role_without_prompt_dump():
    assert agent_name("research", "inspect current eval/test harness opportunities") == "Research Eval Test Harness"
```

- [x] **Step 2: Run naming tests and verify red**

Run:

```bash
uv run pytest apps/server/tests/test_naming.py -q
```

Expected: fails because `arden.core.naming` does not exist.

- [x] **Step 3: Implement deterministic naming helper**

Create `apps/server/arden/core/naming.py`:

```python
import re

_STOPWORDS = {
    "a", "an", "and", "are", "can", "could", "for", "in", "into", "it", "me", "of",
    "on", "please", "the", "this", "to", "we", "with", "you",
}


def _words(text: str) -> list[str]:
    return [part.lower() for part in re.findall(r"[A-Za-z0-9]+", text)]


def _title(text: str, *, fallback: str, max_words: int) -> str:
    words = [word for word in _words(text) if word not in _STOPWORDS]
    selected = words[:max_words]
    if not selected:
        return fallback
    return " ".join(word.capitalize() for word in selected)


def conversation_name(text: str, *, has_images: bool = False) -> str:
    if not text.strip() and has_images:
        return "Image Conversation"
    return _title(text, fallback="New Conversation", max_words=5)


def agent_name(kind: str, task: str) -> str:
    role = _title(kind.replace("_agent", ""), fallback="Agent", max_words=1)
    topic = _title(task, fallback="Task", max_words=4)
    if topic == "Task":
        return role
    return f"{role} {topic}"
```

- [x] **Step 4: Use helper for chat session names**

In `apps/server/arden/services/chat.py`, replace:

```python
name_candidate = message.strip() or ("[image]" if images else "")
if not session_state.name and not is_init and name_candidate and not name_candidate.startswith("/"):
    session_state.name = name_candidate[:50]
```

with:

```python
if not session_state.name and not is_init and not message.strip().startswith("/"):
    session_state.name = conversation_name(message, has_images=bool(images))
```

- [x] **Step 5: Use helper for agents**

In `apps/server/arden/core/spawner.py`:

```python
agent_label = agent_name("sub-agent", task)
task_summary = task[:120]
```

For research calls, use the tool kind if available:

```python
agent_label = agent_name(tool_kind or "sub-agent", task)
```

Use `agent_label` in `TaskStartedEvent.name` and background `label`.

- [x] **Step 6: Preserve display name on desktop**

In `apps/desktop/src/store/transcript-projection.ts`, set `displayName` from `event.display_name` on tool start and from `event.name` on task lifecycle patches.

Use:

```ts
displayName: event.name ?? undefined,
```

in `task_started`, `task_progress`, and `task_finished` patches when present.

- [x] **Step 7: Run naming tests**

Run:

```bash
uv run pytest apps/server/tests/test_naming.py apps/server/tests/test_streaming_events.py -q
bun test apps/desktop/tests/streamEvents.test.ts --test-name-pattern "agent"
```

Expected: pass.

---

### Task 5: Remove Prompt Duplication From Trace Rows

**Files:**
- Modify: `apps/desktop/src/components/trace/ActivityTrace.tsx`
- Modify: `apps/desktop/src/components/ToolViewer.tsx`
- Test: `apps/desktop/tests/activityTraceReplay.test.tsx`

- [x] **Step 1: Write trace rendering test**

Add to `apps/desktop/tests/activityTraceReplay.test.tsx`:

```ts
test("agent trace row shows generated name but not prompt text", () => {
  const html = renderToStaticMarkup(
    <ActivityTail
      items={[{
        id: "call-research",
        kind: "research",
        semanticKind: "agent",
        target: "research",
        args: JSON.stringify({ task: "inspect current eval/test harness opportunities" }),
        displayName: "Research Eval Test Harness",
        status: "ongoing",
      }]}
      max={3}
      motionDisabled
    />,
  );

  expect(html).toContain("Research Eval Test Harness");
  expect(html).not.toContain("inspect current eval/test harness opportunities");
});
```

- [x] **Step 2: Run test and verify red**

Run:

```bash
bun test apps/desktop/tests/activityTraceReplay.test.tsx --test-name-pattern "generated name"
```

Expected: fails because `AgentButton` currently renders `extractTask(item.args)`.

- [x] **Step 3: Implement trace-only hiding**

In `AgentButton` in `apps/desktop/src/components/trace/ActivityTrace.tsx`:

```ts
const label = item.displayName ?? friendlyAgentLabel(item.kind);
```

Remove the task span from the trace row. Keep `statusText`, usage suffix, compacting text, and error/cancel status.

In `ToolViewer.tsx`, keep:

```ts
const task = useMemo(() => extractTask(item.args) ?? item.target, [item.args, item.target]);
```

This preserves the prompt in drill-down only.

- [x] **Step 4: Run trace tests**

Run:

```bash
bun test apps/desktop/tests/activityTraceReplay.test.tsx
```

Expected: pass.

---

### Task 6: Final Verification

**Files:**
- No new implementation files.

- [x] **Step 1: Run focused desktop suites**

Run:

```bash
bun test apps/desktop/tests/activityTraceReplay.test.tsx apps/desktop/tests/streamEvents.test.ts apps/desktop/tests/sessionCache.test.ts apps/desktop/tests/compactionIndicator.test.ts
```

Expected: all pass.

- [x] **Step 2: Run focused server suites**

Run:

```bash
uv run pytest apps/server/tests/test_streaming_events.py apps/server/tests/test_spawn_salvage.py apps/server/tests/test_chat_inject.py apps/server/tests/test_naming.py -q
```

Expected: all pass.

- [x] **Step 3: Run typecheck and build**

Run:

```bash
bun run --cwd apps/desktop typecheck
bun run --cwd apps/desktop build
git diff --check
```

Expected: typecheck passes, build exits 0, diff check is clean. Existing Vite chunk-size warnings are acceptable if unchanged.

- [x] **Step 4: Commit**

Run:

```bash
git add apps/server/arden apps/server/tests apps/desktop/src apps/desktop/tests docs/superpowers/plans/2026-05-23-agent-trace-control.md
git commit -m "Improve subagent trace controls"
```

Expected: one commit containing only the implementation and tests for this plan.

---

## Self-Review

- Covers one-shot/global compaction: Task 2.
- Covers subagent compaction in trace: Task 1.
- Covers stopping any foreground subagent with salvage summary: Task 3.
- Covers generated conversation and agent names: Task 4.
- Covers hiding subagent prompt from trace rows while keeping drill-down prompt: Task 5.
- No server behavior depends on desktop-only state.
- No subagent cancellation path should cancel the parent run unless the parent run itself is cancelled.
