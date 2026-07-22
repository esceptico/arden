# Command-palette sidecar

## Goal

Extend the existing command palette with `Cmd/Ctrl+Enter`: natural-language requests run in a transient side peek, reuse Arden's canonical tool registry, execute permitted actions through the normal policy pipeline, and navigate directly to the resolved in-app resource.

`Enter` remains the instant deterministic palette action. `Cmd/Ctrl+Enter` is the explicit agent boundary.

## Product contract

- A non-empty palette query may be submitted with `Cmd/Ctrl+Enter`.
- The palette closes and a compact right-side command peek opens immediately.
- The peek streams the command run's reasoning summary, tool calls, approvals, connection recovery, result, cancellation, and errors through the existing run-event vocabulary.
- A resolved read-only destination opens immediately without a confirmation step.
- Actions use the selected tool's canonical `ToolPolicy`, including user overrides. The command surface never bypasses approvals.
- Ambiguous targets keep the peek open and present a short clarification instead of guessing.
- Failed or unsupported requests leave the current app location unchanged.
- `Escape` closes the peek without cancelling a running action; an explicit Stop control cancels the run.
- Command sessions are absent from the normal chat list and agent hub. Their runs and tool calls remain durably auditable.

## Tool architecture

The command runner uses the existing `ToolExecutor`, `ToolRegistry`, deferred-tool loading, integration recovery, approval middleware, audit ledger, and tool overrides. It does not create parallel automation, session, memory, or integration actions.

Add explicit command-surface eligibility metadata at the registry boundary. Eligibility is registered metadata, never inferred from the query. Initial eligibility covers in-app domain tools and configured native/MCP integrations; orchestration, shell, filesystem mutation, background spawning, and skill creation remain unavailable unless deliberately opted in later.

The server applies the eligible tool names through the existing hard `tool_scope` allowlist. Denied or unavailable tools remain filtered by the registry.

Automation requests therefore use `list_automations`, `update_automation`, `run_automation`, and their existing siblings. For example, pausing an automation still requests approval because `update_automation` currently requires approval. Any future immediate reversible pause behavior must be implemented in the canonical automation tool contract, not special-cased in the palette.

## Command run

Add `POST /command/runs` with:

- `query`
- current app destination/context
- a durable client id for idempotent retries

The endpoint provisions a `session_type="agent"`, `agent_type="command_sidecar"` session, starts the existing chat runner with the command tool scope and `CommandOutcome` output schema, and returns `run_id` plus `session_id`.

The desktop subscribes to that session's normal SSE stream. When the run completes, the server emits a command completion event containing the validated structured outcome. Structured-output failure becomes a safe command failure; the client never parses prose to navigate.

`CommandOutcome` contains:

- `status`: `completed | needs_input | unsupported | failed`
- concise `summary`
- optional `destination`
- optional clarification prompt and bounded choices

## Navigation contract

`AppDestination` is a closed discriminated union. Initial destinations are:

- Home
- chat session by `session_id`
- Settings, optionally by tab
- Automations, optionally by `task_id`
- Memory, optionally by artifact path
- Area by stable area key

Dynamic identifiers come from existing tool results. The desktop owns the final mapping from `AppDestination` to store actions; the model cannot provide component names, URLs, or arbitrary JavaScript.

Surface selection must be externally controlled. In particular, Automations moves its selected task ID from component-local state into its open/navigation contract so `openAutomations({ taskId })` reliably opens the requested detail.

## Desktop behavior

The side peek is a transient command surface, separate from the persistent background-agent hub. It reuses existing activity rows, approval cards, connection cards, status handling, and cancellation actions where possible.

Only one command peek is active. A new `Cmd/Ctrl+Enter` request replaces a settled peek; if a run is active, the user must stop it before starting another.

After a successful destination is applied, the peek collapses to a compact receipt with the summary and Close control. Navigation itself is not undoable; the app's existing Back/history behavior remains authoritative.

## Errors and safety

- Empty queries do nothing.
- Unknown or ambiguous resource names do not navigate or mutate.
- Approval, connection, timeout, uncertain mutation, and tool errors retain their existing typed behavior.
- A destination is applied only from schema-validated command output and only if its referenced resource still exists.
- Duplicate submissions reuse the durable client id and must not duplicate tool mutations.
- Closing or navigating away from the peek never implicitly approves or cancels work.

## Verification

- Server tests: command tool selection, capability/deny filtering, idempotent submission, structured outcomes, approval preservation, completion events, cancellation, ambiguity, and malformed destinations.
- Desktop tests: `Enter` remains deterministic, `Cmd/Ctrl+Enter` submission, peek lifecycle, event projection, approval flow, direct automation selection, unsupported/ambiguous behavior, and no navigation on invalid output.
- Integration test: `go to email automation` resolves through `list_automations` and opens that task; `pause email automation` calls the canonical update tool and respects its approval policy.
- Run focused server and desktop suites, type checking, build, and `git diff --check`.

## Out of scope

- Replacing the existing command palette search.
- GUI clicking or screenshot-based navigation.
- A second action registry.
- Automatic tool-policy bypasses or heuristic risk classification.
- Persisting command peeks in the normal chat/sidebar history.
