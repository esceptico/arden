# Tool Harness Audit Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix every confirmed tool-harness audit defect, from unsafe autonomous execution through minor naming and documentation drift, in independently verified commits on `main`.

**Architecture:** Make the existing typed policy/result contracts authoritative instead of adding a parallel harness. Capability selection is independent from approval bypass; mutations use stable refs, preconditions, idempotency, effects, receipts, and verification; all model-visible results are bounded, truthful, and recoverable. Native integrations remain canonical and MCP adapts into the same contracts.

**Tech Stack:** Python 3.13, Pydantic, asyncio, FastAPI, aiosqlite, MCP Python SDK, pytest; React/TypeScript only where approval or tool-result contracts require desktop projection.

## Global Constraints

- Work directly on `main`, as explicitly requested.
- Preserve the existing dirty worktree. Stage only named audit hunks and inspect `git diff --cached` before every commit.
- Follow strict RED → GREEN → REFACTOR. Every production behavior change starts with a failing regression test.
- Keep native Slack/Gmail/Calendar/Drive integrations canonical; MCP uses the same normalized contracts.
- Never use keyword or regex heuristics for tool selection, routing, or suggestions.
- Never retry an externally visible mutation unless a provider/ntrp idempotency key or verified no-op proves it safe.
- Canonical timestamps are ISO-8601 with an explicit offset.
- Every bounded collection returns `has_more` and a continuation token or explicit narrowing contract.

## Confirmed Finding Ledger

| Audit finding | Owning task |
|---|---|
| Auto-approved automation widens to all tools; Bash bypasses approval | 1 |
| Approval/audit persistence fails open; payload-less approvals | 2 |
| File, memory, directives, and Area write TOCTOU / non-atomic replacement | 3 |
| Gmail/Calendar/Notify/Bash/read misses report success; generic recovery drift | 4 |
| `forget` fuzzy-deletes before confirmation | 5 |
| MCP schema loss and incomplete annotations | 6 |
| MCP structured payloads bypass bounds; media/error projection drift | 7 |
| External writes lack idempotency, effect, receipt, and verification | 8 |
| Gmail/Calendar/Drive refs and Slack semantic lookup are not chain-safe | 9 |
| Missing cursors, bounds, ordering, and timezone-safe timestamps | 10 |
| Dead/overlapping/misclassified tools, hidden loader guidance, name shadowing | 11 |
| Research child provenance and source coverage are lost | 12 |
| Missing realistic workflows, email reply path, and high-signal renderers | 13 |
| No provider-backed agent ergonomics evals or harness metrics | 14 |

---

### Task 1: Least-Privilege Autonomous Execution

**Files:**
- Modify: `apps/server/ntrp/tools/core/types.py`
- Modify: `apps/server/ntrp/tools/core/context.py`
- Modify: `apps/server/ntrp/tools/bash.py`
- Modify: `apps/server/ntrp/operator/runner.py`
- Modify: `apps/server/ntrp/tools/automation.py`
- Test: `apps/server/tests/test_tools.py`
- Test: `apps/server/tests/test_operator_runner.py`
- Test: `apps/server/tests/test_loop_tools.py`

**Interfaces:**
- Produces: `ToolPolicy.allow_approval_bypass: bool = True`.
- Produces: `CreateAutomationInput.tool_scope: list[str] | None`.
- Contract: `auto_approve` changes approval flow only; it never widens the tool set.

- [x] Add failing tests proving an auto-approved run without `tool_scope` is read-only, explicit scope enables only matching tools, and Bash cannot bypass approval headlessly.
- [x] Run:
  `env PYTHONPATH=apps/server:. uv run --project apps/server pytest -q apps/server/tests/test_operator_runner.py apps/server/tests/test_tools.py apps/server/tests/test_loop_tools.py`
  Expected: failures showing unrestricted auto-approved tools, missing `tool_scope`, and Bash approval bypass.
- [x] Add the policy field and gate the early approval bypass:

