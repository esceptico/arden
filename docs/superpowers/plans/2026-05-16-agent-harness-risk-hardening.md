# Agent Harness Policy Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace scattered tool-control fields with one explicit `ToolPolicy`, then use it for approvals, audit, result limits, MCP classification, and run budgets.

**Architecture:** Keep Arden's existing manual model-tool-observation loop, tool registry, middleware chain, `SessionStore`, SSE replay, deferred tools, and provider adapters. Introduce one consolidated policy object at the tool boundary, then thread it through metadata, permission decisions, execution, persistence, and tests. Broader compaction, telemetry, eval, planning-mode, and skill-governance work stays as a roadmap after the first runtime-safety slice.

**Tech Stack:** Python 3.13, Pydantic, FastAPI, aiosqlite, pytest, existing Arden agent/tool abstractions.

---

## File Structure

- `apps/server/arden/tools/core/types.py`: define `ToolAction`, `ToolScope`, `ToolPolicy`, and `PermissionDecision`.
- `apps/server/arden/tools/core/base.py`: make `Tool.policy` required and expose policy metadata.
- `apps/server/arden/tools/core/function.py`: require `policy` in `tool(...)` and `_FunctionTool`.
- `apps/server/arden/tools/core/registry.py`: validate loaded tools have policy and use `policy.permissions`.
- `apps/server/arden/tools/core/middleware.py`: use `policy.requires_approval` for approval routing.
- `apps/server/arden/tools/core/context.py`: add bounded approval callbacks and structured rejection/expiry results.
- `apps/server/arden/core/tool_executor.py`: apply timeout, audit, `max_result_chars`, and `offload` from policy.
- `apps/server/arden/tools/deferred.py`: replace `requires` reads with `policy.permissions`.
- `apps/server/arden/mcp/models.py`: parse MCP per-tool policies.
- `apps/server/arden/mcp/manager.py`: pass MCP policy into tool construction.
- `apps/server/arden/mcp/tool.py`: use configured or conservative default policy.
- `apps/server/arden/context/store.py`: add durable `tool_calls` and `tool_approvals`.
- `apps/server/arden/agent/tools/dispatch.py`: append cause-specific missing/cancelled results.
- `apps/server/arden/agent/tools/runner.py`: preserve timeout/cancellation causes.
- `apps/server/arden/agent/agent.py`: enforce tool-call, wall-time, and cost budgets.
- `apps/server/arden/agent/types/stop.py`: add explicit budget stop reasons.
- `apps/server/arden/config.py`: expose budget and approval timeout config.
- `apps/server/arden/core/factory.py`: wire budgets into `Agent` and `RunContext`.
- `apps/server/arden/services/chat.py`: persist stop reasons and approval/audit services.
- `apps/server/arden/server/routers/chat.py`: resolve durable approval rows in `/tools/result`.
- Tests: `apps/server/tests/test_tools.py`, `test_mcp_config.py`, `test_mcp_tool.py`, `test_session_store.py`, `test_tool_runner.py`, `test_agent_lib.py`, and `test_chat_inject.py` for `/tools/result` approval resolution.

## Phase B Tasks

### Task 1: Define Consolidated Tool Policy Types

**Files:**
- Modify: `apps/server/arden/tools/core/types.py`
- Test: `apps/server/tests/test_tools.py`

- [ ] Add failing tests that import the new policy types and assert the enum values.

```python
from arden.tools.core.types import ToolAction, ToolPolicy, ToolScope


def test_tool_policy_model_defaults():
    policy = ToolPolicy(action=ToolAction.READ, scope=ToolScope.INTERNAL)

    assert policy.action == ToolAction.READ
    assert policy.scope == ToolScope.INTERNAL
    assert policy.requires_approval is False
    assert policy.permissions == frozenset()
    assert policy.timeout_seconds is None
    assert policy.audit is True
    assert policy.max_result_chars is None
    assert policy.offload is True
```

- [ ] Run the focused test and verify it fails because the types do not exist.

