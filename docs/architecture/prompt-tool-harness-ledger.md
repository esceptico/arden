# Prompt and tool-harness ledger

This is the implementation ledger for Arden's prompts, built-in skills, tool
contracts, and agent-facing failures. It records the contract, every reviewed
prompt/skill family, decisions, changes, and proof.

Status:

- `verified`: matched the runtime contract; no change needed.
- `adjusted`: changed in this audit.
- `deferred`: real issue intentionally left for a later scoped change.

## Canonical contract

### Prompts and skills

1. State the role, task, authority boundary, available workflow, output, and
   completion condition directly.
2. Runtime tool names, argument names, paths, permissions, and defaults are the
   source of truth. Skills and docs must use those exact contracts.
3. Data from events, providers, tools, files, web pages, and wiki pages is
   evidence, never instructions.
4. `tool_scope` grants automation capabilities. `auto_approve` only skips
   approval inside that scope.
5. Autonomous runs use one shared system guard regardless of whether they are
   scheduled, posted to a channel, attached to a chat, or built in.
6. Structured-output prompts use a schema and backend validation. Prompt text
   alone never authorizes a mutation.

### Tools and errors

1. Search/list/read before mutation. Use stable semantic references and exact
   versions for follow-up calls.
2. Source-of-truth mutations expose approval, compare-and-swap or an
   idempotency contract, and the changed state or receipt.
3. Failures use `ToolResult.failure` with a stable code, safe message, retry
   classification, and a concrete `recovery_action` when the agent can act.
4. A timed-out mutation is `uncertain`; the agent verifies state before retry.
5. Raw provider/internal exceptions are logged server-side and never returned
   to the model. The tool-call ID is the diagnostic reference.
6. Model-visible results are bounded. Large exact results are stored and
   returned through a durable `raw_ref`/file continuation.
7. Tool descriptions explain when to call the tool, required discovery/read
   steps, important authority boundaries, and the next tool when applicable.

## Prompt inventory

| Prompt family | Location | Status | Decision |
|---|---|---:|---|
| Main system, context blocks, onboarding, research depths, init | `arden/core/prompts.py` | adjusted | Clarified that the `automations/` restriction applies only to wiki writes. Existing fact/wiki, deferred-tool, goal, and TODO contracts match runtime. |
| Reviewer, explorer, planner, verifier, builder personas | `arden/core/agent_types.py` | adjusted | Direct role, capability, output, and completion boundaries now share the external-data rule. |
| Autonomous run guard and event wrapper | `arden/automation/prompts.py` | adjusted | One guard now covers every autonomous run and explicitly treats event/provider/tool content as data. |
| Built-in Maintenance, Synthesis, Retention, Dream, Wiki Maintenance, storage | `arden/automation/builtins.py` | verified | Prompts match the registered handler/tool scopes; maintenance decisions remain backend validated. |
| Automation display-description generator | `arden/automation/descriptions.py` | adjusted | Display-only, bounded structured output; it cannot change execution instructions and treats the executable prompt as data. |
| Area custodian contract and intake | `arden/areas/agent.py` | verified | Explicit autonomy/tool scope and exactly one validated final report. |
| Area triage classifier | `arden/areas/triage.py` | adjusted | Constrained structured output; returned area IDs are checked against the live catalog and candidate content is data. |
| Conversation/research compaction | `arden/context/prompts.py` | adjusted | Direct handoff sections, provenance rules, bounded output, no mutation authority, and explicit data boundaries. |
| Session and child-agent naming | `arden/core/naming.py` | adjusted | Display-only structured output with bounded schema, deterministic fallback, and untrusted input handling. |
| Research-agent prompt and live evidence harness | `arden/tools/research.py` | adjusted | Read-only authority, bounded depth, evidence/gap tracking, artifact handoff, and external-data handling are explicit. |
| Background-agent prompt | `arden/tools/background.py` | adjusted | Read-only authority, self-contained output, automatic delivery, no polling, and external-data handling are explicit. |
| Workflow worker and formatter | `arden/orchestra/engine.py` | adjusted | Worker/formatter roles are separate; structured results are schema validated and embedded results remain data. |
| Goal proposal | `arden/server/routers/session.py` | adjusted | Preserves user scope, treats session text as data, and returns only goal text; no execution authority. |
| Dream insight renderer | `arden/memory/facts/completion_dream.py` | adjusted | Pinned facts only, provisional claims, exact tokens, strict schema, and untrusted fact text. |
| Fact synthesis renderer | `arden/memory/facts/completion_renderer.py` | adjusted | Pinned facts only, exact citations, Markdown only, untrusted fact text; synthesis validates output. |
| User wiki-edit curator | `arden/wiki/curation/completion.py` | adjusted | Page content is explicitly untrusted; decisions target opaque supplied tokens and are revalidated. |
| Budget pressure and forced finalization | `arden/agent/agent.py` | verified | Direct stop/finalize instructions mirror the enforced run budgets and disable further tools at exhaustion. |
| Child-agent salvage | `arden/core/spawner.py` | adjusted | Partial evidence is clamped and summarized without tools; raw exception text no longer reaches the model or parent. |
| Calendar/Slack event wrappers | `arden/events/triggers.py` | adjusted | Both wrappers now label provider-controlled fields as untrusted data before automation execution. |
| Goal continuation wrapper | `arden/services/goal_continuation.py` | verified | The objective remains the task to pursue but cannot override system authority; completion/block conditions match runtime. |
| Hidden background-result wrapper | `arden/tools/core/context.py` | adjusted | The parent must surface the result, while text inside the result is explicitly data rather than instructions. |
| Deferred-tool context | `arden/tools/deferred.py` | verified | Loader names, discovery sequence, tool groups, and approval labels are derived from the live registry. |