```python
class ToolPolicy(BaseModel):
    # existing fields...
    allow_approval_bypass: bool = True

# ToolExecution.request_approval
tool = self.ctx.registry.get(self.tool_name)
bypass_allowed = tool is None or tool.policy.allow_approval_bypass
if bypass_allowed and not ask_must_block and (
    self.ctx.skip_approvals or self.tool_name in self.ctx.auto_approve
):
    return None
```

- [x] Set Bash `allow_approval_bypass=False`; update its description to state that non-read-only shell commands require an interactive approval and are unavailable headlessly.
- [x] Select tools independently from `auto_approve`:

```python
if request.extra_tool_names:
    tools = executor.get_tools(read_only=True, extra_names=request.extra_tool_names, **scope_kw)
elif request.tool_scope is not None:
    tools = executor.get_tools(scope=request.tool_scope)
else:
    tools = executor.get_tools(read_only=True)
```

- [x] Add `tool_scope` to the agent-facing automation input and pass it to `AutomationService.create`; show it in the approval preview.
- [x] Re-run focused tests, then `git diff --check`.
- [x] Commit: `fix(harness): enforce least-privilege autonomous tools`.

### Task 2: Approval Integrity and Payload Visibility

**Files:**
- Modify: `apps/server/ntrp/tools/core/function.py`
- Modify: `apps/server/ntrp/tools/core/context.py`
- Modify: `apps/server/ntrp/integrations/gmail/tools.py`
- Modify: `apps/server/ntrp/integrations/slack/tools.py`
- Modify: `apps/server/ntrp/tools/notify.py`
- Modify: `apps/server/ntrp/tools/automation.py`
- Modify: `apps/server/ntrp/tools/background.py`
- Test: `apps/server/tests/test_tools.py`
- Test: `apps/server/tests/test_loop_tools.py`
- Test: `apps/server/tests/test_integration_tool_sources.py`

**Interfaces:**
- Produces: explicit `approval=` handler requirement for approval-gated tools, with an intentional opt-out only for payload-free actions.
- Contract: approval persistence must succeed before a mutation may execute.

- [x] Add failing registration tests for approval-gated tools without handlers and approval rendering tests for complete email, Slack Block Kit, notification, wakeup, loop, and cancellation payloads.
- [x] Add failing tests proving record/resolve approval persistence errors prevent execution.
- [x] Implement registration validation and payload previews capped at 1,500 characters; render Block Kit as compact JSON/Markdown, not only a block count.
- [x] Replace best-effort approval record/resolve before mutation with typed `approval_persistence_failed` results carrying a diagnostic reference.
- [x] Run focused approval/integration tests and commit: `fix(harness): make approvals durable and inspectable`.

### Task 3: Approval-Bound Compare-and-Swap Writes

**Files:**
- Create: `apps/server/ntrp/tools/core/file_mutation.py`
- Modify: `apps/server/ntrp/tools/files.py`
- Modify: `apps/server/ntrp/tools/directives.py`
- Modify: `apps/server/ntrp/tools/area.py`
- Modify: `apps/server/ntrp/tools/memory.py`
- Test: `apps/server/tests/test_file_tools.py`
- Test: `apps/server/tests/test_area_tools.py`
- Test: `apps/server/tests/test_memory_filesystem_tools.py`
- Test: `apps/server/tests/test_tools.py`

**Interfaces:**
- Produces: `FileRevision(sha256: str, size: int)` and `atomic_compare_and_swap(path, content, expected_sha256)`.
- Contract: reads expose a revision; replacing existing content requires that revision; creation requires `expected_sha256="absent"`.

- [x] Add race tests: approve against revision A, mutate externally to B, execute, assert `write_conflict` and B remains untouched.
- [x] Add read-result tests for `sha256`; add atomic replacement tests preserving complete old/new files across failures.
- [x] Implement:

```python
@dataclass(frozen=True, slots=True)
class FileRevision:
    sha256: str
    size: int

def atomic_compare_and_swap(path: Path, content: str, expected_sha256: str) -> FileRevision:
    current = revision_or_absent(path)
    if current != expected_sha256:
        raise RevisionConflict(expected=expected_sha256, observed=current)
    # write a same-directory temporary file, fsync it, os.replace, fsync parent
    return file_revision(path)
```

