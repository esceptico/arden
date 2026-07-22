# Restart-Resumable Approvals Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Resume an ordinary foreground chat run after a server restart when it was suspended at a tool approval, without repeating a completed or ambiguous state-changing action.

**Architecture:** Persist the assistant tool-call message before tool dispatch, treat approval decisions as immutable durable inputs, and rehydrate the same run from that checkpoint. Recovery may execute a tool only when its durable phase proves execution never began; an action found in `executing` is marked recovery-blocked and requires a new user-directed action.

**Tech Stack:** Python 3.12, asyncio, FastAPI, SQLite/aiosqlite, pytest/pytest-asyncio.

## Global Constraints

- V1 covers top-level foreground chat runs only; loops, background drains, structured-output runs, and child-agent joins remain interrupted after restart.
- Preserve `session_id`, `run_id`, assistant message, and `tool_call_id` across recovery.
- Never infer safety from tool names; use `ToolPolicy.action` and durable execution phase.
- Never auto-retry a state-changing tool whose durable phase reached `executing`.
- Existing live approval behavior and `/tools/result` request shape remain compatible.
- Do not touch the current unrelated desktop and memory worktree changes.

---

### Task 1: Make Approval and Execution Phases Durable

**Files:**
- Modify: `apps/server/arden/context/store.py`
- Modify: `apps/server/arden/services/session.py`
- Modify: `apps/server/arden/tools/core/context.py`
- Modify: `apps/server/arden/tools/core/middleware.py`
- Modify: `apps/server/arden/core/tool_executor.py`
- Test: `apps/server/tests/test_session_store.py`
- Test: `apps/server/tests/test_tools.py`

**Interfaces:**
- Produces: `SessionStore.get_recoverable_approval(run_id, tool_call_id) -> dict | None`.
- Produces: `SessionStore.get_tool_outcome(run_id, tool_call_id) -> dict | None` for exact result rehydration.
- Produces: `SessionStore.record_tool_execution_started(run_id, tool_call_id) -> bool`.
- Produces: `IOBridge.lookup_approval`, returning a durable terminal decision when replay reaches the same gate.
- Produces durable tool phases: `created`, `awaiting_approval`, `executing`, `success`, `error`, `timeout`, `cancelled`, `ambiguous`.

- [ ] **Step 1: Write failing store tests for immutable decisions and phase transitions**

```python
@pytest.mark.asyncio
async def test_replayed_approval_request_preserves_offline_decision(store: SessionStore):
    await store.record_tool_approval_requested(
        run_id="run-1", session_id="sess-1", tool_call_id="call-1",
        tool_name="send_email", action="write", scope="external",
    )
    assert await store.resolve_tool_approval(
        run_id="run-1", tool_call_id="call-1", status="approved"
    )

    await store.record_tool_approval_requested(
        run_id="run-1", session_id="sess-1", tool_call_id="call-1",
        tool_name="send_email", action="write", scope="external",
    )

    row = await store.get_tool_approval(run_id="run-1", tool_call_id="call-1")
    assert row["status"] == "approved"


@pytest.mark.asyncio
async def test_execution_boundary_is_single_claim(store: SessionStore):
    await store.record_tool_call_started(
        run_id="run-1", session_id="sess-1", tool_call_id="call-1",
        tool_name="send_email", action="write", scope="external",
        args_hash="abc",
    )
    assert await store.record_tool_execution_started("run-1", "call-1") is True
    assert await store.record_tool_execution_started("run-1", "call-1") is False
    row = (await store.list_tool_calls(run_id="run-1"))[0]
    assert row["status"] == "executing"


@pytest.mark.asyncio
async def test_tool_result_lookup_is_scoped_to_run_and_call(store: SessionStore):
    await store.record_tool_call_started(
        run_id="run-1", session_id="sess-1", tool_call_id="call-1",
        tool_name="read_file", action="read", scope="internal",
        args_hash="abc",
    )
    await store.record_tool_call_finished(
        run_id="run-1", tool_call_id="call-1", status="success",
        result_preview="result", result_json={
            "content": "result body", "preview": "result",
            "is_error": False, "data": None, "source_refs": [],
            "has_model_content": False,
        },
    )

    result = await store.get_tool_outcome("run-1", "call-1")
    assert result is not None
    assert result["content"] == "result body"
```

- [ ] **Step 2: Run the focused tests and verify they fail**

Run: `cd apps/server && uv run pytest tests/test_session_store.py -k 'replayed_approval or execution_boundary' -q`