Run: `cd apps/server && uv run pytest tests/test_tools.py::test_tool_policy_model_defaults -q`

Expected: import failure for `ToolAction`, `ToolScope`, or `ToolPolicy`.

- [ ] Implement the policy types in `apps/server/arden/tools/core/types.py`.

```python
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class ToolAction(str, Enum):
    READ = "read"
    DRAFT = "draft"
    WRITE = "write"
    EXECUTE = "execute"


class ToolScope(str, Enum):
    INTERNAL = "internal"
    EXTERNAL = "external"


class ToolPolicy(BaseModel):
    model_config = ConfigDict(frozen=True)

    action: ToolAction
    scope: ToolScope
    requires_approval: bool = False
    permissions: frozenset[str] = Field(default_factory=frozenset)
    timeout_seconds: int | None = None
    audit: bool = True
    max_result_chars: int | None = None
    offload: bool = True


class PermissionDecision(str, Enum):
    EXECUTE = "execute"
    REQUEST_APPROVAL = "request_approval"
    DENY = "deny"
```

- [ ] Run the focused test and verify it passes.

Run: `cd apps/server && uv run pytest tests/test_tools.py::test_tool_policy_model_defaults -q`

Expected: pass.

### Task 2: Require Policy On Core Tool Objects

**Files:**
- Modify: `apps/server/arden/tools/core/base.py`
- Modify: `apps/server/arden/tools/core/function.py`
- Modify: `apps/server/arden/tools/core/__init__.py`
- Test: `apps/server/tests/test_tools.py`

- [ ] Add a failing test that builds a function tool with policy and checks metadata.

```python
from arden.tools.core.function import tool
from arden.tools.core.types import ToolAction, ToolPolicy, ToolScope


def test_function_tool_metadata_exposes_policy():
    async def handler(execution, args):
        return ToolResult(content="ok")

    t = tool(
        description="Reads internal state.",
        execute=handler,
        policy=ToolPolicy(action=ToolAction.READ, scope=ToolScope.INTERNAL),
    )

    metadata = t.get_metadata("read_state")

    assert metadata["policy"] == {
        "action": "read",
        "scope": "internal",
        "requires_approval": False,
        "permissions": [],
        "timeout_seconds": None,
        "audit": True,
        "max_result_chars": None,
        "offload": True,
    }
```

- [ ] Run the focused test and verify it fails because `tool(..., policy=...)` is unsupported.

Run: `cd apps/server && uv run pytest tests/test_tools.py::test_function_tool_metadata_exposes_policy -q`

Expected: `TypeError` for unexpected `policy`.

- [ ] Replace `Tool` fields with required policy in `base.py`.

Implementation rule:

```python
class Tool(ABC):
    display_name: str | None = None
    description: str
    policy: ToolPolicy
    input_model: type[BaseModel] | None = None
    kind: str = "tool"

    def get_metadata(self, name: str) -> dict:
        policy = self.policy.model_dump(mode="json")
        policy["permissions"] = sorted(policy["permissions"])
        return {
            "name": name,
            "display_name": self.display_name or name.replace("_", " ").title(),
            "description": self.description,
            "kind": self.kind,
            "policy": policy,
        }
```

Remove `mutates`, `volatile`, `requires`, and `offload` from `Tool`.

- [ ] Update `_FunctionTool.__init__` and `tool(...)` to require `policy: ToolPolicy`.

Implementation rule:

```python
def __init__(..., policy: ToolPolicy, ...):
    self.policy = policy
```

Remove `mutates`, `volatile`, `requires`, and `offload` parameters from `_FunctionTool` and `tool(...)`.

- [ ] Export `ToolAction`, `ToolPolicy`, `ToolScope`, and `PermissionDecision` from `tools/core/__init__.py` if that file already exports core types.

- [ ] Run the focused test and verify it passes.

Run: `cd apps/server && uv run pytest tests/test_tools.py::test_function_tool_metadata_exposes_policy -q`

Expected: pass.

### Task 3: Migrate Built-In Tool Declarations To Policy