- [x] Thread `expected_sha256` through write/edit/directives/Area/memory inputs and approval diffs; return before/after revisions in `ToolOutcome.effect`.
- [x] Run focused tests. Because `memory.py` is already dirty, stage only audit hunks with `git add -p` and inspect the cached diff.
- [x] Commit: `fix(harness): bind write approvals to file revisions`.

### Task 4: Truthful Typed Failures

**Files:**
- Modify: `apps/server/ntrp/agent/types/tools.py`
- Modify: `apps/server/ntrp/tools/bash.py`
- Modify: `apps/server/ntrp/tools/files.py`
- Modify: `apps/server/ntrp/tools/notify.py`
- Modify: `apps/server/ntrp/integrations/gmail/client.py`
- Modify: `apps/server/ntrp/integrations/gmail/tools.py`
- Modify: `apps/server/ntrp/integrations/calendar/client.py`
- Modify: `apps/server/ntrp/integrations/calendar/tools.py`
- Modify: `apps/server/ntrp/integrations/slack/tools.py`
- Test: `apps/server/tests/test_tool_outcome_contracts.py`
- Test: `apps/server/tests/test_tools.py`
- Test: `apps/server/tests/test_file_tools.py`
- Test: `apps/server/tests/test_calendar_multi_account.py`
- Test: `apps/server/tests/test_integration_tool_sources.py`

**Interfaces:**
- Produces stable codes: `not_found`, `permission_denied`, `provider_error`, `rate_limited`, `timed_out`, `partial_failure`, `invalid_ref`.
- Contract: `ToolResult` cannot contain `is_error=False` with a failed outcome or `is_error=True` with a succeeded outcome.

- [ ] Add failing tests for Bash nonzero/timeout, read misses, zero-notifier delivery, and Gmail/Calendar provider exceptions.
- [ ] Add a constructor invariant and use `ToolResult.failure(...)` for all blocking failures.
- [ ] Replace integration clients' `"Error ..."` string returns with typed exceptions; map only domain-safe messages at tool boundaries. Do not expose arbitrary provider exceptions.
- [ ] Add shared nearest-ref/call-this-first recovery helpers and convert bare `ToolResult(is_error=True)` call sites.
- [ ] Run focused tests and commit: `fix(harness): make tool outcomes truthful`.

### Task 5: Stable-Reference Memory Deletion

**Files:**
- Modify: `apps/server/ntrp/tools/memory.py`
- Test: `apps/server/tests/test_memory_remember.py`

**Interfaces:**
- Replaces: `forget(query)` mutation.
- Produces: `search_memory_candidates(query, limit)` output with stable `memory_ref` and revision; `forget(memory_ref, expected_version)` approval-gated mutation.

- [ ] Add failing tests proving ambiguous text never deletes and stale versions conflict.
- [ ] Split search from delete, add exact preview/undo metadata, and remove fuzzy mutation.
- [ ] Run memory tests, stage only audit hunks, and commit: `fix(memory): delete records by stable revisioned ref`.

### Task 6: Lossless MCP Schemas and Risk Metadata

**Files:**
- Modify: `apps/server/ntrp/mcp/tool.py`
- Modify: `apps/server/ntrp/tools/core/types.py`
- Modify: `apps/server/ntrp/tools/core/base.py`
- Test: `apps/server/tests/test_mcp_tool.py`
- Test: `apps/server/tests/test_tools.py`

**Interfaces:**
- Produces complete provider-compatible JSON Schema with resolved local refs and preserved constraints.
- Extends `ToolPolicy` with destructive/open-world/idempotent annotations and exposes them in metadata.

- [ ] Add failing schema tests containing `$defs`, nested `$ref`, `oneOf`, bounds, and `additionalProperties: false`.
- [ ] Reuse one shared schema normalizer for native and MCP tools; never reconstruct schemas from only `properties` and `required`.
- [ ] Reject contradictory trusted MCP annotations instead of letting `readOnlyHint` override `destructiveHint`.
- [ ] Run tests and commit: `fix(mcp): preserve schemas and risk annotations`.

### Task 7: Bounded MCP and Non-Offloaded Results

