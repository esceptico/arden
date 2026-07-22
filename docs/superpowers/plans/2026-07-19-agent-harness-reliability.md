# Agent Harness Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Arden's current harness safe, restartable, idempotent, and evidence-bearing without replacing it.

**Architecture:** Fix known boundary bugs first. Then introduce one optional `ToolOutcome` carried by existing result/event/storage paths, generalize durable approval rows into suspensions, replay only provably safe work, and derive context/evidence sidecars from durable facts.

**Tech Stack:** Python 3.13, Pydantic, asyncio, FastAPI, SQLite/aiosqlite, React/TypeScript, pytest, Vitest.

## Global Constraints

- Keep abstractions simple and generic; do not add a parallel agent framework.
- Preserve Arden's provider adapters, loop, sessions, budgets, outbox, and SSE protocol.
- Write a failing regression test before each production change.
- Commit each task independently and stage only named paths.
- Do not touch unrelated desktop or memory worktree changes.
- Never retry a state-changing tool whose durable execution state is ambiguous.

---

### Task 1: Disable model-authored inline Python

**Files:**
- Modify: `apps/server/arden/orchestra/dynamic.py`
- Modify: `apps/server/arden/tools/workflow.py`
- Test: `apps/server/tests/test_dynamic_orchestra.py`

**Contract:** `build_dynamic_orchestra(...)` rejects any step with inline Python before compilation. Curated Python callables remain available through normal registered tools.

- [ ] Add a test proving inline code cannot execute or mutate process state.
- [ ] Run the test and confirm the current in-process `exec` path fails it.
- [ ] Reject inline script steps with a stable validation error and remove the runtime `exec` path.
- [ ] Run the focused orchestra/workflow tests.
- [ ] Commit: `fix: disable inline workflow python`.

### Task 2: Prevent child tool-scope widening

**Files:**
- Modify: `apps/server/arden/core/spawner.py`
- Test: `apps/server/tests/test_spawner.py`

**Contract:** Child-visible tools are `requested ∩ parent_allowed ∩ registry`; an unset child request still inherits the parent boundary.

- [ ] Add tests for restricted parents, explicit child requests, and nested children.
- [ ] Run them and observe the child currently sees registry-wide tools.
- [ ] Intersect before schema selection and enforce the same set at execution.
- [ ] Run spawner and child-agent tests.
- [ ] Commit: `fix: preserve tool scope in child runs`.

### Task 3: Separate display metadata from tool arguments

**Files:**
- Modify: `apps/server/arden/tools/core/base.py`
- Modify: `apps/server/arden/tools/core/registry.py`
- Modify: `apps/server/arden/events/sse.py`
- Test: `apps/server/tests/test_tools.py`
- Test: `apps/server/tests/test_event_contract.py`

**Contract:** Display labels use `_arden_display_title` outside validated user arguments. A real schema field named `title` reaches the tool unchanged.

- [ ] Add a tool with a required `title` and prove registry execution currently strips it.
- [ ] Replace the pseudo-argument with namespaced event/provider metadata.
- [ ] Reject collisions with the reserved namespaced key.
- [ ] Run tool and event-contract tests.
- [ ] Commit: `fix: isolate tool display metadata`.

### Task 4: Reject malformed tool arguments without execution

**Files:**
- Modify: `apps/server/arden/agent/llm/parsing.py`
- Modify: `apps/server/arden/agent/types/tool_call.py`
- Modify: `apps/server/arden/agent/tools/runner.py`
- Test: `apps/server/tests/test_agent_lib.py`
- Test: `apps/server/tests/test_tool_runner.py`

**Contract:** Invalid JSON becomes one `invalid_tool_arguments` result containing compact recovery guidance; the target tool is never called.

- [ ] Add parser/runner tests for truncated JSON, non-object JSON, and valid `{}`.
- [ ] Run and verify malformed JSON currently collapses to `{}`.
- [ ] Preserve a structured parse failure on `ToolCall` and short-circuit dispatch.
- [ ] Run agent and runner tests.
- [ ] Commit: `fix: reject malformed tool arguments`.

