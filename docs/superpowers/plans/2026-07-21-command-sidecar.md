# Command Sidecar Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Add `Cmd/Ctrl+Enter` to the existing command palette so a transient agent can reuse canonical ntrp tools, respect their policies, and navigate directly to typed in-app destinations.

**Architecture:** A new command-run endpoint provisions a hidden `agent` session and invokes the existing chat runner with an explicit command-eligible tool scope plus a `CommandOutcome` schema. Its ordinary SSE stream gains one `command_completed` terminal event. The desktop owns a small command-run store, a local SSE projection, a right-side peek, and a closed destination dispatcher; domain actions continue to execute through existing tools.

**Tech Stack:** FastAPI, Pydantic, existing ntrp Agent/ToolRegistry/SSE runtime, React 19, Zustand, TypeScript, Vitest-compatible desktop tests.

## Global Constraints

- `Enter` keeps executing the highlighted deterministic palette item.
- `Cmd/Ctrl+Enter` is the only agent submission shortcut.
- Navigation is immediate after a validated destination; actions keep canonical approvals and overrides.
- No query keywords, regex routing, GUI clicking, arbitrary URLs, or duplicate domain-action registry.
- Command sessions stay out of normal chat and agent lists but retain durable run/tool audit records.
- Preserve unrelated dirty-worktree changes; stage only files named by each task.

---

### Task 1: Command-eligible tool scope

**Files:**
- Modify: `apps/server/ntrp/integrations/base.py`
- Modify: `apps/server/ntrp/integrations/core.py`
- Modify: `apps/server/ntrp/integrations/gmail/__init__.py`
- Modify: `apps/server/ntrp/integrations/calendar/__init__.py`
- Modify: `apps/server/ntrp/integrations/google_drive/__init__.py`
- Modify: `apps/server/ntrp/integrations/slack/__init__.py`
- Modify: `apps/server/ntrp/tools/core/registry.py`
- Modify: `apps/server/ntrp/tools/executor.py`
- Test: `apps/server/tests/test_command_tool_scope.py`

**Interfaces:**
- Produces: `Integration.command_eligible: bool`, `ToolRegistry.register(..., command_eligible=False)`, `ToolRegistry.get_schemas(..., command_eligible=True)`, and `ToolExecutor.get_tools(..., command_eligible=True)`.
- Consumers: Task 2 calls `executor.get_tools(command_eligible=True)` and converts schemas to an exact hard scope.

- [x] **Step 1: Write failing registry tests**

```python
def test_command_filter_is_explicit_and_preserves_outer_scope():
    registry = ToolRegistry()
    registry.register("list_automations", read_tool(), command_eligible=True)
    registry.register("bash", execute_tool(), command_eligible=False)
    schemas = registry.get_schemas(command_eligible=True, scope=("list_automations", "bash"))
    assert schema_names(schemas) == {"list_automations"}

def test_command_metadata_reports_eligibility():
    registry = ToolRegistry()
    registry.register("list_automations", read_tool(), command_eligible=True)
    assert registry.get_metadata()[0]["command_eligible"] is True
```

- [x] **Step 2: Verify RED**

Run: `cd apps/server && uv run pytest -q tests/test_command_tool_scope.py`

Expected: failures because registry registration and schema filtering lack `command_eligible`.

- [x] **Step 3: Implement registry metadata and integration declarations**

```python
@dataclass(frozen=True)
class Integration:
    id: str
    label: str
    command_eligible: bool = False
    # existing fields remain

def register(self, name: str, tool: Tool, *, source="unknown", command_eligible=False):
    self._tools[name] = tool
    self._sources[name] = source
    self._command_eligible[name] = command_eligible
```

Mark the domain/integration groups used by command runs explicitly. Keep shell, filesystem mutation, research/workflow/background spawning, notification, and skill creation groups false. Keep `load_tools`, `tool_search`, and `request_connection` available through a small command-support integration or explicit per-tool registration.

- [x] **Step 4: Verify GREEN**

Run: `cd apps/server && uv run pytest -q tests/test_command_tool_scope.py tests/test_tool_scope.py tests/test_deferred_tools.py`

Expected: all pass.

- [x] **Step 5: Commit**

```bash
git add apps/server/ntrp/integrations apps/server/ntrp/tools/core/registry.py apps/server/ntrp/tools/executor.py apps/server/tests/test_command_tool_scope.py
git commit -m "feat(commands): register command-eligible tools"
```

