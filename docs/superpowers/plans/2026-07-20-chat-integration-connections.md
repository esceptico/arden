# Chat Integration Connections Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build native-integration connection recovery and constrained chat connection suggestions for Gmail, Calendar, and Slack.

**Architecture:** Registered native integrations expose connection metadata and typed failures. A dedicated `request_connection` tool and executor recovery path suspend the run through a durable connection request, while the desktop renders a secure action card and resolves it only after server-side verification. MCP remains an adapter target; no MCP catalog is added.

**Tech Stack:** Python 3.13, FastAPI, Pydantic, asyncio, SQLite run suspensions, React 19, TypeScript, Zustand, Vitest, Testing Library.

## Global Constraints

- Native `Integration` registrations are canonical; MCP discovery and installation are out of scope.
- Recovery and proactive suggestion remain separate triggers.
- No keyword, regex, or arbitrary error-string matching.
- Secrets never enter model-visible arguments, events, or transcript content.
- Accepted connections are verified server-side.
- Retry exactly once only for known pre-side-effect failures; ambiguous writes never auto-retry.
- Work directly on `main` because the user explicitly requested it.
- Preserve all unrelated dirty-worktree changes.

---

### Task 1: Native connection descriptors

**Files:**
- Modify: `apps/server/ntrp/integrations/base.py`
- Modify: `apps/server/ntrp/integrations/registry.py`
- Modify: `apps/server/ntrp/integrations/gmail/__init__.py`
- Modify: `apps/server/ntrp/integrations/calendar/__init__.py`
- Modify: `apps/server/ntrp/integrations/slack/__init__.py`
- Test: `apps/server/tests/test_integration_connections.py`

**Interfaces:**
- Produces `ConnectionState`, `ConnectionAction`, `IntegrationConnectionSpec`, `IntegrationConnectionDescriptor`, `IntegrationConnectionError`.
- Produces `IntegrationRegistry.list_connections()` and `IntegrationRegistry.get_connection(integration_id)`.

- [ ] **Step 1: Write failing descriptor tests**

```python
def test_registry_lists_disconnected_native_capabilities():
    registry = IntegrationRegistry([GMAIL, CALENDAR, SLACK])
    registry.sync(Config(google=False, slack_bot_token=None, slack_user_token=None))
    rows = {row.integration_id: row for row in registry.list_connections()}
    assert rows["gmail"].connection_id == "google"
    assert rows["gmail"].state == "disabled"
    assert rows["slack"].state == "not_configured"
```

- [ ] **Step 2: Run the test and confirm RED**

Run: `cd apps/server && uv run pytest tests/test_integration_connections.py -q`

Expected: import failure for the new descriptor contract.

- [ ] **Step 3: Implement connection domain types and provider metadata**

```python
ConnectionState = Literal["connected", "not_configured", "disabled", "auth_required", "scope_required", "degraded"]
ConnectionAction = Literal["oauth", "credentials", "enable", "settings"]

@dataclass(frozen=True)
class IntegrationConnectionSpec:
    connection_id: str
    capability: str
    action: ConnectionAction
    settings_tab: str = "integrations"
    enabled: Callable[[Config], bool] | None = None
    configured: Callable[[Config], bool] | None = None

@dataclass(frozen=True)
class IntegrationConnectionDescriptor:
    integration_id: str
    connection_id: str
    label: str
    capability: str
    action: ConnectionAction
    settings_tab: str
    state: ConnectionState
    detail: str | None = None
    required_scopes: tuple[str, ...] = ()
    tool_names: tuple[str, ...] = ()
```

Store the last synced `Config` in `IntegrationRegistry`; derive state from the live client, typed build error, enabled predicate, and configured predicate in that order.

- [ ] **Step 4: Run descriptor tests and confirm GREEN**

Run: `cd apps/server && uv run pytest tests/test_integration_connections.py -q`

- [ ] **Step 5: Commit the descriptor contract**