**Files:**
- Modify: `apps/server/ntrp/mcp/results.py`
- Modify: `apps/server/ntrp/mcp/tool.py`
- Modify: `apps/server/ntrp/core/tool_executor.py`
- Modify: `apps/server/ntrp/core/model_context_budget.py`
- Test: `apps/server/tests/test_mcp_results.py`
- Test: `apps/server/tests/test_mcp_tool.py`
- Test: `apps/server/tests/test_tools.py`

**Interfaces:**
- Produces: bounded `data`, `truncated`, `raw_ref`, and optional `next_cursor`.
- Contract: result budgets cover content, model content, metadata, and structured data together.

- [ ] Add failing 100k structured-data and media tests; assert emitted event size stays bounded and raw data remains retrievable.
- [ ] Store full structured/raw payloads durably; emit only an allowlisted summary and stable raw ref.
- [ ] Ensure `offload=False` over-budget results retain a retrieval pointer or are rejected before context loss.
- [ ] Route MCP exceptions through the typed MCP error formatter and map image/audio blocks to `model_content`.
- [ ] Run tests and commit: `fix(harness): bound and preserve tool result payloads`.

### Task 8: Retry-Safe Mutation Outcomes

**Files:**
- Modify: `apps/server/ntrp/integrations/gmail/tools.py`
- Modify: `apps/server/ntrp/integrations/calendar/tools.py`
- Modify: `apps/server/ntrp/integrations/slack/tools.py`
- Modify: `apps/server/ntrp/integrations/google_drive/tools.py`
- Modify: `apps/server/ntrp/core/tool_executor.py`
- Test: `apps/server/tests/test_integration_mutations.py`
- Test: `apps/server/tests/test_google_drive_tools.py`
- Test: `apps/server/tests/test_tool_executor_reads.py`

**Interfaces:**
- Mutation inputs accept `idempotency_key`; outputs populate `ToolEffect`, `ToolVerification`, and provider/ntrp receipt.
- Contract: an uncertain timeout instructs verification and never silently retries.

- [ ] Add failing duplicate-call and timeout-ambiguity tests for each external mutation family.
- [ ] Add a shared idempotency ledger keyed by integration, operation, account, and idempotency key; persist before/after provider receipts.
- [ ] Return changed state/diff. Gmail verifies by message ID, Calendar by event ref, Slack by `(channel, ts)`, and Drive by qualified file ref; if a provider returns no readable ref, return `UNCERTAIN` with its receipt.
- [ ] Run tests and commit: `feat(harness): make external mutations retry-safe`.

### Task 9: Chain-Safe Integration References

**Files:**
- Modify: `apps/server/ntrp/integrations/gmail/client.py`
- Modify: `apps/server/ntrp/integrations/gmail/tools.py`
- Modify: `apps/server/ntrp/integrations/calendar/client.py`
- Modify: `apps/server/ntrp/integrations/calendar/tools.py`
- Modify: `apps/server/ntrp/integrations/google_drive/client.py`
- Modify: `apps/server/ntrp/integrations/google_drive/tools.py`
- Modify: `apps/server/ntrp/integrations/slack/client.py`
- Modify: `apps/server/ntrp/integrations/slack/tools.py`
- Test: `apps/server/tests/test_integration_refs.py`
- Test: `apps/server/tests/test_calendar_multi_account.py`
- Test: `apps/server/tests/test_google_drive_tools.py`

**Interfaces:**
- Canonical refs are account-qualified and are exactly the strings sibling tools accept.
- Slack semantic user resolution paginates all candidates and returns ambiguity rather than choosing silently.

- [ ] Add failing create→read/update and multi-account collision tests.
- [ ] Print and return qualified refs from Gmail/Calendar/Drive create/search/read results; stop probing accounts in configuration order.
- [ ] Return Drive creation refs, preserve globally stable sorting, and paginate Slack user lookup beyond five users.
- [ ] Run tests and commit: `fix(integrations): return chain-safe semantic refs`.

### Task 10: Common Pagination, Ordering, and Time Contracts

