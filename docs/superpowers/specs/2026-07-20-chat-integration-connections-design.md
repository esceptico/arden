# Chat Integration Connections Design

## Goal

Let ntrp recover broken native integrations and propose required disconnected integrations from chat, while keeping recovery and proactive suggestion as separate product features.

Native `Integration` registrations are canonical. MCP servers may implement the same contract later, but MCP discovery and a public server catalog are out of scope.

## Product boundaries

### Connection recovery

Recovery begins only after an attempted integration tool reports a typed connection failure. It never infers authentication state from arbitrary exception text.

Supported reasons:

- `not_configured`
- `disabled`
- `auth_required`
- `scope_required`
- `degraded`

The chat shows a connection card, waits for the user, verifies the connection, refreshes the runtime registry, and retries the exact call once when the provider declares that retry safe. Reads may be retried after a known pre-execution authentication failure. Ambiguous writes are not retried automatically.

### Integration suggestion

Suggestion begins before a tool call, when the model determines that the user's explicit request requires a known but disconnected capability and no connected tool can satisfy it.

The model receives a server-generated allowlist of disconnected registered integrations and may call `request_connection(integration_id, reason)`. The tool validates the identifier against that allowlist. There is no keyword, regex, or error-string suggestion engine.

Declining a suggestion lets the model continue without that provider and prevents another prompt for the same integration in the current run.

### Installation and MCP catalog

Installing plugins or browsing a public MCP marketplace is a third feature and is not part of this implementation. Existing configured MCP inventory and manual MCP setup remain unchanged.

## Server architecture

### Integration connection contract

Extend the base integration model with provider-owned connection metadata:

- stable integration and connection identifiers
- user-facing label and capability summary
- setup action: `oauth`, `credentials`, `enable`, or `settings`
- settings destination
- current connection state and detail
- requested scopes when applicable
- registered tool names

Gmail and Calendar remain separate capabilities but share the `google` connection target. Slack uses the existing secure credentials setup. Secrets never travel through model-visible tool arguments or chat events.

`IntegrationRegistry` produces two views:

- active clients/tools for execution
- all registered connection descriptors, including disconnected integrations, for constrained model suggestions and UI verification

### Typed failures

Add `IntegrationConnectionError`, carrying:

- integration identifier
- reason code
- user-facing detail
- required scopes
- whether the failure occurred before side effects and exact retry is safe

Google credential refresh and scope failures raise this type. Slack maps documented API error codes such as invalid/revoked authentication and missing scope to this type. Unrelated provider errors remain ordinary tool failures.

### Request lifecycle

Add `ToolExecution.request_connection(...)`, backed by a per-tool `pending_connections` future. It emits `connection_needed` and blocks until the user connects or declines.

Connection requests use the existing durable run-suspension table with a distinct `integration_connection` kind. They get dedicated storage helpers, replay filtering, runtime snapshots, and resolution routing. Approval auto-mode never resolves them.

The resolution endpoint verifies the integration is connected before resolving an accepted request. Verification refreshes runtime configuration and integration clients. A rejected request resolves immediately with optional feedback.

### Tool execution and refresh

`request_connection` is always available in interactive chat and validates against the current disconnected catalog.

After an accepted suggestion, the run adds that integration's tool names to its allowed and loaded tool sets. Deferred-tool middleware therefore exposes the newly connected tools on the next model step without rebuilding the conversation.

When a typed runtime failure is raised, the executor requests recovery. After successful verification it retries once only when the error and tool policy establish that retry is safe. Otherwise it returns a structured recoverable result so the model can retry explicitly.

Background and headless runs cannot open connection UI. They receive a structured failure explaining which integration needs attention.

## Desktop behavior

Add a dedicated pending-connection store slice populated from `connection_needed` and runtime snapshots.

Render a compact solid connection card above the composer while the run is waiting. It shows:

- integration label
- required capability
- concrete reason
- requested scopes when present
- primary action
- `Not now`

Google OAuth can start directly from the card through the existing secure API. Slack opens Settings > Integrations because the token must be entered in a secure settings surface. The card remains pending and offers `Check connection` after returning.

The client calls the connection-resolution endpoint only after setup succeeds or the user explicitly declines. Failed verification leaves the card actionable and displays the server error.

Resolved cards disappear from the sticky action area. The normal tool timeline records the resulting success or failure; no extra conversational message is inserted.

## Event contract

`connection_needed` contains:

- `tool_id`
- `integration_id`
- `connection_id`
- `label`
- `reason`
- `detail`
- `capability`
- `action`
- `settings_tab`
- `required_scopes`
- `source`: `recovery` or `suggestion`

No token, credential, authorization code, or provider response body is included.

## Error handling

- Unknown or already-connected suggestion identifiers return a normal tool error and do not emit a card.
- Accepted requests are verified server-side; client success alone is insufficient.
- OAuth/setup failure leaves the request pending for retry or decline.
- Disconnecting the desktop does not cancel the request; replay and runtime snapshot restore it.
- Server restart resumes from the durable suspension. A completed but unconsumed resolution is consumed exactly once.
- Connection timeout returns a recoverable tool failure.
- Writes with uncertain side effects never auto-retry.

## Testing

Server tests cover:

- registry descriptors for connected, disabled, unconfigured, and error states
- strict suggestion allowlist validation
- no heuristic suggestion path
- typed Google and Slack auth/scope errors
- connection event serialization and durable suspension lifecycle
- accept, decline, verification failure, replay, and timeout
- safe read retry exactly once and no automatic ambiguous write retry
- newly connected tool visibility on the next model step

Desktop tests cover:

- event projection and runtime snapshot restoration
- Google direct OAuth action
- Slack settings deep-link and subsequent verification
- decline, verification failure, retry, and resolved removal
- card copy, scopes, keyboard focus, reduced motion, and no secret rendering

End-to-end verification exercises one proactive Gmail connection and one broken-credential recovery path without changing MCP behavior.

## Non-goals

- public MCP directory or marketplace
- plugin installation
- automatic connector ranking
- keyword or regex matching over user text
- collecting secrets inside chat
- silently enabling or connecting providers without explicit user action
