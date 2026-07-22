# Compaction Rehydration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Compaction must preserve active control-plane refs: pending approvals, background tasks, and active plan reference. Loaded deferred tools are intentionally dropped after compaction.

**Architecture:** Keep message summarization separate from runtime-state rehydration. Add a small typed `CompactionState` snapshot that is captured before compaction, stored on the handoff message and `chat_compactions`, then restored onto the current `RunContext` after compaction. Do not persist live `asyncio.Task` or `Future` objects; persist stable refs only. Do not rehydrate `loaded_tools`; the model can call `load_tools` again if it still needs a deferred group.

**Tech Stack:** Python dataclasses/Pydantic-style dicts, existing `RunContext`, `IOBridge`, `BackgroundTaskRegistry`, `SessionStore`, pytest.

---

## File Structure

- Modify `apps/server/arden/tools/core/context.py`: add `active_plan_ref` and snapshot helpers on `RunContext`; add pending approval/background snapshot helpers.
- Modify `apps/server/arden/core/compactor.py`: embed structured `rehydration` metadata in the compacted handoff message.
- Modify `apps/server/arden/core/compaction_model_request_middleware.py`: capture and restore runtime state around compaction.
- Modify `apps/server/arden/context/store.py`: add `rehydration_state` JSON column to `chat_compactions`.
- Modify `apps/server/arden/services/session.py`: pass the optional `rehydration_state` into the store.
- Test `apps/server/tests/test_deferred_tools.py`: deferred tools are dropped while control-plane refs survive compaction.
- Test `apps/server/tests/test_compactor.py`: compacted handoff contains rehydration metadata.
- Test `apps/server/tests/test_session_store.py`: `chat_compactions.rehydration_state` round trips.

## Task 1: Runtime Snapshot Shape

**Files:**
- Modify: `apps/server/arden/tools/core/context.py`
- Test: `apps/server/tests/test_deferred_tools.py`

- [ ] **Step 1: Write failing test**

Add this test to `apps/server/tests/test_deferred_tools.py`:

```python
def test_run_context_rehydration_snapshot_round_trip():
    run = RunContext(
        run_id="run",
        deferred_tools_enabled=True,
        loaded_tools={"slack_search", "background"},
        loop_task_id="loop-1",
        active_plan_ref="plan:abc",
    )

    snapshot = run.to_rehydration_state(
        pending_approvals=["call-1"],
        background_tasks=[{"task_id": "bg-1", "command": "research"}],
    )

    restored = RunContext(run_id="run", deferred_tools_enabled=True)
    restored.apply_rehydration_state(snapshot)

    assert restored.loaded_tools == set()
    assert restored.loop_task_id == "loop-1"
    assert restored.active_plan_ref == "plan:abc"
    assert snapshot["pending_approval_ids"] == ["call-1"]
    assert snapshot["background_tasks"] == [{"task_id": "bg-1", "command": "research"}]
```

- [ ] **Step 2: Verify red**

Run:

```bash
cd apps/server && uv run pytest tests/test_deferred_tools.py::test_run_context_rehydration_snapshot_round_trip -q
```

Expected: fail because `active_plan_ref`, `to_rehydration_state`, or `apply_rehydration_state` does not exist.

- [ ] **Step 3: Implement minimal snapshot API**

Add to `RunContext` in `apps/server/arden/tools/core/context.py`:

```python
active_plan_ref: str | None = None

def to_rehydration_state(
    self,
    *,
    pending_approvals: list[str] | None = None,
    background_tasks: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    return {
        "pending_approval_ids": pending_approvals or [],
        "background_tasks": background_tasks or [],
        "active_plan_ref": self.active_plan_ref,
        "loop_task_id": self.loop_task_id,
    }

def apply_rehydration_state(self, state: dict[str, Any] | None) -> None:
    if not state:
        return
    active_plan_ref = state.get("active_plan_ref")
    self.active_plan_ref = active_plan_ref if isinstance(active_plan_ref, str) else None
    loop_task_id = state.get("loop_task_id")
    self.loop_task_id = loop_task_id if isinstance(loop_task_id, str) else None
```

- [ ] **Step 4: Verify green**

Run:

```bash
cd apps/server && uv run pytest tests/test_deferred_tools.py::test_run_context_rehydration_snapshot_round_trip -q
```

Expected: pass.

## Task 2: Capture Pending Approval And Background Refs

**Files:**
- Modify: `apps/server/arden/tools/core/context.py`
- Test: `apps/server/tests/test_deferred_tools.py`