```bash
git add apps/server/ntrp/integrations apps/server/tests/test_integration_connections.py
git commit -m "feat: add native integration connection descriptors"
```

### Task 2: Connection prompt and request tool

**Files:**
- Create: `apps/server/ntrp/tools/connections.py`
- Modify: `apps/server/ntrp/integrations/core.py`
- Modify: `apps/server/ntrp/core/prompts.py`
- Modify: `apps/server/ntrp/services/chat.py`
- Modify: `apps/server/ntrp/server/runtime/core.py`
- Test: `apps/server/tests/test_connection_suggestions.py`

**Interfaces:**
- Produces `render_connection_catalog(descriptors)`.
- Produces `request_connection_tool` with `RequestConnectionInput(integration_id, reason)`.
- Adds a dynamic `connections_context` system block containing only disconnected registered integrations.

- [ ] **Step 1: Write failing allowlist and prompt tests**

```python
def test_prompt_lists_only_registered_disconnected_connections():
    text = render_connection_catalog([gmail_descriptor, connected_slack_descriptor])
    assert 'integration_id="gmail"' in text
    assert 'integration_id="slack"' not in text
    assert "Only request a connection when the user's explicit request requires it" in text

@pytest.mark.asyncio
async def test_request_connection_rejects_unknown_integration(execution):
    result = await request_connection(execution, RequestConnectionInput(integration_id="notion", reason="needed"))
    assert result.is_error
    assert result.outcome.error.code == "connection_not_available"
```

- [ ] **Step 2: Run the tests and confirm RED**

Run: `cd apps/server && uv run pytest tests/test_connection_suggestions.py -q`

- [ ] **Step 3: Implement catalog rendering and strict request validation**

```python
class RequestConnectionInput(BaseModel):
    integration_id: str = Field(description="Exact integration_id from <available_connections>.")
    reason: str = Field(description="Short explanation tied to the user's explicit request.")

async def request_connection(execution: ToolExecution, args: RequestConnectionInput) -> ToolResult:
    service = execution.ctx.get_client("connections", ConnectionService)
    descriptor = service.get_disconnected(args.integration_id) if service else None
    if descriptor is None:
        return ToolResult.failure(code="connection_not_available", message="That connection is not available.")
    accepted = await execution.request_connection(descriptor, source="suggestion", detail=args.reason)
    return service.result_for_resolution(execution, descriptor, accepted)
```

Register the tool in `_system`, inject `ConnectionService` into runtime tool services, and append the catalog as a dynamic system block during `_prepare_messages`.

- [ ] **Step 4: Run suggestion tests and confirm GREEN**

Run: `cd apps/server && uv run pytest tests/test_connection_suggestions.py -q`

- [ ] **Step 5: Commit suggestion support**

```bash
git add apps/server/ntrp/tools/connections.py apps/server/ntrp/integrations/core.py apps/server/ntrp/core/prompts.py apps/server/ntrp/services/chat.py apps/server/ntrp/server/runtime/core.py apps/server/tests/test_connection_suggestions.py
git commit -m "feat: add constrained integration connection requests"
```

### Task 3: Durable connection suspension and HTTP resolution

**Files:**
- Modify: `apps/server/ntrp/events/sse.py`
- Modify: `apps/server/ntrp/tools/core/context.py`
- Modify: `apps/server/ntrp/server/state.py`
- Modify: `apps/server/ntrp/context/store.py`
- Modify: `apps/server/ntrp/services/chat.py`
- Modify: `apps/server/ntrp/server/schemas.py`
- Modify: `apps/server/ntrp/server/routers/chat.py`
- Modify: `apps/server/ntrp/server/routers/session.py`
- Test: `apps/server/tests/test_connection_requests.py`
- Test: `apps/server/tests/test_streaming_events.py`

**Interfaces:**
- Produces `ConnectionNeededEvent` and `ConnectionResponse`.
- Adds `RunState.pending_connections` and `IOBridge.pending_connections`.
- Adds `POST /connections/result` accepting `{run_id, tool_id, approved, result}`.
- Adds `integration_connection` run-suspension storage helpers and runtime snapshot rows.