**Files:**
- Modify all files matching `apps/server/arden/tools/*.py`
- Modify all files matching `apps/server/arden/integrations/*/tools.py`
- Modify: `apps/server/arden/skills/tool.py`
- Test: `apps/server/tests/test_tools.py`

- [ ] Add a test that every registered tool has a `ToolPolicy`.

```python
from arden.tools.core.types import ToolPolicy


def test_all_registered_tools_have_policy(tool_executor):
    for name, tool_obj in tool_executor.registry.tools.items():
        assert isinstance(tool_obj.policy, ToolPolicy), name
```

If `test_tools.py` has no executor fixture, build one directly:

```python
from arden.config import Config
from arden.tools.executor import ToolExecutor


def test_all_registered_tools_have_policy():
    executor = ToolExecutor(Config())
    for name, tool_obj in executor.registry.tools.items():
        assert isinstance(tool_obj.policy, ToolPolicy), name
```

- [ ] Run the test and verify it fails on unmigrated tools.

Run: `cd apps/server && uv run pytest tests/test_tools.py::test_all_registered_tools_have_policy -q`

Expected: failure naming at least one tool without policy.

- [ ] Replace every `mutates=...`, `volatile=...`, `requires=...`, and `offload=...` in `tool(...)` calls with `policy=ToolPolicy(...)`.

Mapping:

```python
# read/search/inspect internal state
ToolPolicy(action=ToolAction.READ, scope=ToolScope.INTERNAL)

# write local/session/memory/file/artifact state
ToolPolicy(action=ToolAction.WRITE, scope=ToolScope.INTERNAL, requires_approval=True)

# run bash/process/local automation
ToolPolicy(action=ToolAction.EXECUTE, scope=ToolScope.INTERNAL, requires_approval=True, timeout_seconds=120)

# read/search external services
ToolPolicy(action=ToolAction.READ, scope=ToolScope.EXTERNAL, permissions=frozenset({"gmail"}))

# create a draft in external service
ToolPolicy(action=ToolAction.DRAFT, scope=ToolScope.EXTERNAL, permissions=frozenset({"gmail"}))

# commit external side effect
ToolPolicy(action=ToolAction.WRITE, scope=ToolScope.EXTERNAL, requires_approval=True, permissions=frozenset({"gmail"}))
```

If a tool previously had `requires={"mcp"}`, use `permissions=frozenset({"mcp"})`.

If a tool previously had `offload=False`, use `offload=False` in policy.

- [ ] Update imports in migrated files.

```python
from arden.tools.core.types import ToolAction, ToolPolicy, ToolScope
```

- [ ] Run the registry test again.

Run: `cd apps/server && uv run pytest tests/test_tools.py::test_all_registered_tools_have_policy -q`

Expected: pass.

### Task 4: Switch Deferred Loading And Approval Middleware To Policy

**Files:**
- Modify: `apps/server/arden/tools/deferred.py`
- Modify: `apps/server/arden/tools/core/registry.py`
- Modify: `apps/server/arden/tools/core/middleware.py`
- Test: `apps/server/tests/test_deferred_tools.py`
- Test: `apps/server/tests/test_tools.py`

- [ ] Add or update a deferred-tool test so loading checks `policy.permissions`.

Expected behavior:

```python
assert "gmail" in some_gmail_tool.policy.permissions
```

And deferred grouping should discover/load it based on the same permission token.

- [ ] Run deferred tests and verify they fail where code still reads `tool.requires`.

Run: `cd apps/server && uv run pytest tests/test_deferred_tools.py tests/test_tools.py -q`

Expected: failures around missing `requires` or old metadata.

- [ ] Replace `tool.requires` reads with `tool.policy.permissions`.

Search command:

```bash
rg -n "\.requires|requires=" apps/server/arden apps/server/tests
```

Expected after migration: no production reads of `tool.requires`; only references to policy permissions or historical docs.

- [ ] Update approval middleware so approval is required when `tool.policy.requires_approval` is true.

Rule:

```python
if tool.policy.requires_approval and not approval_is_skipped_for_this_call:
    approval = await tool.approval_info(execution, **kwargs)
    ...
```

`approval_info(...)` remains only the preview/diff builder.

- [ ] Run focused tests.

Run: `cd apps/server && uv run pytest tests/test_deferred_tools.py tests/test_tools.py -q`

Expected: pass.

### Task 5: Apply Policy In Tool Executor

**Files:**
- Modify: `apps/server/arden/core/tool_executor.py`
- Test: `apps/server/tests/test_tool_runner.py`
- Test: `apps/server/tests/test_tools.py`

- [ ] Add a test for `policy.offload=False`.

Expected behavior:

```python
policy = ToolPolicy(
    action=ToolAction.READ,
    scope=ToolScope.INTERNAL,
    offload=False,
)
```

Large result should remain inline for that tool.

- [ ] Add a test for `policy.max_result_chars`.

Expected behavior: a tool result longer than `max_result_chars` is truncated before the model receives it, with a clear suffix such as `"... [truncated]"`.

- [ ] Add a test for `policy.timeout_seconds`.

Expected behavior: a slow tool returns a timeout tool result with `is_error=True`.

- [ ] Run tests and verify they fail against current executor behavior.

Run: `cd apps/server && uv run pytest tests/test_tool_runner.py tests/test_tools.py -q`

Expected: failures for timeout/result limit/offload policy.

- [ ] Update `ArdenToolExecutor` to read `tool.policy.offload` instead of `tool.offload`.

- [ ] Wrap tool execution with `asyncio.wait_for(...)` when `tool.policy.timeout_seconds` is not `None`.

Result on timeout:

```python
ToolResult(
    content="Tool call timed out.",
    preview="Timed out",
    is_error=True,
)
```

- [ ] Apply `policy.max_result_chars` before appending/offloading result content.

Rule: result preview should remain short and safe; result content should be bounded before it enters model context.

- [ ] Run focused tests.

Run: `cd apps/server && uv run pytest tests/test_tool_runner.py tests/test_tools.py -q`

Expected: pass.

### Task 6: Add Durable Tool Call Audit

**Files:**
- Modify: `apps/server/arden/context/store.py`
- Modify: `apps/server/arden/core/tool_executor.py`
- Test: `apps/server/tests/test_session_store.py`
- Test: `apps/server/tests/test_tool_runner.py`

- [ ] Add failing store round-trip test for `record_tool_call_started(...)` and `record_tool_call_finished(...)`.

Test shape:

```python
await store.record_tool_call_started(
    run_id="run-1",
    session_id="s-1",
    tool_call_id="call-1",
    tool_name="read_state",
    action="read",
    scope="internal",
    args_hash="abc123",
)
await store.record_tool_call_finished(
    tool_call_id="call-1",
    status="success",
    result_preview="ok",
)
rows = await store.list_tool_calls(run_id="run-1")
assert rows[0]["status"] == "success"
```

- [ ] Run the store test and verify it fails because methods/table do not exist.

Run: `cd apps/server && uv run pytest tests/test_session_store.py::test_tool_call_audit_round_trip -q`

Expected: missing method/table failure.

- [ ] Add `tool_calls` table to `SCHEMA`.

Required columns:

```sql
run_id TEXT NOT NULL,
session_id TEXT NOT NULL,
tool_call_id TEXT PRIMARY KEY,
tool_name TEXT NOT NULL,
action TEXT NOT NULL,
scope TEXT NOT NULL,
args_hash TEXT,
status TEXT NOT NULL,
result_preview TEXT,
started_at TEXT NOT NULL,
ended_at TEXT
```

Add indexes on `run_id` and `(session_id, started_at)`.

- [ ] Implement store methods:

```python
async def record_tool_call_started(...): ...
async def record_tool_call_finished(...): ...
async def list_tool_calls(self, *, run_id: str) -> list[dict]: ...
```

- [ ] In `ArdenToolExecutor`, when `tool.policy.audit` is true:

1. hash JSON-serialized validated args with sorted keys;
2. record started before execution;
3. record success/error/timeout after execution;
4. never store raw full args.

- [ ] Run focused tests.

Run: `cd apps/server && uv run pytest tests/test_session_store.py tests/test_tool_runner.py -q`

Expected: pass.

### Task 7: Add Durable Bounded Approvals

**Files:**
- Modify: `apps/server/arden/context/store.py`
- Modify: `apps/server/arden/tools/core/context.py`
- Modify: `apps/server/arden/services/chat.py`
- Modify: `apps/server/arden/server/routers/chat.py`
- Modify: `apps/server/arden/config.py`
- Test: `apps/server/tests/test_session_store.py`
- Test: `apps/server/tests/test_tools.py`

- [ ] Add store tests for approval request, approve, reject, and expire.

Expected statuses:

```python
"pending" -> "approved"
"pending" -> "rejected"
"pending" -> "expired"
```

- [ ] Run store tests and verify missing methods/table fail.

Run: `cd apps/server && uv run pytest tests/test_session_store.py -q`

Expected: missing approval storage failures.

- [ ] Add `tool_approvals` table to `SCHEMA`.

Required columns:

```sql
run_id TEXT NOT NULL,
session_id TEXT NOT NULL,
tool_call_id TEXT PRIMARY KEY,
tool_name TEXT NOT NULL,
action TEXT NOT NULL,
scope TEXT NOT NULL,
preview TEXT,
diff TEXT,
status TEXT NOT NULL,
requested_at TEXT NOT NULL,
resolved_at TEXT,
expires_at TEXT,
result_feedback TEXT
```

Add indexes on `run_id` and `(session_id, status)`.

- [ ] Add store methods:

```python
async def record_tool_approval_requested(...): ...
async def resolve_tool_approval(...): ...
async def expire_tool_approval(...): ...
async def get_tool_approval(...): ...
```

- [ ] Add `approval_timeout_seconds` to config with a finite default.

Recommended first default: `300`.

- [ ] Extend `IOBridge` with callbacks:

```python
record_approval: Callable[..., Awaitable[None]] | None = None
resolve_approval: Callable[..., Awaitable[None]] | None = None
approval_timeout_seconds: int = 300
```

- [ ] Update `ToolExecution.request_approval(...)` to:

1. record durable request before emitting;
2. create/register the Future;
3. wait with `asyncio.wait_for`;
4. expire durable row on timeout;
5. return a structured rejection/expired result.

- [ ] Update `/tools/result` route to resolve both in-memory Future and durable row.

- [ ] Run focused tests.

Run: `cd apps/server && uv run pytest tests/test_session_store.py tests/test_tools.py -q`

Expected: pass.

### Task 8: Preserve Explicit Abort Causes For Tool Results

**Files:**
- Modify: `apps/server/arden/agent/tools/dispatch.py`
- Modify: `apps/server/arden/agent/tools/runner.py`
- Test: `apps/server/tests/test_tool_runner.py`
- Test: `apps/server/tests/test_agent_lib.py`

- [ ] Add a cancellation test where two parallel calls are requested, one completes, and the run is cancelled before the other completes.

Expected appended tool result for missing call:

```text
Tool call cancelled.
```

- [ ] Add a missing-result unit test for `_append_results(...)`.

Expected fallback:

```text
Tool call result missing.
```

- [ ] Run tests and verify current code returns generic `"Error: tool execution failed"`.

Run: `cd apps/server && uv run pytest tests/test_tool_runner.py tests/test_agent_lib.py -q`

Expected: assertion failure showing old generic text.

- [ ] Change `_append_results(...)` to accept `missing_content`.

```python
def _append_results(messages, tool_calls, results, *, missing_content: str) -> None:
    ...
    "content": results.get(tc.id, missing_content)
```

- [ ] In cancellation path, call it with `"Tool call cancelled."`.

- [ ] In normal missing path, call it with `"Tool call result missing."`.

- [ ] Ensure timeout results from Task 5 are ordinary completed tool results, not missing results.