- [ ] **Step 1: Write failing test**

Add:

```python
def test_tool_context_builds_compaction_rehydration_state():
    run = RunContext(run_id="run", loaded_tools={"slack_search"}, active_plan_ref="plan:abc")
    io = IOBridge(pending_approvals={"call-1": object()})  # type: ignore[dict-item]
    background = BackgroundTaskRegistry(session_id="s")
    background._commands["bg-1"] = "research"

    ctx = ToolContext(
        session_state=SessionState(session_id="s", started_at=datetime.now(UTC)),
        registry=_registry(),
        run=run,
        io=io,
        background_tasks=background,
    )

    assert ctx.to_rehydration_state() == {
        "pending_approval_ids": ["call-1"],
        "background_tasks": [{"task_id": "bg-1", "command": "research"}],
        "active_plan_ref": "plan:abc",
        "loop_task_id": None,
    }
```

- [ ] **Step 2: Verify red**

Run:

```bash
cd apps/server && uv run pytest tests/test_deferred_tools.py::test_tool_context_builds_compaction_rehydration_state -q
```

Expected: fail because `ToolContext.to_rehydration_state` does not exist.

- [ ] **Step 3: Implement helper**

Add to `BackgroundTaskRegistry`:

```python
def to_rehydration_refs(self) -> list[dict[str, str]]:
    return [
        {"task_id": task_id, "command": command}
        for task_id, command in sorted(self._commands.items())
    ]
```

Add to `ToolContext`:

```python
def to_rehydration_state(self) -> dict[str, Any]:
    return self.run.to_rehydration_state(
        pending_approvals=sorted((self.io.pending_approvals or {}).keys()),
        background_tasks=self.background_tasks.to_rehydration_refs(),
    )
```

- [ ] **Step 4: Verify green**

Run:

```bash
cd apps/server && uv run pytest tests/test_deferred_tools.py::test_tool_context_builds_compaction_rehydration_state -q
```

Expected: pass.

## Task 3: Store Rehydration Metadata In Compacted Messages

**Files:**
- Modify: `apps/server/arden/core/compactor.py`
- Test: `apps/server/tests/test_compactor.py`

- [ ] **Step 1: Write failing test**

Add:

```python
def test_build_compacted_messages_embeds_rehydration_state():
    messages = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "old", "message_id": "m1"},
        {"role": "assistant", "content": "new", "message_id": "m2"},
    ]
    state = {"active_plan_ref": "plan:abc", "pending_approval_ids": ["call-1"]}

    compacted = _build_compacted_messages(messages, 1, 2, "summary", rehydration_state=state)

    assert compacted[1]["compaction"]["rehydration"] == state
```

- [ ] **Step 2: Verify red**

Run:

```bash
cd apps/server && uv run pytest tests/test_compactor.py::test_build_compacted_messages_embeds_rehydration_state -q
```

Expected: fail because `_build_compacted_messages` has no `rehydration_state` argument.

- [ ] **Step 3: Implement metadata argument**

Change `_build_compacted_messages` signature:

```python
def _build_compacted_messages(
    messages: list[dict],
    start: int,
    end: int,
    summary: str,
    *,
    rehydration_state: dict | None = None,
) -> list[dict]:
```

Before returning:

```python
if rehydration_state:
    compaction["rehydration"] = rehydration_state
```

Thread the optional keyword through `compact_messages(...)` and `SummaryCompactor.maybe_compact(...)`.

- [ ] **Step 4: Verify green**

Run:

```bash
cd apps/server && uv run pytest tests/test_compactor.py::test_build_compacted_messages_embeds_rehydration_state -q
```

Expected: pass.

## Task 4: Drop Loaded Tools And Rehydrate Control State During Compaction

**Files:**
- Modify: `apps/server/arden/core/compaction_model_request_middleware.py`
- Modify: `apps/server/arden/core/factory.py`
- Modify: `apps/server/arden/core/spawner.py`
- Test: `apps/server/tests/test_deferred_tools.py`

- [ ] **Step 1: Replace the old expectation**

Replace `test_compaction_unloads_deferred_tools_after_current_request` with:

```python
@pytest.mark.asyncio
async def test_compaction_unloads_deferred_tools_but_rehydrates_control_state():
    registry = _registry()
    run = RunContext(
        run_id="run",
        deferred_tools_enabled=True,
        loaded_tools={"slack_search"},
        active_plan_ref="plan:abc",
    )
    deferred = DeferredToolsModelRequestMiddleware(registry=registry, run=run, get_services=dict)
    compaction = CompactionModelRequestMiddleware(
        compactor=AlwaysCompacts(),
        on_compact=run.loaded_tools.clear,
        get_rehydration_state=lambda: run.to_rehydration_state(),
        apply_rehydration_state=run.apply_rehydration_state,
    )

    async def compacting_next(req: ModelRequest) -> ModelRequest:
        return await compaction(req, _identity)

    prepared = await deferred(_request(registry), compacting_next)
    names = {t["function"]["name"] for t in prepared.tools}
    assert "slack_search" in names
    assert prepared.messages == [{"role": "system", "content": "compacted"}]
    assert run.loaded_tools == set()
    assert run.active_plan_ref == "plan:abc"

    next_prepared = await deferred(_request(registry), _identity)
    next_names = {t["function"]["name"] for t in next_prepared.tools}
    assert "slack_search" not in next_names
```

- [ ] **Step 2: Verify red**

Run:

```bash
cd apps/server && uv run pytest tests/test_deferred_tools.py::test_compaction_unloads_deferred_tools_but_rehydrates_control_state -q
```

Expected: fail because `CompactionModelRequestMiddleware` lacks `get_rehydration_state` and `apply_rehydration_state`.

- [ ] **Step 3: Implement middleware callbacks**

Update `CompactionModelRequestMiddleware.__init__`:

```python
get_rehydration_state: Callable[[], dict[str, Any]] | None = None,
apply_rehydration_state: Callable[[dict[str, Any] | None], None] | None = None,
```

Store both on `self`. Before `maybe_compact`, capture:

```python
rehydration_state = self.get_rehydration_state() if self.get_rehydration_state else None
```

Pass it to the compactor call if the compactor supports it by updating the local `Compactor` protocol and `SummaryCompactor`.

After compaction succeeds:

```python
if self.apply_rehydration_state:
    self.apply_rehydration_state(rehydration_state)
```

Keep `on_compact=run.loaded_tools.clear` in `apps/server/arden/core/factory.py` and `apps/server/arden/core/spawner.py`. Wire `get_rehydration_state=ctx.to_rehydration_state` and `apply_rehydration_state=run.apply_rehydration_state` so approval/background/plan refs survive without restoring deferred tools.

- [ ] **Step 4: Verify green**

Run:

```bash
cd apps/server && uv run pytest tests/test_deferred_tools.py -q
```

Expected: pass.

## Task 5: Persist Rehydration State With Compaction Rows

**Files:**
- Modify: `apps/server/arden/context/store.py`
- Modify: `apps/server/arden/services/session.py`
- Test: `apps/server/tests/test_session_store.py`

- [ ] **Step 1: Write failing test**

Extend `test_compaction_boundary_round_trip`:

```python
rehydration_state = {"active_plan_ref": "plan:abc", "pending_approval_ids": ["call-1"]}
await store.record_chat_compaction(
    compaction_id="compact-1",
    session_id="sess-1",
    boundary_seq=12,
    messages_before=20,
    messages_after=5,
    rehydration_state=rehydration_state,
)
compactions = await store.list_chat_compactions("sess-1")
assert compactions[0]["rehydration_state"] == rehydration_state
```

- [ ] **Step 2: Verify red**

Run:

```bash
cd apps/server && uv run pytest tests/test_session_store.py::test_compaction_boundary_round_trip -q
```

Expected: fail because `record_chat_compaction` does not accept or return `rehydration_state`.

- [ ] **Step 3: Implement store field**

In `CREATE TABLE chat_compactions`, add:

```sql
rehydration_state TEXT
```

Add migration:

```python
await self._execute("ALTER TABLE chat_compactions ADD COLUMN rehydration_state TEXT")
```

Wrap it in the same duplicate-column handling pattern used in this file.

Update insert/update to JSON-encode `rehydration_state`, and update `list_chat_compactions` to JSON-decode it.

- [ ] **Step 4: Verify green**

Run:

```bash
cd apps/server && uv run pytest tests/test_session_store.py::test_compaction_boundary_round_trip -q
```

Expected: pass.

## Task 6: Final Verification

- [ ] Run focused tests.

```bash
cd apps/server && uv run pytest tests/test_deferred_tools.py tests/test_compactor.py tests/test_session_store.py -q
```

Expected: pass.

- [ ] Run formatting/lint checks.

```bash
cd apps/server && uv run ruff check arden/core/compactor.py arden/core/compaction_model_request_middleware.py arden/tools/core/context.py arden/context/store.py arden/services/session.py
git diff --check
```

Expected: both pass.