### Task 5: Contain background result paths

**Files:**
- Modify: `apps/server/arden/tools/background.py`
- Modify: `apps/server/arden/tools/core/context.py`
- Test: `apps/server/tests/test_background_tool.py`

**Contract:** A result can be read only for a task owned by the current run/session, and every resolved fallback path remains under `RESULT_BASE`.

- [ ] Add ownership and `../` traversal regression tests.
- [ ] Run and verify the filesystem fallback escapes the base.
- [ ] Validate task ownership first, then enforce `resolved.is_relative_to(base)`.
- [ ] Run background tests.
- [ ] Commit: `fix: contain background result paths`.

### Task 6: Add the generic terminal outcome contract

**Files:**
- Modify: `apps/server/arden/agent/types/tools.py`
- Modify: `apps/server/arden/agent/types/events.py`
- Modify: `apps/server/arden/agent/tools/runner.py`
- Modify: `apps/server/arden/core/tool_executor.py`
- Test: `apps/server/tests/test_tool_runner.py`
- Test: `apps/server/tests/test_tools.py`

**Produces:** `ToolOutcome`, `ToolError`, `ToolEffect`, and `ToolVerification`; `ToolResult.outcome`; harness failures with stable codes.

- [ ] Add serialization and failure-mapping tests for succeeded, failed, denied, and uncertain outcomes.
- [ ] Implement frozen Pydantic/dataclass value types with optional evidence fields.
- [ ] Map validation, permission, approval, timeout, cancellation, unknown-tool, and internal failures.
- [ ] Keep existing string content as the bounded model-facing projection.
- [ ] Run focused harness tests.
- [ ] Commit: `feat: add typed tool outcomes`.

### Task 7: Persist and project tool outcomes

**Files:**
- Modify: `apps/server/arden/context/store.py`
- Modify: `apps/server/arden/core/tool_executor.py`
- Modify: `apps/server/arden/events/sse.py`
- Modify: `apps/desktop/src/stores/chat-stream-types.ts`
- Modify: `apps/desktop/src/stores/transcript-projection.ts`
- Test: `apps/server/tests/test_session_store.py`
- Test: `apps/server/tests/test_event_contract.py`
- Test: `apps/desktop/tests/transcriptProjection.test.ts`

**Produces:** JSON-safe `outcome_json` keyed by `(run_id, tool_call_id)` and an optional `outcome` field on tool-result SSE events.

- [ ] Add store round-trip and SSE compatibility tests.
- [ ] Add a nullable outcome column/migration and persist exact terminal outcomes.
- [ ] Project the optional field through desktop state without changing existing rendering.
- [ ] Run server and desktop focused tests.
- [ ] Commit: `feat: carry tool outcomes end to end`.

### Task 8: Generalize approvals into durable suspensions

**Files:**
- Modify: `apps/server/arden/context/store.py`
- Modify: `apps/server/arden/tools/core/context.py`
- Modify: `apps/server/arden/server/routers/chat.py`
- Test: `apps/server/tests/test_session_store.py`
- Test: `apps/server/tests/test_chat_inject.py`

**Produces:** durable suspension rows with `kind`, typed payload/resolution, terminal-state idempotency, and approval compatibility wrappers.

- [ ] Add tests for create, duplicate create, offline resolve, replayed resolve, and terminal conflicts.
- [ ] Add the generic store API and migrate approval methods to wrappers.
- [ ] Consult durable resolution before allocating a live future.
- [ ] Keep `/tools/result` compatible while resolving the durable suspension.
- [ ] Run approval/store tests.
- [ ] Commit: `feat: persist typed run suspensions`.

### Task 9: Resume suspended foreground runs safely