### Task 2: Typed command run endpoint

**Files:**
- Create: `apps/server/ntrp/commands/models.py`
- Create: `apps/server/ntrp/commands/prompt.py`
- Create: `apps/server/ntrp/server/routers/commands.py`
- Modify: `apps/server/ntrp/server/app.py`
- Test: `apps/server/tests/test_command_runs.py`

**Interfaces:**
- Produces: `POST /command/runs`, `CommandRunRequest`, `CommandRunResponse`, `CommandOutcome`, `AppDestination`, and `command_tool_scope(executor)`.
- Consumes: Task 1 command-eligible schemas and existing `submit_chat_message(..., tool_scope, output_schema)`.

- [x] **Step 1: Write failing route/model tests**

```python
def test_completed_destination_rejects_arbitrary_url():
    with pytest.raises(ValidationError):
        CommandOutcome.model_validate({
            "status": "completed",
            "summary": "Opened it",
            "destination": {"kind": "url", "url": "https://example.com"},
        })

async def test_command_run_reuses_stable_agent_session(client, fake_runtime, monkeypatch):
    first = await client.post("/command/runs", json={"query": "open automations", "client_id": "cmd-1"})
    second = await client.post("/command/runs", json={"query": "open automations", "client_id": "cmd-1"})
    assert first.json() == second.json()
    assert fake_runtime.submitted_scope == ("list_automations", "update_automation", "tool_search")
    assert fake_runtime.submitted_output_schema is CommandOutcome
```

- [x] **Step 2: Verify RED**

Run: `cd apps/server && uv run pytest -q tests/test_command_runs.py`

Expected: import/route failures because command models and router do not exist.

- [x] **Step 3: Implement closed destination models and endpoint**

```python
class AutomationDestination(BaseModel):
    kind: Literal["automation"]
    task_id: str | None = None

class CommandChoice(BaseModel):
    label: str = Field(min_length=1, max_length=120)
    query: str = Field(min_length=1, max_length=500)

class CommandOutcome(BaseModel):
    status: Literal["completed", "needs_input", "unsupported", "failed"]
    summary: str = Field(max_length=500)
    destination: AppDestination | None = None
    prompt: str | None = Field(default=None, max_length=500)
    choices: list[CommandChoice] = Field(default_factory=list, max_length=5)
```

Use `session_id = f"command::{client_id}"`; create/save it directly as `session_type="agent"`, `agent_type="command_sidecar"` without publishing `SESSION_CREATED`. On retry, reuse the existing session and chat idempotency claim. Submit a trusted command context block, the exact eligible tool scope, and `CommandOutcome`.

- [x] **Step 4: Verify GREEN**

Run: `cd apps/server && uv run pytest -q tests/test_command_runs.py tests/test_chat_inject.py`

Expected: command tests and chat idempotency tests pass.

- [x] **Step 5: Commit**

```bash
git add apps/server/ntrp/commands apps/server/ntrp/server/routers/commands.py apps/server/ntrp/server/app.py apps/server/tests/test_command_runs.py
git commit -m "feat(commands): start scoped command runs"
```

### Task 3: Command completion SSE

**Files:**
- Modify: `apps/server/ntrp/events/sse.py`
- Modify: `apps/server/ntrp/services/chat.py`
- Modify: `apps/desktop/src/api/events.ts`
- Test: `apps/server/tests/test_command_completion_events.py`
- Test: `apps/server/tests/test_streaming_events.py`

**Interfaces:**
- Produces: `CommandCompletedEvent(run_id, outcome)` on command-session streams before `RUN_FINISHED`.
- Consumers: Task 4's desktop reducer applies only schema-shaped outcomes from this event.

- [x] **Step 1: Write failing completion-event tests**

```python
def test_command_completed_wire_shape():
    event = CommandCompletedEvent(run_id="run-1", outcome={"status": "completed", "summary": "Done"})
    payload = json.loads(event.to_sse()["data"])
    assert payload["type"] == "command_completed"
    assert payload["outcome"]["status"] == "completed"

async def test_command_session_emits_outcome_before_finished(command_chat_fixture):
    events = await command_chat_fixture.run(structured_output={"status": "completed", "summary": "Done"})
    assert event_names(events)[-2:] == ["command_completed", "RUN_FINISHED"]
```