- [ ] Run focused tests.

Run: `cd apps/server && uv run pytest tests/test_tool_runner.py tests/test_agent_lib.py -q`

Expected: pass.

### Task 9: Enforce Run Budgets

**Files:**
- Modify: `apps/server/arden/constants.py`
- Modify: `apps/server/arden/config.py`
- Modify: `apps/server/arden/core/factory.py`
- Modify: `apps/server/arden/tools/core/context.py`
- Modify: `apps/server/arden/agent/agent.py`
- Modify: `apps/server/arden/agent/types/stop.py`
- Modify: `apps/server/arden/services/chat.py`
- Test: `apps/server/tests/test_agent_lib.py`

- [ ] Add failing tests for `max_iterations` being wired from `AgentConfig` into `Agent`.

Expected: an agent with `max_iterations=1` stops with `StopReason.MAX_ITERATIONS`.

- [ ] Add failing test for `max_tool_calls`.

Expected: after configured call count is reached, stop with `StopReason.MAX_TOOL_CALLS`.

- [ ] Add failing test for `max_wall_time_seconds`.

Expected: run stops with `StopReason.MAX_WALL_TIME`.

- [ ] Add failing test for `max_cost`.

Expected: when usage tracker cost exceeds budget, stop with `StopReason.MAX_COST`.

- [ ] Run tests and verify missing stop reasons/config wiring fail.

Run: `cd apps/server && uv run pytest tests/test_agent_lib.py -q`

Expected: failures for new budget paths.

- [ ] Add config fields:

```python
agent_max_iterations: int | None = None
agent_max_tool_calls: int | None = None
agent_max_wall_time_seconds: float | None = None
agent_max_cost: float | None = None
```

Add the fields to `Config` using these exact names so env vars become `ARDEN_AGENT_MAX_ITERATIONS`, `ARDEN_AGENT_MAX_TOOL_CALLS`, `ARDEN_AGENT_MAX_WALL_TIME_SECONDS`, and `ARDEN_AGENT_MAX_COST`.

- [ ] Add `max_iterations`, `max_tool_calls`, `max_wall_time_seconds`, and `max_cost` to `AgentConfig`.

- [ ] Add budget fields to `RunContext`.

- [ ] Add stop reasons:

```python
MAX_TOOL_CALLS = "max_tool_calls"
MAX_WALL_TIME = "max_wall_time"
MAX_COST = "max_cost"
```

- [ ] In `Agent.stream(...)`, check wall time and cost before each model call and after tool dispatch.

- [ ] Count tool calls from parsed calls before dispatch; if the count would exceed the budget, append tool results explaining budget denial and stop with `MAX_TOOL_CALLS`.

- [ ] Persist terminal stop reason through existing chat run status path.

- [ ] Run focused tests.

Run: `cd apps/server && uv run pytest tests/test_agent_lib.py -q`

Expected: pass.

### Task 10: MCP Tool Policies

**Files:**
- Modify: `apps/server/arden/mcp/models.py`
- Modify: `apps/server/arden/mcp/manager.py`
- Modify: `apps/server/arden/mcp/tool.py`
- Test: `apps/server/tests/test_mcp_config.py`
- Test: `apps/server/tests/test_mcp_tool.py`

- [ ] Add config parsing test for `tool_policies`.

Input:

```python
raw = {
    "transport": "http",
    "url": "https://example.com/mcp",
    "tool_policies": {
        "search": {"action": "read", "scope": "external"},
        "send_message": {
            "action": "write",
            "scope": "external",
            "requires_approval": True,
        },
    },
}
```

Expected: parsed config exposes `ToolPolicy` objects keyed by tool name.

- [ ] Add MCP tool metadata test.

Expected: `MCPTool(...).get_metadata(name)["policy"]` reflects configured policy.

- [ ] Run tests and verify they fail before implementation.

Run: `cd apps/server && uv run pytest tests/test_mcp_config.py tests/test_mcp_tool.py -q`

Expected: missing `tool_policies` support.