**Files:**
- Create: `apps/server/ntrp/tools/core/collections.py`
- Modify: `apps/server/ntrp/tools/files.py`
- Modify: `apps/server/ntrp/tools/automation.py`
- Modify: `apps/server/ntrp/tools/sessions.py`
- Modify: `apps/server/ntrp/tools/time.py`
- Modify: `apps/server/ntrp/integrations/slack/tools.py`
- Modify: `apps/server/ntrp/integrations/gmail/tools.py`
- Modify: `apps/server/ntrp/integrations/calendar/tools.py`
- Modify: `apps/server/ntrp/integrations/google_drive/tools.py`
- Modify: `apps/server/ntrp/tools/workflow.py`
- Test: `apps/server/tests/test_tool_collections.py`
- Test: `apps/server/tests/test_file_tools.py`
- Test: `apps/server/tests/test_session_tools.py`
- Test: `apps/server/tests/test_loop_tools.py`

**Interfaces:**
- Produces: `Page[T](items, total, has_more, next_cursor)` with deterministic order.
- Produces: `format_timestamp(datetime) -> ISO-8601-with-offset`.

- [ ] Add failing tests for continuation, bounds, deterministic ordering, invalid enum rejection, and timezone output.
- [ ] Replace silent slices and prose-only truncation with cursor envelopes; constrain all limits and enum-like parameters in Pydantic schemas.
- [ ] Sort explicit lists before rendering and convert every canonical timestamp to offset-bearing ISO-8601.
- [ ] Replace raw sheet/workflow JSON dumps with compact high-signal tables/summaries.
- [ ] Run tests and commit: `fix(harness): standardize pages ordering and time`.

### Task 11: Clear Tool Ownership and Honest Policies

**Files:**
- Modify: `apps/server/ntrp/tools/discover.py`
- Modify: `apps/server/ntrp/core/spawner.py`
- Modify: `apps/server/ntrp/tools/background.py`
- Modify: `apps/server/ntrp/tools/research_artifacts.py`
- Modify: `apps/server/ntrp/tools/research.py`
- Modify: `apps/server/ntrp/tools/workflow.py`
- Modify: `apps/server/ntrp/tools/memory.py`
- Modify: `apps/server/ntrp/tools/directives.py`
- Modify: `apps/server/ntrp/tools/deferred.py`
- Modify: `apps/server/ntrp/core/deferred_tools_middleware.py`
- Modify: `apps/server/ntrp/integrations/core.py`
- Delete: `apps/server/ntrp/integrations/obsidian/__pycache__/`
- Test: `apps/server/tests/test_tool_registry_ergonomics.py`
- Test: `apps/server/tests/test_deferred_tools.py`
- Test: `apps/server/tests/test_research_ledger.py`
- Test: `apps/server/tests/test_background_tools.py`

**Interfaces:**
- One tool owns each intent; reserved built-in and child-only names cannot be shadowed.
- Stateful spawns and artifact writes are not classified READ.

- [ ] Add failing tests for user-tool shadowing, READ-filter leakage, dead registered tools, and hidden-loader recovery guidance.
- [ ] Remove `memory_rebuild`, `save_workflow`, and rejected `workflow.script` from the agent schema. Keep `run_workflow(name=...)` for curated built-ins; delete unused compatibility registrations after `rg` proves no Python caller imports them.
- [ ] Add `get_directives`; consolidate research claim/source and dead-end/gap recorders behind one canonical tool each.
- [ ] Classify background/research writes by actual effect; align deferred recovery with the loader actually exposed.
- [ ] Add one shared spawn-surface decision block and canonical parameter naming (`task_id`, `limit`, singular `kind`).
- [ ] Remove stale Obsidian bytecode and incorrect tool comments.
- [ ] Run tests and commit: `refactor(harness): clarify tool ownership and policy`.

### Task 12: End-to-End Provenance

**Files:**
- Modify: `apps/server/ntrp/core/spawner.py`
- Modify: `apps/server/ntrp/tools/research.py`
- Modify: `apps/server/ntrp/core/tool_result_data.py`
- Modify: `apps/server/ntrp/integrations/slack/tools.py`
- Modify: `apps/server/ntrp/tools/memory.py`
- Modify: `apps/server/ntrp/tools/sessions.py`
- Test: `apps/server/tests/test_research_provenance.py`
- Test: `apps/server/tests/test_tool_sources.py`
- Test: `apps/server/tests/test_session_store.py`

