# Background Agent Runs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace ephemeral background-agent state with durable, replayable server-owned runs/events, then make desktop render that truth.

**Architecture:** Add durable `background_agent_runs` and `background_agent_events` to `SessionStore`. Keep `BackgroundTaskRegistry` as a live worker-handle cache only; it records lifecycle transitions/events through the store. Desktop consumes snapshots/events and never infers completion from missing poll rows.

**Tech Stack:** Python FastAPI/aiosqlite backend, existing SSE events, Bun/Vitest desktop tests.

---

## File Structure

- Modify `apps/server/arden/context/store.py`
  - schema
  - background run/event CRUD
  - startup interruption
- Modify `apps/server/arden/events/sse.py`
  - extend `BackgroundTaskEvent` with `session_id`, `run_id`, `result_ref`, `terminal`
- Modify `apps/server/arden/tools/core/context.py`
  - registry depends on optional lifecycle sink
  - no durable truth in registry maps
- Modify `apps/server/arden/core/spawner.py`
  - create run before `asyncio.create_task`
  - emit status through lifecycle sink
- Modify `apps/server/arden/tools/background.py`
  - list/cancel/read use durable store when available
- Modify `apps/server/arden/server/routers/chat.py`
  - snapshot endpoint returns durable background runs
  - cancel endpoint writes `cancel_requested` and emits final state when worker cancels
- Modify `apps/server/arden/server/schemas.py`
  - response models for background runs
- Modify `apps/desktop/src/api.ts`
  - background event and snapshot types
- Modify `apps/desktop/src/store.ts`
  - stop fake-completing missing tasks
  - merge durable snapshots/events
- Modify `apps/desktop/src/components/AgentRightSidebar.tsx`
  - render server status
  - optimistic state is `cancelling`, not terminal
- Tests:
  - `apps/server/tests/test_session_store.py`
  - `apps/server/tests/test_background_agent_runs.py`
  - `apps/desktop/tests/streamEvents.test.ts`

---

### Task 1: Durable Store

**Files:**
- Modify: `apps/server/arden/context/store.py`
- Test: `apps/server/tests/test_session_store.py`

- [ ] **Step 1: Write failing store tests**

Add tests:

```python
async def test_background_agent_run_lifecycle(store: SessionStore):
    await store.record_background_agent_started(
        task_id="bg-1",
        session_id="sess-1",
        parent_run_id="run-1",
        command="research task",
    )
    await store.record_background_agent_event(
        task_id="bg-1",
        session_id="sess-1",
        status="activity",
        detail="read files",
    )
    await store.record_background_agent_finished(
        task_id="bg-1",
        session_id="sess-1",
        status="completed",
        result_ref="bg_results/bg-1.txt",
        detail="done",
    )

    runs = await store.list_background_agent_runs("sess-1")
    assert runs[0]["task_id"] == "bg-1"
    assert runs[0]["status"] == "completed"
    assert runs[0]["result_ref"] == "bg_results/bg-1.txt"

    events = await store.list_background_agent_events("sess-1", after_seq=0)
    assert [e["status"] for e in events] == ["started", "activity", "completed"]
    assert events[-1]["terminal"] is True


async def test_marks_running_background_agents_interrupted_on_startup(store: SessionStore):
    await store.record_background_agent_started(
        task_id="bg-1",
        session_id="sess-1",
        parent_run_id="run-1",
        command="research task",
    )

    changed = await store.mark_interrupted_background_agent_runs()
    runs = await store.list_background_agent_runs("sess-1")

    assert changed == 1
    assert runs[0]["status"] == "interrupted"
```

- [ ] **Step 2: Run failing tests**

Run:

```bash
uv run pytest apps/server/tests/test_session_store.py -q
```

Expected: fails because background-agent store methods do not exist.

- [ ] **Step 3: Add schema**

Add to `SCHEMA`:

```sql
CREATE TABLE IF NOT EXISTS background_agent_runs (
    task_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    parent_run_id TEXT,
    status TEXT NOT NULL,
    command TEXT NOT NULL,
    detail TEXT,
    result_ref TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    updated_at TEXT NOT NULL,
    ended_at TEXT,
    cancel_requested_at TEXT,
    notified_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_background_agent_runs_session_status
    ON background_agent_runs(session_id, status);

CREATE TABLE IF NOT EXISTS background_agent_events (
    session_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    task_id TEXT NOT NULL,
    status TEXT NOT NULL,
    detail TEXT,
    result_ref TEXT,
    terminal INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    PRIMARY KEY (session_id, seq)
);

CREATE INDEX IF NOT EXISTS idx_background_agent_events_task
    ON background_agent_events(task_id);
```

- [ ] **Step 4: Add store methods**

Add methods:

```python
def _background_agent_payload(self, row: aiosqlite.Row) -> dict:
    return {
        "task_id": row["task_id"],
        "session_id": row["session_id"],
        "parent_run_id": row["parent_run_id"],
        "status": row["status"],
        "command": row["command"],
        "detail": row["detail"],
        "result_ref": row["result_ref"],
        "created_at": row["created_at"],
        "started_at": row["started_at"],
        "updated_at": row["updated_at"],
        "ended_at": row["ended_at"],
        "cancel_requested_at": row["cancel_requested_at"],
        "notified_at": row["notified_at"],
    }

async def record_background_agent_started(
    self, *, task_id: str, session_id: str, parent_run_id: str | None, command: str
) -> None:
    now = datetime.now(UTC).isoformat()
    await self.conn.execute(
        """
        INSERT INTO background_agent_runs (
            task_id, session_id, parent_run_id, status, command,
            created_at, started_at, updated_at
        )
        VALUES (?, ?, ?, 'running', ?, ?, ?, ?)
        ON CONFLICT(task_id) DO UPDATE SET
            session_id = excluded.session_id,
            parent_run_id = excluded.parent_run_id,
            status = 'running',
            command = excluded.command,
            updated_at = excluded.updated_at,
            ended_at = NULL,
            cancel_requested_at = NULL
        """,
        (task_id, session_id, parent_run_id, command, now, now, now),
    )
    await self.record_background_agent_event(
        task_id=task_id,
        session_id=session_id,
        status="started",
        detail=None,
    )

async def record_background_agent_event(
    self,
    *,
    task_id: str,
    session_id: str,
    status: str,
    detail: str | None = None,
    result_ref: str | None = None,
) -> int:
    terminal = status in {"completed", "failed", "cancelled", "interrupted"}
    now = datetime.now(UTC).isoformat()
    rows = await self.conn.execute_fetchall(
        "SELECT COALESCE(MAX(seq), 0) + 1 AS next_seq FROM background_agent_events WHERE session_id = ?",
        (session_id,),
    )
    seq = int(rows[0]["next_seq"])
    await self.conn.execute(
        """
        INSERT INTO background_agent_events (
            session_id, seq, task_id, status, detail, result_ref, terminal, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (session_id, seq, task_id, status, detail, result_ref, int(terminal), now),
    )
    await self.conn.commit()
    return seq
```

Also add `record_background_agent_finished`, `request_background_agent_cancel`, `list_background_agent_runs`, `list_background_agent_events`, and `mark_interrupted_background_agent_runs`.

- [ ] **Step 5: Run tests**

Run:

```bash
uv run pytest apps/server/tests/test_session_store.py -q
```

Expected: pass.

---

### Task 2: Server Lifecycle Contract

**Files:**
- Modify: `apps/server/arden/events/sse.py`
- Modify: `apps/server/arden/tools/core/context.py`
- Modify: `apps/server/arden/core/spawner.py`
- Test: `apps/server/tests/test_background_agent_runs.py`

- [ ] **Step 1: Write failing lifecycle tests**

Create `apps/server/tests/test_background_agent_runs.py`:

```python
import asyncio

from arden.tools.core.context import BackgroundTaskRegistry


async def test_background_registry_records_started_activity_and_completed():
    calls = []

    async def record(**kwargs):
        calls.append(kwargs)

    registry = BackgroundTaskRegistry(session_id="sess-1", record_event=record)
    task = asyncio.create_task(asyncio.sleep(0))
    registry.register("bg-1", task, command="research", parent_run_id="run-1")
    await registry.record_activity("bg-1", "read files")
    await registry.deliver_result(
        task_id="bg-1",
        result="done",
        label="research",
        status="completed",
        emit=None,
    )

    assert [c["status"] for c in calls] == ["started", "activity", "completed"]
    assert calls[0]["session_id"] == "sess-1"
    assert calls[-1]["terminal"] is True
```