- [x] **Step 2: Verify RED**

Run: `cd apps/server && uv run pytest -q tests/test_command_completion_events.py`

Expected: `CommandCompletedEvent` is missing.

- [x] **Step 3: Implement terminal event**

```python
@dataclass(frozen=True)
class CommandCompletedEvent(SSEEvent):
    type: EventType = field(default=EventType.COMMAND_COMPLETED, init=False)
    run_id: str = ""
    outcome: dict = field(default_factory=dict)
```

During successful non-backgrounded finalization, when `session_state.agent_type == "command_sidecar"`, emit a validated structured outcome or `{status: "failed", summary: "The command did not return a valid result."}` before `RunFinishedEvent`.

- [x] **Step 4: Verify GREEN**

Run: `cd apps/server && uv run pytest -q tests/test_command_completion_events.py tests/test_streaming_events.py tests/test_chat_inject.py`

Expected: all pass.

- [x] **Step 5: Commit**

```bash
git add apps/server/ntrp/events/sse.py apps/server/ntrp/services/chat.py apps/server/tests/test_command_completion_events.py apps/server/tests/test_streaming_events.py apps/desktop/src/api/events.ts
git commit -m "feat(commands): stream validated command outcomes"
```

### Task 4: Desktop command domain and API

**Files:**
- Create: `apps/desktop/src/api/commands.ts`
- Create: `apps/desktop/src/features/command-sidecar/domain.ts`
- Create: `apps/desktop/src/features/command-sidecar/useCommandEvents.ts`
- Modify: `apps/desktop/src/stores/types.ts`
- Modify: `apps/desktop/src/stores/index.ts`
- Test: `apps/desktop/tests/commandSidecarDomain.test.ts`

**Interfaces:**
- Produces: `startCommandRunApi`, `CommandSidecarState`, event reducer, `openCommandSidecar(query)`, `closeCommandSidecar()`, and `stopCommandSidecar()`.
- Consumers: Tasks 5-7 use the Zustand state and event hook.

- [x] **Step 1: Write failing reducer tests**

```typescript
test("projects tool calls and a validated completion", () => {
  let state = createCommandSidecarState();
  state = reduceCommandEvent(state, { type: "TOOL_CALL_START", tool_call_id: "t1", tool_call_name: "list_automations" });
  state = reduceCommandEvent(state, { type: "command_completed", run_id: "r1", outcome: { status: "completed", summary: "Opened", destination: { kind: "automation", task_id: "a1" } } });
  expect(state.activities[0].name).toBe("list_automations");
  expect(state.outcome?.destination).toEqual({ kind: "automation", task_id: "a1" });
});

test("ignores malformed destinations", () => {
  const next = reduceCommandEvent(createCommandSidecarState(), malformedCommandEvent);
  expect(next.outcome).toBeNull();
  expect(next.error).toMatch(/invalid/i);
});
```

- [x] **Step 2: Verify RED**

Run: `cd apps/desktop && bun test tests/commandSidecarDomain.test.ts`

Expected: missing module failures.

- [x] **Step 3: Implement API, state, reducer, and local SSE subscription**

```typescript
export type AppDestination =
  | { kind: "home" }
  | { kind: "session"; session_id: string }
  | { kind: "settings"; tab?: string | null }
  | { kind: "automation"; task_id?: string | null }
  | { kind: "memory"; path?: string | null }
  | { kind: "area"; area_key: string };

export interface CommandSidecarState {
  open: boolean;
  query: string;
  runId: string | null;
  sessionId: string | null;
  status: "idle" | "starting" | "running" | "completed" | "failed" | "cancelled";
  activities: CommandActivity[];
  approval: CommandApproval | null;
  connection: PendingConnection | null;
  outcome: CommandOutcome | null;
  error: string | null;
}
```

The hook subscribes to the command session without changing the current chat's global event cursor. It supports the Electron bridge and fetch fallback, tracks its own sequence, reconnects from the last sequence, and dispatches local events into the command reducer.

- [x] **Step 4: Verify GREEN**

Run: `cd apps/desktop && bun test tests/commandSidecarDomain.test.ts && bun run typecheck`

Expected: tests and typecheck pass.

- [x] **Step 5: Commit**