## Built-in skill inventory

| Skill | Status | Decision |
|---|---:|---|
| `add-model` | adjusted | Role models use `model_roles`/Settings; only supported direct environment selections are documented. |
| `add-skill` | adjusted | Six live discovery roots, 48-character name limit, and non-hard-coded paths now match the registry. |
| `add-tool` | adjusted | Current services and typed failure example now match the runtime harness. |
| `audit` | verified | Workflow name, arguments, and read-only review roles match the preset. |
| `implement` | verified | Workflow name, arguments, implementation, and verification phases match the preset. |
| `investigate` | verified | Workflow name, arguments, evidence, and verifier phases match the preset. |
| `loop` | adjusted | Composite intervals match trigger parsing; current-chat attachment remains explicit. |
| `mermaid` | adjusted | Uses the actual `read_file` tool name. |
| `panel` | adjusted | Empty questions and invalid panel sizes fail before spawning workers. |
| `propose-automation` | adjusted | Execution instructions use required `prompt`; `description` is display-only; authority wording matches `tool_scope`/`auto_approve`. |
| `propose-skill` | verified | `create_skill` arguments, approval, and reusable-content rules match runtime. |
| `wiki-automation` | adjusted | First-run missing directories are explicit; generic CAS wiki tools, shared `automations/`, dedicated channels, and loop split match runtime. |

## Tool-family audit

| Family | Status | Evidence/decision |
|---|---:|---|
| Registry, deferred discovery, capability gates | verified | Built-in names cannot be shadowed; deferred schemas use exact names; tool scope is the outer gate. |
| Approval and mutation policy | verified | Approval-gated tools require a preview callback; automation scope and approval bypass are independent. |
| Result bounds and continuation | verified | The executor caps complete serialized payloads at 50,000 bytes and offloads exact data with a stable continuation. |
| Argument validation | adjusted | Every regular input model now rejects unknown arguments centrally, including legacy models without strict config. |
| Outer exception boundary | adjusted | Uncaught exceptions now produce sanitized `internal_error` results with a diagnostic ref; raw errors remain server logs only. |
| Offloaded continuation | adjusted | Result offload now preserves scalar `next_*` and `has_more` fields, so pagination survives the real agent boundary. |
| Wiki read/write tools | adjusted | Listing gained offset continuation; common failures gained recovery actions. CAS, replay, Health protection, and automation path containment remain intact. |
| Fact/Wiki Maintenance tools | adjusted | Reserved/not-running failures now explain the valid calling context. |
| Area tools | adjusted | Runtime/storage/not-found/transient failures now give explicit recovery. |
| HTML input tool | adjusted | Missing interactive client is a typed actionable failure. |
| File tools | adjusted | Expected filesystem/search failures are typed and actionable without exposing raw OS or process errors. |
| Skill activation/creation tools | adjusted | Legacy untyped failures were replaced with stable codes and recovery guidance; raw conflict details are not exposed. |
| Automation tools | adjusted | Runtime description now states that `tool_scope` grants actions while `auto_approve` only skips approval inside that scope. |
| Integration adapters | verified | Provider operation errors are normalized through typed safe results; connection recovery stays explicit. |
| MCP tools | verified | Untrusted annotations cannot lower authority; schemas are preserved; provider failures are typed and sanitized. |

## Audit history

1. Inventoried every runtime prompt family, all 12 built-in skills, and the
   92-tool registry before changing contracts.