**Interfaces:**
- `SpawnResult` carries normalized child source refs and child tool-call IDs.
- Derived results persist query/window/derivation and a durable workspace/raw ref.

- [ ] Add failing parent-child provenance and persistence round-trip tests.
- [ ] Propagate normalized refs across the child boundary; persist research workspace/artifacts by durable ref.
- [ ] Add source refs where stable provider/session refs already exist; never synthesize unverifiable provenance.
- [ ] Run tests and commit: `feat(harness): preserve provenance across tool chains`.

### Task 13: Complete Common Workflows and Documentation

**Files:**
- Modify: `apps/server/ntrp/integrations/gmail/client.py`
- Modify: `apps/server/ntrp/integrations/gmail/tools.py`
- Modify: `docs/guides/tools.mdx`
- Modify: `apps/server/skills/add-tool/SKILL.md`
- Test: `apps/server/tests/test_gmail_reply.py`
- Test: `apps/server/tests/test_tool_documentation.py`

**Interfaces:**
- Produces: `reply_email(message_ref, body, idempotency_key)` with Gmail thread headers/thread ID.
- Documents canonical search → inspect → preview → mutate → verify workflows.

- [ ] Add failing reply/threading tests.
- [ ] Implement reply by qualified message ref with `In-Reply-To`, `References`, and provider `threadId`.
- [ ] Replace toy snippets with bounded file, Slack/email, and MCP workflows including errors, pagination, approvals, stable refs, receipts, and verification.
- [ ] Update the add-tool scaffold to require typed failures, bounds, source refs, idempotency, and mutation outcomes.
- [ ] Run tests and commit: `feat(harness): complete and document common workflows`.

### Task 14: Provider-Backed Agent Ergonomics Evals

**Files:**
- Modify: `evals/run.py`
- Modify: `evals/assertions.py`
- Modify: `evals/metrics.py`
- Modify: `evals/cases/basic.eval.py`
- Modify: `evals/cases/approval_wait.eval.py`
- Modify: `evals/cases/load_tools.eval.py`
- Modify: `evals/cases/schedule_dispatch.eval.py`
- Create: `evals/cases/file_mutation.eval.py`
- Create: `evals/cases/external_mutation.eval.py`
- Create: `evals/cases/mcp_recovery.eval.py`
- Test: `apps/server/tests/test_event_aware_evals.py`
- Test: `apps/server/tests/test_harness_journeys.py`
- Test: `apps/server/tests/test_agent_ergonomics_evals.py`

**Interfaces:**
- Discovers case files and records tool calls, wrong-tool calls, errors, retries, token use, truncation/offload, latency, and recovery.
- Keeps hermetic scripted journeys as runtime regression tests; adds an opt-in provider-backed lane for ergonomics.

- [ ] Add failing discovery and metrics tests.
- [ ] Implement executable case discovery and trace-derived metrics with deterministic thresholds.
- [ ] Add file, Slack/email, automation, memory, research, and MCP end-to-end tasks.
- [ ] Run hermetic tests. Run the provider-backed smoke lane when configured; otherwise verify its `--dry-run` discovery/contract output without fabricating a provider result. Commit: `test(harness): add agent ergonomics evaluations`.

### Task 15: Completion Audit and Full Verification

**Files:** only files changed by Tasks 1–14.

- [ ] Re-read this ledger and map every row to a passing test, code path, and commit.
- [ ] Search for remaining stringly errors, bare mutation successes, unbounded list inputs, naive timestamp formatting, approval-gated tools without previews, registered dead tools, and production mutations without outcomes.
- [ ] Run Ruff/type checks, the full server suite, harness journeys, provider-backed eval smoke, and affected desktop tests.
- [ ] Inspect `git status`, every commit diff, and `git diff --cached --check`; prove unrelated dirty changes were never staged.
- [ ] Commit any review-only corrections as `fix(harness): close remediation audit gaps`.