- [ ] **Step 1: Write failing event, wait, resolution, replay, and snapshot tests**

```python
@pytest.mark.asyncio
async def test_request_connection_waits_for_matching_resolution():
    pending = {}
    execution = make_execution(IOBridge(emit=emit, pending_connections=pending))
    task = asyncio.create_task(execution.request_connection(descriptor, source="suggestion"))
    await event_seen.wait()
    pending["call-1"].set_result({"approved": True, "result": "connected"})
    assert await task is True

def test_connection_event_contains_no_secret_fields():
    payload = ConnectionNeededEvent(
        tool_id="call-1",
        integration_id="gmail",
        connection_id="google",
        label="Gmail",
        reason="auth_required",
        detail="Reconnect Gmail",
        capability="Read and send email",
        action="oauth",
        settings_tab="integrations",
        required_scopes=[],
        source="recovery",
    ).to_sse()["data"]
    assert "token" not in payload.lower()
    assert "credential" not in payload.lower()
```

- [ ] **Step 2: Run the tests and confirm RED**

Run: `cd apps/server && uv run pytest tests/test_connection_requests.py tests/test_streaming_events.py -q`

- [ ] **Step 3: Implement dedicated connection suspension plumbing**

```python
@dataclass(frozen=True)
class ConnectionNeededEvent(SSEEvent):
    type: EventType = field(default=EventType.CONNECTION_NEEDED, init=False)
    tool_id: str = ""
    integration_id: str = ""
    connection_id: str = ""
    label: str = ""
    reason: str = ""
    detail: str = ""
    capability: str = ""
    action: str = "settings"
    settings_tab: str = "integrations"
    required_scopes: list[str] = field(default_factory=list)
    source: Literal["recovery", "suggestion"] = "suggestion"
```

Use `record_run_suspension(kind="integration_connection", payload=descriptor)` and `resolve_run_suspension`; filter replay by pending live future or pending durable row. Verification calls `runtime.reload_config()` and requires `descriptor.state == "connected"` before resolving accepted requests.

- [ ] **Step 4: Run connection lifecycle tests and confirm GREEN**

Run: `cd apps/server && uv run pytest tests/test_connection_requests.py tests/test_streaming_events.py tests/test_session_runtime_snapshot.py -q`

- [ ] **Step 5: Commit durable connection requests**

```bash
git add apps/server/ntrp/events/sse.py apps/server/ntrp/tools/core/context.py apps/server/ntrp/server/state.py apps/server/ntrp/context/store.py apps/server/ntrp/services/chat.py apps/server/ntrp/server/schemas.py apps/server/ntrp/server/routers/chat.py apps/server/ntrp/server/routers/session.py apps/server/tests
git commit -m "feat: persist chat connection requests"
```

### Task 4: Typed provider recovery and safe retry

**Files:**
- Modify: `apps/server/ntrp/integrations/google_auth/auth.py`
- Modify: `apps/server/ntrp/integrations/gmail/client.py`
- Modify: `apps/server/ntrp/integrations/slack/client.py`
- Modify: `apps/server/ntrp/core/tool_executor.py`
- Test: `apps/server/tests/test_connection_recovery.py`
- Test: `apps/server/tests/test_gmail.py`
- Test: `apps/server/tests/test_slack.py`

**Interfaces:**
- Consumes `IntegrationConnectionError` and `ToolExecution.request_connection`.
- Produces exact-once safe retry for read tools and `connection_retry_required` for unsafe writes.

- [ ] **Step 1: Write failing typed-error and retry tests**