Expected: failures because approval upsert resets terminal state and `record_tool_execution_started` does not exist.

- [ ] **Step 3: Preserve terminal approvals and add compare-and-set execution claims**

Change `record_tool_approval_requested` so its conflict clause refreshes display metadata but preserves `approved`, `rejected`, `expired`, and `cancelled`. Add:

```python
async def record_tool_execution_started(self, run_id: str, tool_call_id: str) -> bool:
    now = datetime.now(UTC).isoformat()
    cursor = await self.conn.execute(
        """
        UPDATE tool_calls
        SET status = 'executing', started_at = COALESCE(started_at, ?)
        WHERE run_id = ? AND tool_call_id = ?
          AND status IN ('created', 'awaiting_approval')
        """,
        (now, run_id, tool_call_id),
    )
    await self.conn.commit()
    return cursor.rowcount == 1
```

Add nullable `result_json` to `tool_calls`. Make `record_tool_call_started` an insert/metadata refresh that does not reset an existing execution phase. When an approval is requested, set the matching tool call from `created` to `awaiting_approval`.

Extend `record_tool_call_finished` to store a JSON-safe projection of `ToolResult`: `content`, `preview`, `is_error`, allowlisted `data`, normalized `source_refs`, and `has_model_content`. Add `get_tool_outcome` by selecting `result_json` with the exact `(run_id, tool_call_id)` primary key. Recovery must block when `has_model_content` is true because V1 does not reconstruct media blocks.

- [ ] **Step 4: Add the execution-boundary middleware**

Extend `IOBridge` with:

```python
lookup_approval: Callable[..., Awaitable[dict | None]] | None = None
record_execution_started: Callable[..., Awaitable[bool]] | None = None
```

In `ToolExecution.request_approval`, consult `lookup_approval` before creating a `Future`. Return a rejection for durable `rejected`; return `None` for durable `approved`; preserve expiry/cancellation as rejection results. Add middleware after `request_approval`:

```python
async def record_execution_boundary(call: ToolCall, next_call: ToolNext) -> ToolResult:
    claim = call.execution.ctx.io.record_execution_started
    if claim is not None:
        claimed = await claim(
            run_id=call.execution.ctx.run.run_id,
            tool_call_id=call.execution.tool_id,
        )
        if not claimed:
            raise RuntimeError(f"Tool execution phase is not claimable: {call.execution.tool_id}")
    return await next_call(call)
```

Set `DEFAULT_TOOL_MIDDLEWARE` to `(validate_arguments, request_approval, record_execution_boundary)`.

- [ ] **Step 5: Add focused middleware tests**

Test that an offline `approved` decision skips the `Future` and crosses the execution boundary once; an offline `rejected` decision returns a rejected `ToolResult`; a second execution claim fails before tool code runs.

Run: `cd apps/server && uv run pytest tests/test_tools.py tests/test_session_store.py -q`

Expected: all selected tests pass.

- [ ] **Step 6: Commit the persistence contract**

```bash
git add apps/server/arden/context/store.py apps/server/arden/services/session.py apps/server/arden/tools/core/context.py apps/server/arden/tools/core/middleware.py apps/server/arden/core/tool_executor.py apps/server/tests/test_session_store.py apps/server/tests/test_tools.py
git commit -m "feat: persist approval execution phases"
```

### Task 2: Checkpoint the Model Response Before Tool Dispatch

**Files:**
- Modify: `apps/server/arden/services/chat.py`
- Test: `apps/server/tests/test_streaming_events.py`

**Interfaces:**
- Consumes: existing `AgentHooks.on_response`, called after `normalize_assistant_message` appends the assistant message.
- Produces: a durable session transcript ending with the exact assistant `tool_calls` before any host tool begins.

- [ ] **Step 1: Write a failing crash-boundary test**

Create a fake agent response containing one approval-gated tool call. Block the tool before approval, reload the session from `SessionStore`, and assert the saved final message is the assistant message with the original `tool_call_id` and arguments.

```python
saved = await session_service.load(session_id)
assistant = saved.messages[-1]
assert assistant["role"] == "assistant"
assert assistant["tool_calls"][0]["id"] == "call-1"
assert assistant["tool_calls"][0]["function"]["name"] == "send_email"
```

- [ ] **Step 2: Run the test and verify the current checkpoint is too late**

Run: `cd apps/server && uv run pytest tests/test_streaming_events.py -k model_response_checkpoint -q`