- [ ] **Step 2: Run failing test**

Run:

```bash
uv run pytest apps/server/tests/test_background_agent_runs.py -q
```

Expected: fails because registry has no lifecycle sink.

- [ ] **Step 3: Extend event shape**

Change `BackgroundTaskEvent`:

```python
@dataclass(frozen=True)
class BackgroundTaskEvent(SSEEvent):
    type: EventType = field(default=EventType.BACKGROUND_TASK, init=False)
    task_id: str = ""
    session_id: str = ""
    run_id: str | None = None
    command: str = ""
    status: str = ""
    detail: str | None = None
    result_ref: str | None = None
    terminal: bool = False
```

- [ ] **Step 4: Add registry lifecycle sink**

Update `BackgroundTaskRegistry` constructor fields:

```python
record_event: Callable[..., Awaitable[None]] | None = None
```

Add:

```python
async def _record(self, *, task_id: str, status: str, detail: str | None = None, result_ref: str | None = None) -> None:
    terminal = status in {"completed", "failed", "cancelled", "interrupted"}
    if self.record_event:
        await self.record_event(
            task_id=task_id,
            session_id=self.session_id,
            command=self._commands.get(task_id, ""),
            status=status,
            detail=detail,
            result_ref=result_ref,
            terminal=terminal,
        )

async def record_activity(self, task_id: str, detail: str) -> None:
    await self._record(task_id=task_id, status="activity", detail=detail)
```

Make `register` emit `started` by scheduling an async record from `spawner.py`, not from sync `register`.

- [ ] **Step 5: Wire spawner**

In `create_spawn_fn`, after `task_id` is generated and before `create_task`, call:

```python
await registry.record_started(task_id=task_id, command=label, parent_run_id=calling_ctx.run.run_id)
```

In `_to_bg_events`, call `await registry.record_activity(...)` before emitting activity.

- [ ] **Step 6: Run tests**

Run:

```bash
uv run pytest apps/server/tests/test_background_agent_runs.py apps/server/tests/test_spawn_salvage.py -q
```

Expected: pass.

---

### Task 3: Snapshot And Cancel APIs

**Files:**
- Modify: `apps/server/arden/server/schemas.py`
- Modify: `apps/server/arden/server/routers/chat.py`
- Test: `apps/server/tests/test_chat_background_tasks_api.py`

- [ ] **Step 1: Write failing API tests**

Create tests:

```python
from fastapi.testclient import TestClient

from arden.server.app import app
from arden.server.runtime import get_runtime


class _Store:
    async def list_background_agent_runs(self, session_id, include_terminal=True):
        return [{
            "task_id": "bg-1",
            "session_id": session_id,
            "parent_run_id": "run-1",
            "status": "running",
            "command": "research",
            "detail": "read files",
            "result_ref": None,
            "created_at": "2026-05-15T00:00:00+00:00",
            "started_at": "2026-05-15T00:00:00+00:00",
            "updated_at": "2026-05-15T00:00:01+00:00",
            "ended_at": None,
            "cancel_requested_at": None,
            "notified_at": None,
        }]


class _SessionService:
    store = _Store()


class _Runtime:
    session_service = _SessionService()


def test_background_tasks_endpoint_returns_durable_snapshot():
    app.dependency_overrides[get_runtime] = lambda: _Runtime()
    try:
        response = TestClient(app).get("/chat/background-tasks?session_id=sess-1")
    finally:
        app.dependency_overrides.pop(get_runtime, None)

    assert response.status_code == 200
    assert response.json()["tasks"][0]["status"] == "running"
    assert response.json()["tasks"][0]["detail"] == "read files"
```

- [ ] **Step 2: Run failing API tests**

Run:

```bash
uv run pytest apps/server/tests/test_chat_background_tasks_api.py -q
```

Expected: fails because endpoint uses in-memory registry shape.

- [ ] **Step 3: Add schemas**

Add:

```python
class BackgroundAgentRunResponse(BaseModel):
    task_id: str
    session_id: str
    parent_run_id: str | None = None
    status: Literal["running", "activity", "completed", "failed", "cancelled", "interrupted", "cancel_requested"]
    command: str
    detail: str | None = None
    result_ref: str | None = None
    created_at: str
    started_at: str | None = None
    updated_at: str
    ended_at: str | None = None
    cancel_requested_at: str | None = None
    notified_at: str | None = None


class BackgroundAgentRunsResponse(BaseModel):
    tasks: list[BackgroundAgentRunResponse] = Field(default_factory=list)
```

- [ ] **Step 4: Update endpoint**

`GET /chat/background-tasks` should prefer:

```python
runs = await runtime.session_service.store.list_background_agent_runs(session_id)
return {"tasks": runs}
```

Fallback to registry only when no session service exists.

- [ ] **Step 5: Run API tests**

Run:

```bash
uv run pytest apps/server/tests/test_chat_background_tasks_api.py apps/server/tests/test_chat_runs_status_api.py -q
```

Expected: pass.

---

### Task 4: Desktop Stops Guessing

**Files:**
- Modify: `apps/desktop/src/api.ts`
- Modify: `apps/desktop/src/store.ts`
- Modify: `apps/desktop/src/hooks/useEvents.ts`
- Test: `apps/desktop/tests/streamEvents.test.ts`

- [ ] **Step 1: Write failing desktop tests**

Add to `streamEvents.test.ts`:

```ts
test("background snapshot does not complete missing running tasks", () => {
  const s = useStore.getState();
  s.upsertBackgroundAgent({
    taskId: "bg-1",
    sessionId: "session-1",
    command: "research",
    status: "running",
    updatedAt: 1,
  });

  s.setBackgroundAgentsForSession("session-1", []);

  expect(useStore.getState().backgroundAgents["session-1:bg-1"].status).toBe("running");
});
```

- [ ] **Step 2: Run failing desktop test**

Run:

```bash
bun test apps/desktop/tests/streamEvents.test.ts
```

Expected: fails if current code marks missing tasks completed.

- [ ] **Step 3: Update desktop event type**

In `api.ts`, make `background_task` include:

```ts
| {
    type: "background_task";
    task_id: string;
    session_id?: string;
    run_id?: string | null;
    command: string;
    status: "started" | "activity" | "completed" | "failed" | "cancelled" | "interrupted" | "cancel_requested" | string;
    detail?: string | null;
    result_ref?: string | null;
    terminal?: boolean;
  }
```

- [ ] **Step 4: Remove fake completion**

In `setBackgroundAgentsForSession`, merge snapshot records, but do not mutate existing missing records to `completed`.

- [ ] **Step 5: Run tests**

Run:

```bash
bun test apps/desktop/tests/streamEvents.test.ts
bun run --cwd apps/desktop typecheck
```

Expected: pass.

---

### Task 5: End-To-End Verification

**Files:**
- No new files unless failures require scoped fixes.

- [ ] **Step 1: Run focused backend tests**

Run:

```bash
uv run pytest apps/server/tests/test_session_store.py apps/server/tests/test_background_agent_runs.py apps/server/tests/test_chat_background_tasks_api.py apps/server/tests/test_spawn_salvage.py -q
```

Expected: pass.

- [ ] **Step 2: Run focused desktop tests**

Run:

```bash
bun test apps/desktop/tests/streamEvents.test.ts apps/desktop/tests/sessionCache.test.ts apps/desktop/tests/streamOrdering.test.ts apps/desktop/tests/eventContract.test.ts
```

Expected: pass.

- [ ] **Step 3: Typecheck**

Run:

```bash
bun run --cwd apps/desktop typecheck
```

Expected: pass.

- [ ] **Step 4: Diff hygiene**

Run:

```bash
git diff --check
git status --short
```

Expected: only intended files changed; no whitespace errors.

---

## Self-Review

- Spec coverage: durable runs/events, cancellation, replay snapshot, desktop no-guessing, terminal states covered.
- Scope control: no UI redesign in backend tasks; right sidebar visual polish is out of scope.
- Risk: exact `BackgroundTaskRegistry` async API may need minor adjustment during implementation because current `register` is sync.