**Files:**
- Modify: `apps/server/arden/services/chat.py`
- Modify: `apps/server/arden/agent/agent.py`
- Modify: `apps/server/arden/agent/llm/parsing.py`
- Modify: `apps/server/arden/server/state.py`
- Test: `apps/server/tests/test_streaming_events.py`
- Test: `apps/server/tests/test_agent_lib.py`
- Test: `apps/server/tests/test_chat_inject.py`

**Contract:** Checkpoint the assistant tool-call turn before dispatch. On recovery, reuse stored outcomes, execute only `created/awaiting` calls, and produce `uncertain` for `executing` calls.

- [ ] Add crash-boundary, partial parallel-step, completed-effect, and ambiguous-effect tests.
- [ ] Persist the tool-call checkpoint in the existing response hook.
- [ ] Parse the exact trailing incomplete tool step by tool-call ID.
- [ ] Restore the original run ID and consume durable suspension resolutions.
- [ ] Resume automatically after an offline approval resolution.
- [ ] Run chat/agent recovery tests.
- [ ] Commit: `feat: resume suspended chat runs`.

### Task 10: Make background completion exactly-once

**Files:**
- Modify: `apps/server/arden/context/store.py`
- Modify: `apps/server/arden/tools/core/context.py`
- Modify: `apps/server/arden/services/chat.py`
- Modify: `apps/server/arden/server/bus.py`
- Test: `apps/server/tests/test_session_store.py`
- Test: `apps/server/tests/test_streaming_events.py`

**Contract:** One transaction claims terminal completion and enqueues stable completion/parent-notification records. Repeated completion or delivery is harmless.

- [ ] Add crash-window and duplicate-completion journey tests.
- [ ] Add a unique completion operation ID and transactional outbox rows.
- [ ] Make delivery retryable and consumers idempotent by event ID.
- [ ] Remove filesystem output as a completion authority.
- [ ] Run background/event tests.
- [ ] Commit: `feat: deliver background completion exactly once`.

### Task 11: Persist context manifests and derive run evidence

**Files:**
- Modify: `apps/server/arden/core/content.py`
- Modify: `apps/server/arden/core/prompts.py`
- Modify: `apps/server/arden/context/store.py`
- Modify: `apps/server/arden/services/chat.py`
- Test: `apps/server/tests/test_prompts.py`
- Test: `apps/server/tests/test_session_store.py`

**Produces:** `ContextManifest` entries and derived `RunEvidence` containing sources, approvals, receipts, checks, and limitations.

- [ ] Add deterministic manifest and evidence-derivation tests.
- [ ] Preserve provenance when selecting/rendering each context block.
- [ ] Store one run-level manifest sidecar.
- [ ] Derive evidence only from persisted facts; never accept model-authored proof.
- [ ] Run prompt/store tests.
- [ ] Commit: `feat: persist run provenance and evidence`.

### Task 12: Add whole-journey harness evals

**Files:**
- Modify: `evals/__main__.py`
- Modify: `evals/run.py`
- Create: `evals/scenarios/harness_reliability.py`
- Test: `apps/server/tests/test_harness_journeys.py`

**Contract:** `python -m evals --help` works. Hermetic scenarios cover approved mutation, malformed repair, scope denial, restart/resume, background exactly-once, failed postcondition, and partial completion.

- [ ] Add a CLI smoke test that reproduces the current missing `main` failure.
- [ ] Repair the entrypoint without adding a second runner.
- [ ] Add deterministic fake-model/fake-tool journeys for all seven cases.
- [ ] Run the eval CLI and journey tests.
- [ ] Commit: `test: add harness reliability journeys`.

### Task 13: Review and final verification

**Files:** only files changed by Tasks 1-12.

- [ ] Review the full commit range for unsafe replay, weak idempotency, compatibility breaks, and unrelated changes.
- [ ] Fix each concrete finding in its owning commit area; commit review fixes separately if needed.
- [ ] Run server type/lint checks, the full server suite, affected desktop tests, and eval journeys.
- [ ] Inspect `git diff <design-commit>..HEAD` and prove every design requirement has authoritative coverage.
