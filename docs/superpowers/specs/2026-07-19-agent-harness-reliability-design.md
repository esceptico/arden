# Agent Harness Reliability Design

## Objective

Improve ntrp's existing harness without replacing its agent loop, provider adapters, persistence, sessions, budgets, outbox, or desktop protocol.

The result must make risky execution safe, failures legible, suspended runs restartable, background completion idempotent, and run claims traceable to durable evidence.

## Constraints

- Keep abstractions small and generic.
- Preserve current APIs unless a contract is unsafe or ambiguous.
- Do not execute model-authored Python in the server process.
- A child run may never gain tools its parent run could not use.
- Every provider tool call gets one typed terminal outcome.
- Never repeat an ambiguous or completed side effect during recovery.
- Durable state, not an in-memory future, is authoritative after restart.
- Do not mix unrelated desktop or memory worktree changes into commits.

## Architecture

### 1. Safe execution boundary

Fix the five known correctness and safety defects before adding recovery:

1. Disable inline Python workflow steps in normal runtime.
2. Intersect child tools with the parent run allowlist.
3. Move display-only tool-call metadata out of user argument names.
4. Turn malformed tool JSON into a typed non-executing failure.
5. Validate background result ownership and resolved-path containment.

These are independent commits with focused regression tests.

### 2. One terminal tool contract

Extend `ToolResult` with a compact `ToolOutcome`:

```text
status: succeeded | failed | denied | uncertain
error: code, retryable, recovery_action, diagnostic_ref
effect: operation, target, before_ref, after_ref
verification: postcondition, observed, confidence
receipt: stable operation receipt
```

The model receives bounded recovery-oriented text. Persistence, SSE, and the desktop receive the structured form. Existing tools can omit optional evidence; harness-generated failures always provide an error code.

### 3. Durable suspension

Replace approval-specific waiting semantics with a generic durable suspension record:

```text
run_id + suspension_id + kind + payload + status + resolution
```

Approval remains the first suspension kind. Live futures become notification helpers only. A resolved suspension can be consumed during re-entry without asking again.

The model response containing tool calls is checkpointed before dispatch. Recovery replays only calls whose durable phase proves execution never began. Completed calls use stored outcomes; ambiguous executing calls stop as `uncertain`.

### 4. Exactly-once background completion

Background terminal state, its completion event, and parent notification are committed as one durable transaction/outbox entry. Delivery may retry, but consumers deduplicate by stable event and operation IDs. Filesystem output is supplementary, never the authority.

### 5. Provenance and proof

Every run persists a `ContextManifest` sidecar for selected context blocks: source reference, freshness, selection reason, and size.

`RunEvidence` is derived from persisted tool outcomes, approvals, source references, receipts, and postcondition checks. The model does not author proof claims. UI expansion is deferred; the server contract ships now.

## Data flow

```text
provider call
  -> validate/authorize
  -> durable suspension or execution claim
  -> tool execution
  -> ToolOutcome
  -> persistence + SSE projection
  -> model-facing bounded result

restart
  -> load checkpoint + suspensions + outcomes
  -> reuse completed outcomes
  -> execute only never-started calls
  -> stop on ambiguous effects
```

## Verification

Each commit follows red-green-refactor. The final gate includes focused unit tests plus journey tests for malformed calls, scope denial, approved mutation, restart/resume, exactly-once background completion, failed postconditions, and partial completion.

## Deferred

- Wholesale replacement of provider message history with ai-python's IR.
- Generator tools and rich streaming snapshots.
- Telemetry dashboards.
- Full context inspector UI.
- A general distributed workflow engine.