```python
def test_slack_missing_scope_is_typed():
    with pytest.raises(IntegrationConnectionError) as raised:
        client._raise_for_error("conversations.list", {"error": "missing_scope", "needed": "channels:read"}, {})
    assert raised.value.reason == "scope_required"
    assert raised.value.required_scopes == ("channels:read",)

@pytest.mark.asyncio
async def test_read_retries_once_after_connection():
    result = await executor.execute("emails", {}, "call-1")
    assert result.content == "mail"
    assert provider_calls == 2

@pytest.mark.asyncio
async def test_write_does_not_auto_retry_after_connection():
    result = await executor.execute("send_email", payload, "call-2")
    assert result.outcome.error.code == "connection_retry_required"
    assert provider_calls == 1
```

- [ ] **Step 2: Run the tests and confirm RED**

Run: `cd apps/server && uv run pytest tests/test_connection_recovery.py tests/test_gmail.py tests/test_slack.py -q`

- [ ] **Step 3: Implement typed provider mappings and recovery wrapper**

Map Google refresh/missing-scope failures at credential boundaries and Slack's `invalid_auth`, `token_revoked`, `account_inactive`, `not_authed`, and `missing_scope` codes. Remove Gmail paths that convert these failures into success-shaped strings.

Catch only `IntegrationConnectionError` around registry execution. Request recovery, then execute a second time only when `error.retry_safe` is true and the tool action is `READ`.

- [ ] **Step 4: Run recovery tests and confirm GREEN**

Run: `cd apps/server && uv run pytest tests/test_connection_recovery.py tests/test_gmail.py tests/test_slack.py -q`

- [ ] **Step 5: Commit typed recovery**

```bash
git add apps/server/ntrp/integrations apps/server/ntrp/core/tool_executor.py apps/server/tests
git commit -m "feat: recover typed native integration failures"
```

### Task 5: Desktop connection state and secure action card

**Files:**
- Modify: `apps/desktop/src/api/events.ts`
- Modify: `apps/desktop/src/api/chat.ts`
- Modify: `apps/desktop/src/stores/types.ts`
- Modify: `apps/desktop/src/stores/index.ts`
- Modify: `apps/desktop/src/stores/chat-stream.ts`
- Create: `apps/desktop/src/actions/connections.ts`
- Create: `apps/desktop/src/features/chat/components/ConnectionBanner.tsx`
- Modify: `apps/desktop/src/features/chat/components/Chat.tsx`
- Test: `apps/desktop/tests/connectionProjection.test.ts`
- Test: `apps/desktop/tests/connectionBanner.test.tsx`

**Interfaces:**
- Produces `PendingConnection` store rows and `resolveConnection(toolId, approved)`.
- Renders Google OAuth directly and deep-links Slack to Settings > Integrations.

- [ ] **Step 1: Write failing projection and component tests**

```typescript
test("connection_needed adds one pending card without exposing secrets", () => {
  applyEvent(connectionEvent);
  expect(useStore.getState().pendingConnections).toEqual([
    expect.objectContaining({ toolId: "call-1", integrationId: "gmail", action: "oauth" }),
  ]);
  expect(JSON.stringify(useStore.getState().pendingConnections)).not.toContain("access_token");
});

test("Slack connection opens integrations settings and stays pending", async () => {
  render(<ConnectionBanner />);
  await user.click(screen.getByRole("button", { name: "Open settings" }));
  expect(useStore.getState().settingsTab).toBe("integrations");
  expect(useStore.getState().pendingConnections).toHaveLength(1);
});
```

- [ ] **Step 2: Run desktop tests and confirm RED**

Run: `cd apps/desktop && bun test tests/connectionProjection.test.ts tests/connectionBanner.test.tsx`

- [ ] **Step 3: Implement state, API action, and card**

```typescript
export interface PendingConnection {
  toolId: string;
  runId: string | null;
  integrationId: string;
  connectionId: string;
  label: string;
  reason: ConnectionReason;
  detail: string;
  capability: string;
  action: "oauth" | "credentials" | "enable" | "settings";
  settingsTab: "integrations" | "mcp";
  requiredScopes: string[];
  source: "recovery" | "suggestion";
}
```

