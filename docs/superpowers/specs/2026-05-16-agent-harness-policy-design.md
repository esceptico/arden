# Agent Harness Policy Design

## Objective

Make Arden's agent harness safer and more legible by replacing scattered tool-control fields with one explicit `ToolPolicy`, then using that policy for runtime approval, audit, result limits, connector classification, and budgets.

This design implements the first buildable slice of the `agents-best-practices` review, with a roadmap for the broader harness improvements.

## Scope

Included in the first implementation slice:

- Replace tool-level `mutates`, `volatile`, `requires`, and ad hoc approval flags with one required `ToolPolicy`.
- Classify built-in and MCP tools with a simple action/scope model.
- Use policy for approval decisions, tool metadata, deferred loading, execution timeouts, large-result handling, and audit eligibility.
- Add durable tool-call audit records.
- Add durable, timeout-bounded approval records.
- Return explicit tool results for cancellation, timeout, denial, invalid arguments, unknown tool, and missing result.
- Enforce run budgets for iterations, tool calls, wall time, and cost.

Deferred roadmap:

- Compaction rehydration for loaded tools, pending approvals, background tasks, and active plan refs.
- Prompt/cache telemetry with prompt and tool bundle hashes.
- Harness scenario evals for adversarial and failure cases.
- Runtime planning mode.
- Skill and connector governance cleanup.

Non-goals:

- No agent loop rewrite.
- No broad policy engine.
- No multi-agent orchestration change.
- No UI redesign beyond existing approval/result surfaces.
- No static `deny` field on tool policy; blocked tools should usually be hidden or denied by contextual runtime checks.

## Source Alignment

This design follows the upstream `agents-best-practices` docs:

- The harness owns validation, permission decisions, execution, tracing, and recovery.
- Every tool call receives exactly one result, including denial, timeout, cancellation, or error.
- Risky side effects need runtime enforcement outside the model.
- Tool contracts include risk, permission behavior, timeout, result limits, and audit behavior.
- MCP and connector tools are namespaced, scoped, risk-mapped, approval-gated when risky, and logged.
- Budgets are hard stop conditions, not only observations.
- Compaction must preserve active control-plane state.

## Tool Policy

Every tool must declare one policy:

```python
class ToolAction(str, Enum):
    READ = "read"
    DRAFT = "draft"
    WRITE = "write"
    EXECUTE = "execute"


class ToolScope(str, Enum):
    INTERNAL = "internal"
    EXTERNAL = "external"


class ToolPolicy(BaseModel):
    action: ToolAction
    scope: ToolScope
    requires_approval: bool = False
    permissions: frozenset[str] = frozenset()
    timeout_seconds: int | None = None
    audit: bool = True
    max_result_chars: int | None = None
    offload: bool = True
```

Definitions:

- `action=read`: reads, searches, or computes without changing source-of-truth state.
- `action=draft`: creates a reviewable proposal, draft, or staged artifact without committing it.
- `action=write`: changes source-of-truth state.
- `action=execute`: runs code, shell, browser automation, or another process-like action.
- `scope=internal`: arden-controlled state, local workspace/files, local process, local DB, session, memory, or artifacts.
- `scope=external`: third-party systems, remote services, external people, remote MCP, or network side effects.

Examples:

- Read session, memory, or file: `READ + INTERNAL`.
- Write file, session, memory, or artifact: `WRITE + INTERNAL`.
- Run bash or local process: `EXECUTE + INTERNAL`.
- Search Gmail, Slack, web, or remote resources: `READ + EXTERNAL`.
- Draft Slack/Gmail/calendar/GitHub content: `DRAFT + EXTERNAL`.
- Send email, post Slack message, create calendar event: `WRITE + EXTERNAL`.
- Remote automation or remote MCP command: `EXECUTE + EXTERNAL`.

`DRAFT` means the tool prepares a reviewable artifact or proposal. `WRITE` means the tool commits a change to source-of-truth state.

## Permission Decision

Static policy says the default behavior for a visible tool. Runtime permission evaluation can still make a contextual decision:

```python
class PermissionDecision(str, Enum):
    EXECUTE = "execute"
    REQUEST_APPROVAL = "request_approval"
    DENY = "deny"
```

Decision inputs:

- tool policy;
- tool arguments;
- session mode;
- loaded/deferred tool state;
- user/session scope;
- MCP server config;
- budget state;
- optional future user settings.

Default rule:

- If the tool is not visible or not loaded, the model should not see it.
- If visible and `requires_approval=False`, execute after schema validation unless contextual checks deny it.
- If visible and `requires_approval=True`, create an approval request before execution.
- If contextual checks deny the call, append a `permission_denied` tool result.

This keeps static policy small while preserving runtime deny for planning mode, hosted/local boundaries, connector revocation, kill switches, or scope failures.

## Runtime Flow

The tool registry accepts only tools with `policy`.

Tool metadata emitted to the client includes:

- `action`;
- `scope`;
- `requires_approval`;
- `permissions`;
- `timeout_seconds`;
- `audit`;
- `max_result_chars`;
- `offload`.

The deferred tool middleware uses `policy.permissions` instead of `requires`. Tool visibility should remain the first line of defense: tools that are irrelevant or blocked should not be loaded.