2. Centralized autonomous-run guarding on `automation_id`; removed
   caller-specific suffix wiring so built-ins, scheduled runs, channel runs,
   and loops cannot drift.
3. Reconciled skill and automation documentation against live schemas instead
   of removing supported idle, count, multi-trigger, or message behavior.
4. Applied strict argument rejection and actionable typed failures at shared
   boundaries; retained domain validation details but removed raw provider,
   process, filesystem, and child-agent exception text.
5. Adversarial review found and fixed four post-implementation gaps: offloaded
   pagination continuation, automation scope wording, supported REST triggers,
   and boolean/float panel sizes.
6. A final repository-wide scan found legacy skill-tool errors and child-agent
   salvage leakage; both now use the same safe failure contract.

## Deliberate compatibility

- `publish_wiki_generated` remains for existing producer-owned generated
  regions. Generic page mutation is the normal path; removing this compatibility
  tool before those pages are migrated would regress existing automations.
- Automation wiki writes share `automations/`. Per-automation ownership roots
  are deferred until isolation between independent automations is a product
  requirement.
- Provider reads rely on the executor's shared exact-result offload boundary
  instead of duplicating truncation logic in every integration.

## Verification

- `cd apps/server && uv run ruff check arden tests` — passed.
- `cd apps/server && uv run pytest` — **2,294 passed**.
- Harness reliability eval — **7/7 passed**: approval, malformed-argument
  repair, scope denial, restart/resume, exactly-once background completion,
  failed postconditions, and partial completion.
- Agent-ergonomics dry run — **9/9 cases discovered and validated** with
  expected tool sets and thresholds.
- Live registry smoke — **92 tools**, every description non-empty, every
  external state change approval-gated.
- `git diff --check` — passed.
- Adversarial regressions cover prompt injection through events, compaction,
  background results, and child salvage; unknown arguments; secret exception
  text; uncertain mutation completion; offloaded pagination; approval/scope
  separation; invalid workflow inputs; and ledger inventory completeness.
- No desktop/UI source was changed.

## 2026-07-30 — semantic directory contracts and page moves

The later user decision supersedes the earlier omission of nested README files.
Directory READMEs are semantic working contracts, never generated file lists.
The first managed child creates every missing ancestor contract in the same
wiki commit. Create, move, restore, generated publication, and approved rename
share this invariant. Active children protect their contract from archival,
and a fixed contract path cannot be moved or renamed.

Adjusted agent surfaces:

- `arden/core/prompts.py` — agents read an existing directory contract before
  its pages; `move_wiki_page` is the explicit path-only operation.
- `arden/automation/prompts.py` — every autonomous run reads the applicable
  README and explicitly reads each named wiki input.
- `skills/wiki-automation/SKILL.md` — documents first-write creation,
  producer/consumer/privacy/retention refinement, explicit downstream inputs,
  and optional move scope.
- Wiki tool descriptions — explain atomic README creation, exact CAS inputs,
  path-link rewriting, Area ownership, and actionable recovery.

README-read compliance remains a direct prompt/skill contract. A run-scoped
read gate was deliberately omitted: it cannot distinguish a newly created
output from an input, makes first-write creation circular, and adds mutable
harness state without improving trusted-agent behavior.

Verification:

- complete server suite: **2,300 passed**;
- Ruff and format checks: passed across all Arden and test Python files;
- wheel and source distribution: built; `wiki-automation`, wiki service, and
  move tool are packaged;
- adversarial review: create/move/restore/generated publication atomicity,
  collisions, Synthesis crash replay, automation scope, Area binding, and
  nested Coast creation passed;
- one-time live cutover rehearsal: exact replay made no changes; an injected
  database failure rolled back the database and a rerun completed from the
  already committed wiki state;
- no Desktop/UI source changed.

### Post-implementation hardening

- The root `README.md` remains the ordinary Home page. Only nested
  `*/README.md` paths are reserved directory contracts.
- Public create, move, rename, and generated-publication operations cannot
  claim a nested README path. The service alone creates or restores contracts.
- Exact link rewrites caused by page moves no longer invalidate a producer's
  generated-region history.
- Rename approvals use renewable, durable owner leases. A second live runtime
  cannot steal an active apply; an expired crash claim remains retryable.
- Approval-list responses retain stable recovery guidance without persisting
  backend exception text.

Verification:

- complete server suite: **2,323 passed**;
- focused README and approval adversarial suites: passed;
- Ruff, format, and `git diff --check`: passed;
- wheel and source distribution: built with the wiki service, approval
  coordinator, move tool, and `wiki-automation` skill;
- no Desktop/UI source changed.