- [ ] Add `tool_policies: dict[str, ToolPolicy]` to `MCPServerConfig`.

- [ ] Parse raw config with `ToolPolicy.model_validate(...)`.

- [ ] Pass per-tool policy from manager to `MCPTool`.

- [ ] Give `MCPTool` a conservative default when no override exists:

```python
ToolPolicy(
    action=ToolAction.WRITE,
    scope=ToolScope.EXTERNAL,
    requires_approval=True,
    permissions=frozenset({"mcp"}),
)
```

If config restricts allowed tools via `tools`, keep that visibility behavior unchanged.

- [ ] Run focused tests.

Run: `cd apps/server && uv run pytest tests/test_mcp_config.py tests/test_mcp_tool.py -q`

Expected: pass.

### Task 11: Final B-Slice Verification And Docs

**Files:**
- Modify: `docs/internal/agent-harness-practices.md`
- Test: focused backend suite

- [ ] Search for removed compatibility fields in production code.

Run:

```bash
rg -n "\bmutates\b|\bvolatile\b|\brequires\b|\.offload\b" apps/server/arden
```

Expected: no production use of removed tool fields; `requires_approval` is allowed.

- [ ] Run focused backend suite.

Run:

```bash
cd apps/server && uv run pytest \
  tests/test_tools.py \
  tests/test_deferred_tools.py \
  tests/test_tool_runner.py \
  tests/test_session_store.py \
  tests/test_mcp_config.py \
  tests/test_mcp_tool.py \
  tests/test_agent_lib.py \
  -q
```

Expected: pass.

- [ ] Run whitespace check.

Run: `git diff --check`

Expected: no output.

- [ ] Update `docs/internal/agent-harness-practices.md`.

Required update:

```markdown
## Completed

- Consolidated tool policy object replaced scattered tool-control fields.
- Tool approvals and tool-call audit are durable.
- Tool result failures are explicit.
- Runtime budgets are enforced.

## Remaining

- Compaction rehydration.
- Prompt/cache telemetry.
- Harness scenario evals.
- Runtime planning mode.
- Skill and connector governance cleanup.
```

- [ ] Run final doc diff check.

Run: `git diff --check -- docs/internal/agent-harness-practices.md docs/superpowers/plans/2026-05-16-agent-harness-risk-hardening.md`

Expected: no output.

## C Roadmap Plans

Do not implement these in the B slice. Create separate specs/plans when B is merged.

### C1: Compaction Rehydration

- Persist pending approvals, background task refs, active plan refs, artifact refs, and loop task ID as structured compaction metadata.
- Keep clearing loaded deferred tools on compaction; the model can reload a deferred group if it is still needed.
- Test that compaction drops deferred tool visibility while preserving pending control-plane state.

### C2: Prompt And Cache Telemetry

- Store deterministic hashes for static prompt, tool schema bundle, visible tool names, and selected skill index.
- Persist cached token fields and cache-read ratio in run metadata.
- Add fragmentation reporting by prompt/tool hash.

### C3: Harness Scenario Evals

- Add fake-model tests for unknown tool, invalid args, approval bypass, permission denial, timeout, cancellation, huge output, connector auth failure, prompt injection, and compaction retention.

### C4: Runtime Planning Mode

- Add session mode: `chat | planning | executing`.
- In planning mode, expose read/search/draft-safe tools and block writes, sends, deletes, process execution, and irreversible actions.
- Tie risky approvals to an approved plan artifact version.

### C5: Skill And Connector Governance

- Enforce skill metadata quality.
- Add source/version/review metadata.
- Add read-only inventory and stale-candidate reports.
- Keep cleanup candidate-only unless user approves mutation.

## Execution Notes

- Prefer one task per commit.
- Do not change desktop UI unless a backend event shape requires a minimal corresponding update.
- Do not keep compatibility fields. If a tool still needs old behavior, express it in `ToolPolicy`.
- Hide unavailable tools before model exposure when possible; use runtime `DENY` only for contextual blocking.