Expected: failure because `on_step_finish` occurs only after tool dispatch completes.

- [ ] **Step 3: Persist inside the existing response hook**

Replace `_track_response` with `_checkpoint_model_response`. It must keep usage tracking, then call `save_progress(session_state, _persistable_messages(run))`, mark the bus checkpoint, and update `chat_runs.last_seq`. Do not emit a second assistant event.

- [ ] **Step 4: Verify model-response and ordinary stream behavior**

Run: `cd apps/server && uv run pytest tests/test_streaming_events.py -k 'model_response_checkpoint or tool or checkpoint' -q`

Expected: selected tests pass with one assistant message and unchanged SSE ordering.

- [ ] **Step 5: Commit the checkpoint boundary**

```bash
git add apps/server/arden/services/chat.py apps/server/tests/test_streaming_events.py
git commit -m "feat: checkpoint tool calls before dispatch"
```

### Task 3: Rehydrate a Run Suspended at an Approval Gate

**Files:**
- Modify: `apps/server/arden/agent/agent.py`
- Modify: `apps/server/arden/agent/llm/parsing.py`
- Modify: `apps/server/arden/services/chat.py`
- Modify: `apps/server/arden/server/state.py`
- Test: `apps/server/tests/test_agent_lib.py`
- Test: `apps/server/tests/test_streaming_events.py`

**Interfaces:**
- Produces: `pending_tool_step(messages: list[dict]) -> tuple[list[PendingToolCall], list[ToolCall]] | None`.
- Produces: `prepare_resumed_chat(deps: ChatDeps, run_id: str) -> ChatContext`.
- Produces: `resume_chat_run(...) -> dict[str, str]`, registering the original `run_id` in `RunRegistry`.

- [ ] **Step 1: Write parser tests for a trailing incomplete tool step**

Cover: all calls missing, some calls already have tool messages, all calls complete, malformed arguments, and a non-tool assistant tail. Only missing tool call IDs may be returned.

- [ ] **Step 2: Implement exact pending-step parsing**

Reconstruct `ToolCall` from the final assistant message and subtract tool IDs already represented by following `role=tool` messages. Do not scan earlier turns or infer from names.

- [ ] **Step 3: Write a failing agent replay test**

Start `Agent.stream` with a transcript ending in an incomplete assistant tool-call step. Assert the missing tool dispatch occurs before the fake LLM receives another request and that completed tool IDs are not dispatched twice.

- [ ] **Step 4: Resume the pending tool step at the start of `Agent.stream`**

Before the first `_call_llm`, dispatch the exact missing calls using the existing `ToolRunner`; append tool results through `dispatch_tools`; run `on_step_finish`; then continue the normal loop. If no pending step exists, retain the current path unchanged.

- [ ] **Step 5: Rehydrate completed results and the original run identity**

Add `RunRegistry.restore_run(run_id, session_id)`. `prepare_resumed_chat` must load saved messages, restore the existing run ID, retain the original client ID and persisted run metadata, and avoid appending a new user message.

Before starting `Agent.stream`, inspect every unresolved tool call against `tool_calls`:

- `success` or `error`: load the exact body with `get_tool_outcome` and append its `role=tool` message; if the body is absent or `has_model_content` is true, set `recovery_blocked` and stop.
- `created` or `awaiting_approval`: leave it missing so the agent replay path dispatches it.
- `executing`, `timeout`, `cancelled`, or `ambiguous`: set `recovery_blocked` and stop; V1 does not guess or retry.

This reconstruction must preserve the assistant call order when appending recovered tool results.

- [ ] **Step 6: Add an end-to-end service test**

Simulate: persisted assistant tool call → pending approval → process restart represented by a fresh `RunRegistry` → offline approval → resume. Assert the same run/tool IDs are used, the tool executes once, and the following model call sees one assistant tool call plus one tool result.

Run: `cd apps/server && uv run pytest tests/test_agent_lib.py tests/test_streaming_events.py -k 'pending_tool_step or resum' -q`

Expected: all recovery tests pass.

- [ ] **Step 7: Commit run rehydration**

```bash
git add apps/server/arden/agent/agent.py apps/server/arden/agent/llm/parsing.py apps/server/arden/services/chat.py apps/server/arden/server/state.py apps/server/tests/test_agent_lib.py apps/server/tests/test_streaming_events.py
git commit -m "feat: resume approval-gated chat runs"
```