```bash
git add apps/desktop/src/api/commands.ts apps/desktop/src/features/command-sidecar apps/desktop/src/stores/types.ts apps/desktop/src/stores/index.ts apps/desktop/tests/commandSidecarDomain.test.ts
git commit -m "feat(commands): add desktop command run domain"
```

### Task 5: Palette `Cmd/Ctrl+Enter`

**Files:**
- Modify: `apps/desktop/src/features/command-palette/components/PaletteBody.tsx`
- Modify: `apps/desktop/src/features/command-palette/components/CommandPalette.tsx`
- Test: `apps/desktop/tests/commandPaletteAgentSubmit.test.tsx`

**Interfaces:**
- Consumes: Task 4 `openCommandSidecar(query)`.
- Produces: keyboard submission while preserving deterministic `Enter` activation.

- [x] **Step 1: Write failing keyboard tests**

```typescript
test("Cmd+Enter submits the raw query without activating the highlighted row", async () => {
  render(<CommandPalette />);
  await user.type(screen.getByRole("combobox"), "go to email automation");
  fireEvent.keyDown(screen.getByRole("combobox"), { key: "Enter", metaKey: true });
  expect(openCommandSidecar).toHaveBeenCalledWith("go to email automation");
  expect(goToNewSessionHome).not.toHaveBeenCalled();
});

test("plain Enter still activates the highlighted row", async () => {
  // existing deterministic assertion
});
```

- [x] **Step 2: Verify RED**

Run: `cd apps/desktop && bun test tests/commandPaletteAgentSubmit.test.tsx`

Expected: `Cmd+Enter` follows the existing plain-Enter path or does nothing.

- [x] **Step 3: Implement shortcut**

Handle `Cmd/Ctrl+Enter` before list navigation, require `query.trim()`, invoke the store action, and close/reset the palette. Add a compact footer hint: `⌘↵ ask agent` only while the query is non-empty.

- [x] **Step 4: Verify GREEN**

Run: `cd apps/desktop && bun test tests/commandPaletteAgentSubmit.test.tsx`

Expected: both agent and deterministic paths pass.

- [x] **Step 5: Commit**

```bash
git add apps/desktop/src/features/command-palette apps/desktop/tests/commandPaletteAgentSubmit.test.tsx
git commit -m "feat(commands): submit palette queries with command enter"
```

### Task 6: Typed in-app navigation

**Files:**
- Create: `apps/desktop/src/features/command-sidecar/navigation.ts`
- Modify: `apps/desktop/src/stores/types.ts`
- Modify: `apps/desktop/src/stores/index.ts`
- Modify: `apps/desktop/src/features/automations/components/AutomationsModal.tsx`
- Test: `apps/desktop/tests/commandNavigation.test.ts`
- Test: `apps/desktop/tests/automationsDeepOpen.test.tsx`

**Interfaces:**
- Produces: `applyCommandDestination(destination, state)`, `automationTargetId`, and `openAutomations(origin?, taskId?)`.
- Consumers: Task 7 applies `CommandOutcome.destination` exactly once after validation.

- [x] **Step 1: Write failing destination tests**

```typescript
test("opens a specific automation", () => {
  applyCommandDestination({ kind: "automation", task_id: "email-digest" });
  expect(getState().automationsOpen).toBe(true);
  expect(getState().automationTargetId).toBe("email-digest");
});

test("does not open a missing automation", () => {
  setState({ automations: [automation("other")] });
  expect(applyCommandDestination({ kind: "automation", task_id: "missing" })).toEqual({ ok: false });
  expect(getState().automationsOpen).toBe(false);
});
```

- [x] **Step 2: Verify RED**

Run: `cd apps/desktop && bun test tests/commandNavigation.test.ts tests/automationsDeepOpen.test.tsx`

Expected: missing dispatcher and deep-selection state.

- [x] **Step 3: Implement closed dispatcher and external automation selection**

Map only union members to existing actions. Validate dynamic resources against current store collections before applying. Move automation selection into store-owned `automationTargetId`, clear it after the modal consumes/reflects it, and preserve the existing first-item fallback when no target is supplied.

- [x] **Step 4: Verify GREEN**

Run: `cd apps/desktop && bun test tests/commandNavigation.test.ts tests/automationsDeepOpen.test.tsx`

Expected: all destinations and missing-resource guards pass.

- [x] **Step 5: Commit**