Approval middleware uses `policy.requires_approval` plus contextual permission checks. `approval_info(...)` can remain as the preview/diff builder, but it no longer decides whether approval is required.

The executor applies:

- `timeout_seconds` around tool execution;
- `max_result_chars` before appending result content;
- `offload` for large result references;
- `audit` to decide whether to write durable call records.

MCP tools receive a default policy from MCP config. If no override exists, remote MCP tools should default to conservative policy: external scope, audit enabled, and approval required for non-read actions.

## Durable Tool Audit

Add a durable table for tool calls, not only connector calls.

Required fields:

- `run_id`
- `session_id`
- `tool_call_id`
- `tool_name`
- `action`
- `scope`
- `args_hash`
- `status`
- `result_preview`
- `started_at`
- `ended_at`

Only write rows when `tool.policy.audit=True`.

Do not store raw secrets or full raw arguments by default. Store argument hashes and safe previews. This gives enough evidence to debug what happened without leaking credentials into traces.

## Durable Approvals

Add a durable table for approval requests.

Required fields:

- `run_id`
- `session_id`
- `tool_call_id`
- `tool_name`
- `action`
- `scope`
- `preview`
- `diff`
- `status`
- `requested_at`
- `resolved_at`
- `expires_at`
- `result_feedback`

Statuses:

- `pending`
- `approved`
- `rejected`
- `expired`
- `cancelled`

Execution flow:

1. Record approval request before emitting the UI event.
2. Register the in-memory Future for live response routing.
3. Wait with a timeout.
4. Resolve both the Future and durable row when `/tools/result` arrives.
5. If the timeout fires, mark approval `expired` and return an expired/rejected tool result.

The model cannot approve its own action.

## Tool Results And Abort Causes

Every raw provider tool call must get exactly one tool result.

Required structured result types:

- `unknown_tool`
- `invalid_arguments`
- `permission_denied`
- `approval_rejected`
- `approval_expired`
- `timeout`
- `cancelled`
- `missing_result`
- `internal_error`

The current generic fallback should become cause-specific. This improves replay, model recovery, and evals.

## Budgets

Add hard runtime budgets:

- `max_iterations`
- `max_tool_calls`
- `max_wall_time_seconds`
- `max_cost`

Budget state belongs to the run, not only the prompt. When a budget is reached, the agent stops with a clear `stop_reason` and persists it on the chat run.

Budget stop reasons should be explicit enough for UI and debugging:

- `max_iterations`
- `max_tool_calls`
- `max_wall_time`
- `max_cost`

The existing `Agent.max_iterations` support should be wired from config as the first budget fix.

## MCP Policy

MCP server config should support per-tool policy overrides.

Example shape:

```json
{
  "tool_policies": {
    "search": {
      "action": "read",
      "scope": "external",
      "requires_approval": false
    },
    "send_message": {
      "action": "write",
      "scope": "external",
      "requires_approval": true
    }
  }
}
```

Remote tool descriptions and schemas are not trusted policy. Local config decides policy. If the local config is missing, default conservatively.

## Roadmap

After the policy and recoverability slice lands, continue with these stages.

### Compaction Rehydration

Persist and reattach control-plane state:

- loaded deferred tools;
- invoked skills;
- pending approval IDs;
- background task counts and refs;
- active plan ref;
- important artifact refs;
- loop task ID.

Compaction summaries should not hide this inside prose.

### Prompt And Cache Telemetry

Store deterministic hashes for:

- static prompt;
- tool schema bundle;
- visible tool names;
- selected skill index.

Track cached tokens, cache-read ratio, and prompt/tool hash fragmentation in run metadata.

### Harness Scenario Evals

Add fake-model scenario tests for:

- unknown tool;
- invalid arguments;
- approval bypass attempt;
- permission denied;
- approval timeout;
- cancellation;
- huge tool result;
- connector auth failure;
- prompt injection in retrieved content;
- compaction state retention.

### Runtime Planning Mode

Planning mode should expose read/search/draft-safe tools and block writes, sends, deletes, process execution, and irreversible actions until a plan artifact is approved.

### Skill And Connector Governance

Add inventory and cleanup flows:

- stricter skill metadata validation;
- source/version/review metadata;
- stale skill and stale plan candidates;
- repeated tool failure reports;
- connector policy inventory.

Cleanup should produce candidates only. It should not self-modify without approval.

## Testing

Focused tests for the first implementation slice:

- tool policy is required for all tools;
- tool metadata exposes policy fields;
- deferred loading reads `policy.permissions`;
- approval middleware uses `requires_approval`;
- approval records persist approve/reject/expire paths;
- tool audit records success/error/denial without raw args;
- MCP config parses per-tool policies;
- timeout returns a timeout tool result;
- cancellation appends cancellation tool results;
- `max_iterations`, `max_tool_calls`, wall-time, and cost budgets stop runs with explicit reasons.

## Launch Criteria

The first slice is complete when:

- all built-in tools declare policy;
- MCP tools have conservative default policy and config override support;
- approval state is durable and bounded;
- audited tool calls are queryable by run/session;
- budget stop reasons are persisted;
- provider tool-call result contracts are preserved under errors and cancellation;
- focused backend tests pass;
- `docs/internal/agent-harness-practices.md` is updated with completed and remaining risks.