Google's primary button calls `addGmailAccountApi` with `email`, `calendar`, or `all`, then posts accepted resolution. Slack opens `openSettings(origin, "integrations")`; the card changes its primary action to `Check connection`. `Not now` posts rejected resolution. Errors remain local to the card.

- [ ] **Step 4: Run desktop tests and confirm GREEN**

Run: `cd apps/desktop && bun test tests/connectionProjection.test.ts tests/connectionBanner.test.tsx`

- [ ] **Step 5: Commit desktop connection UX**

```bash
git add apps/desktop/src/api apps/desktop/src/stores apps/desktop/src/actions/connections.ts apps/desktop/src/features/chat/components/ConnectionBanner.tsx apps/desktop/src/features/chat/components/Chat.tsx apps/desktop/tests/connectionProjection.test.ts apps/desktop/tests/connectionBanner.test.tsx
git commit -m "feat: show integration connection actions in chat"
```

### Task 6: Full verification and completion audit

**Files:**
- Verify and, when a gate exposes a defect, modify only the feature files listed in Tasks 1-5
- Verify: `docs/superpowers/specs/2026-07-20-chat-integration-connections-design.md`

**Interfaces:**
- Confirms every spec requirement through current tests, type checks, and focused runtime probes.

- [ ] **Step 1: Run focused server suite**

Run: `cd apps/server && uv run pytest tests/test_integration_connections.py tests/test_connection_suggestions.py tests/test_connection_requests.py tests/test_connection_recovery.py tests/test_streaming_events.py tests/test_session_runtime_snapshot.py -q`

- [ ] **Step 2: Run server type/lint gates used by the repository**

Run: `cd apps/server && uv run ruff check ntrp tests`

- [ ] **Step 3: Run focused desktop suite**

Run: `cd apps/desktop && bun test tests/connectionProjection.test.ts tests/connectionBanner.test.tsx`

- [ ] **Step 4: Run desktop typecheck and build**

Run: `cd apps/desktop && bun run typecheck && bun run build`

- [ ] **Step 5: Audit the spec requirement by requirement**

Confirm from current files and test output that recovery, suggestion, durable resolution, replay, secure setup, retry safety, and non-goals match the design. Confirm `git diff --check` and inspect the exact feature diff without staging unrelated work.

- [ ] **Step 6: Commit verification fixes if any**

```bash
git add apps/server/ntrp/integrations/base.py apps/server/ntrp/integrations/registry.py apps/server/ntrp/integrations/gmail/__init__.py apps/server/ntrp/integrations/calendar/__init__.py apps/server/ntrp/integrations/slack/__init__.py apps/server/ntrp/integrations/google_auth/auth.py apps/server/ntrp/integrations/gmail/client.py apps/server/ntrp/integrations/slack/client.py apps/server/ntrp/integrations/core.py apps/server/ntrp/tools/connections.py apps/server/ntrp/tools/core/context.py apps/server/ntrp/core/prompts.py apps/server/ntrp/core/tool_executor.py apps/server/ntrp/services/chat.py apps/server/ntrp/server/runtime/core.py apps/server/ntrp/events/sse.py apps/server/ntrp/server/state.py apps/server/ntrp/context/store.py apps/server/ntrp/server/schemas.py apps/server/ntrp/server/routers/chat.py apps/server/ntrp/server/routers/session.py apps/server/tests/test_integration_connections.py apps/server/tests/test_connection_suggestions.py apps/server/tests/test_connection_requests.py apps/server/tests/test_connection_recovery.py apps/desktop/src/api/events.ts apps/desktop/src/api/chat.ts apps/desktop/src/stores/types.ts apps/desktop/src/stores/index.ts apps/desktop/src/stores/chat-stream.ts apps/desktop/src/actions/connections.ts apps/desktop/src/features/chat/components/ConnectionBanner.tsx apps/desktop/src/features/chat/components/Chat.tsx apps/desktop/tests/connectionProjection.test.ts apps/desktop/tests/connectionBanner.test.tsx
git commit -m "test: verify integration connection flows"
```