### Task 4: Wire Offline Approval Resolution to Safe Resume

**Files:**
- Modify: `apps/server/arden/context/store.py`
- Modify: `apps/server/arden/server/routers/chat.py`
- Modify: `apps/server/arden/server/stores.py`
- Modify: `apps/server/arden/server/runtime/core.py`
- Test: `apps/server/tests/test_chat_inject.py`
- Test: `apps/server/tests/test_session_store.py`

**Interfaces:**
- Consumes: `resume_chat_run` from Task 3.
- Produces: offline `/tools/result` resolution schedules exactly one recovery task.
- Produces startup statuses: `waiting_for_approval` for provably gated runs and `recovery_blocked` for state-changing calls found in `executing`.

- [ ] **Step 1: Write startup classification tests**

Test these rows independently:

```text
pending approval + awaiting_approval tool -> waiting_for_approval
approved approval + awaiting_approval tool -> resumable
write tool + executing -> recovery_blocked / ambiguous_side_effect
read tool + executing -> interrupted (V1 does not generalize retry)
running run without a recoverable gate -> interrupted / server_restart
```

- [ ] **Step 2: Replace blanket startup interruption with classification**

Add `classify_interrupted_chat_runs()` in the store and call it from `initialize_stores`. The update must be transactional so run and tool states cannot disagree after startup.

- [ ] **Step 3: Write a failing router recovery test**

With no live `RunState`, post an approval decision. Assert the durable decision is committed first, one recovery task is scheduled second, and duplicate posts return `409` without scheduling another task.

- [ ] **Step 4: Wire the runtime callback**

Expose a runtime-owned `resume_chat_run(run_id)` callback. In `/tools/result`, after `resolve_durable_approval_if_pending()` succeeds for an absent run, schedule this callback and return:

```json
{"status": "resuming", "run_id": "run-1"}
```

If classification is `recovery_blocked`, return `409` with code `ambiguous_side_effect`; never call the executor.

- [ ] **Step 5: Run persistence and router tests**

Run: `cd apps/server && uv run pytest tests/test_chat_inject.py tests/test_session_store.py -q`

Expected: all tests pass, including existing live-Future approval behavior.

- [ ] **Step 6: Commit recovery orchestration**

```bash
git add apps/server/arden/context/store.py apps/server/arden/server/routers/chat.py apps/server/arden/server/stores.py apps/server/arden/server/runtime/core.py apps/server/tests/test_chat_inject.py apps/server/tests/test_session_store.py
git commit -m "feat: resume offline approval decisions"
```

### Task 5: Verify Crash Safety and Document the Contract

**Files:**
- Create: `apps/server/tests/test_chat_approval_recovery.py`
- Modify: `apps/server/arden/integrations/README.md`

**Interfaces:**
- Verifies the complete contract from Tasks 1-4.

- [ ] **Step 1: Add crash-point scenario tests**

Cover a restart at each boundary: before approval request persistence, while pending, after offline rejection, after offline approval but before execution claim, immediately after execution claim, and after successful completion. Assert no state-changing tool executes more than once.

- [ ] **Step 2: Add parallel-call recovery tests**

Use one read and one approval-gated write in the same model response. Persist a completed tool result for the read and a pending approval for the write. Recovery must reuse the completed result and execute only the approved write. If the completed result body is unavailable, recovery must block rather than repeat the call.

- [ ] **Step 3: Document lifecycle and exclusions**

Document this exact state flow:

```text
model response checkpointed
-> tool created
-> awaiting_approval
-> approved/rejected
-> executing
-> success/error
```

Document that `executing` after restart is ambiguous, is never auto-retried, and V1 excludes background/loop/child runs.

- [ ] **Step 4: Run the full server suite**

Run: `cd apps/server && uv run pytest tests -q`

Expected: all server tests pass.

- [ ] **Step 5: Commit verification and documentation**

```bash
git add apps/server/tests/test_chat_approval_recovery.py apps/server/arden/integrations/README.md
git commit -m "test: verify approval recovery crash safety"
```

## Follow-up Plans

After this plan ships and the recovery data is trustworthy, write separate implementation plans for:

1. Normalized provider/runtime error taxonomy with retryability and request IDs.
2. Stateful `AgentEvent -> AG-UI/SSE` projector ownership consolidation.
3. General typed external-input hooks for clarification, OAuth, and missing-secret waits.

These are intentionally excluded from this plan because each changes an independent subsystem and can ship on its own.