```bash
git add apps/desktop/src/features/command-sidecar/navigation.ts apps/desktop/src/stores apps/desktop/src/features/automations/components/AutomationsModal.tsx apps/desktop/tests/commandNavigation.test.ts apps/desktop/tests/automationsDeepOpen.test.tsx
git commit -m "feat(commands): navigate to typed app destinations"
```

### Task 7: Transient command peek

**Files:**
- Create: `apps/desktop/src/features/command-sidecar/CommandPeek.tsx`
- Modify: `apps/desktop/src/app/App.tsx`
- Test: `apps/desktop/tests/commandPeek.test.tsx`

**Interfaces:**
- Consumes: Tasks 4 and 6 state, event hook, approval/connection APIs, cancel API, and destination dispatcher.
- Produces: compact right-side command surface with activity, approvals, clarification choices, result receipt, Close, and Stop.

- [x] **Step 1: Write failing interaction tests**

```typescript
test("renders a run, approves a tool, and applies a completed destination once", async () => {
  render(<CommandPeek />);
  emit(commandApprovalEvent);
  await user.click(screen.getByRole("button", { name: "Approve" }));
  expect(submitToolResult).toHaveBeenCalledWith(expect.anything(), expect.objectContaining({ approved: true }));
  emit(commandCompletedAutomationEvent);
  expect(openAutomations).toHaveBeenCalledTimes(1);
});

test("Escape closes without cancelling and Stop cancels", async () => {
  // assert close action vs cancelRun API
});
```

- [x] **Step 2: Verify RED**

Run: `cd apps/desktop && bun test tests/commandPeek.test.tsx`

Expected: missing component.

- [x] **Step 3: Implement peek**

Render an always-mounted animated aside above the ordinary inspector. Show the query, status, bounded activity rows, the pending approval preview/diff with Approve/Deny, connection recovery controls, clarification choice buttons that start a new command, terminal summary, explicit Stop, and Close. Apply a completed destination once per run id; invalid/missing destinations remain visible as failures.

- [x] **Step 4: Verify GREEN**

Run: `cd apps/desktop && bun test tests/commandPeek.test.tsx && bun run typecheck`

Expected: interaction tests and typecheck pass.

- [x] **Step 5: Commit**

```bash
git add apps/desktop/src/features/command-sidecar/CommandPeek.tsx apps/desktop/src/app/App.tsx apps/desktop/tests/commandPeek.test.tsx
git commit -m "feat(commands): add transient command side peek"
```

### Task 8: End-to-end verification

**Files:**
- Modify only if verification reveals a failing behavior in files already owned above.
- Test: existing server and desktop suites.

**Interfaces:**
- Consumes: complete command flow.
- Produces: evidence for the design's explicit verification contract.

- [x] **Step 1: Run focused server tests**

Run: `cd apps/server && uv run pytest -q tests/test_command_tool_scope.py tests/test_command_runs.py tests/test_command_completion_events.py tests/test_deferred_tools.py tests/test_approval_policy.py tests/test_tool_scope.py tests/test_streaming_events.py`

Expected: all pass without warnings introduced by command code.

- [x] **Step 2: Run focused desktop tests**

Run: `cd apps/desktop && bun test tests/commandSidecarDomain.test.ts tests/commandPaletteAgentSubmit.test.tsx tests/commandNavigation.test.ts tests/automationsDeepOpen.test.tsx tests/commandPeek.test.tsx`

Expected: all pass.

- [x] **Step 3: Run static verification**

Run: `cd apps/desktop && bun run typecheck && bun run build`

Expected: both exit 0.

- [x] **Step 4: Run repository checks**

Run: `git diff --check && git status --short`

Expected: no whitespace errors; only known user changes plus command-sidecar work.

- [x] **Step 5: Verify the real interaction**

Start the app against a test automation named `email automation`. Confirm:

1. `Cmd+K`, query, plain `Enter` still uses deterministic results.
2. `Cmd+Enter` opens the command peek.
3. `go to email automation` opens its detail immediately after resolution.
4. `pause email automation` invokes `update_automation` and shows its canonical approval before mutation.
5. Ambiguous, unsupported, denied, cancelled, and connection-required commands do not navigate or mutate silently.

- [x] **Step 6: Final focused commit if verification required fixes**

```bash
git add <only command-sidecar files changed by verification>
git commit -m "fix(commands): close command sidecar verification gaps"
```
