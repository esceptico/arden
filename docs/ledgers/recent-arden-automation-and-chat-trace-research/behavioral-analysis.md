# Behavioral analysis

## Scope

Read-only follow-up over the same `2026-07-30` through `2026-08-03` UTC window. This pass distinguishes exact duplication from higher-cost behavioral amplification.

## Executive finding

Exact repeated calls are a secondary issue. The dominant waste is repeated model steps over large contexts, recurring discovery of stable tools, speculative fan-out, and payloads/concurrency without resource budgets.

## System automation

### Model/context amplification

**Fact.** 108 automation-associated chat runs produced 611 model responses and 870 tool calls. Terminal-run usage records total 27.64M tokens. The largest runs consumed roughly 0.8M–1.14M tokens and 14–18 model responses each.

| Automation | Aggregate observation |
| --- | --- |
| Email updates | 28 runs; 203 model responses; 195 tools; 7.93M tokens |
| Job Applications | 17 runs; 74 responses; 151 tools; 3.84M tokens |
| O-1A | 13 runs; 51 responses; 85 tools; 2.68M tokens |
| daily Coast digest | 4 runs; 60 responses; 67 tools; 2.63M tokens |

Eleven of 15 enabled automation sessions had last input at or above 50k tokens; Job Applications reached 86,758. Loop history trimming is message-count based and deliberately soft at tool boundaries (`apps/server/arden/services/chat.py:703-735`), so a bounded message tail can still be a large token tail.

**Inference.** Reusing a 40k–86k input over many model steps is a larger performance/cost multiplier than exact tool duplication.

### Recurring tool discovery

**Fact.** `tool_search` accounts for 131 calls, 15% of automation tool calls. Email updates repeated four stable tool selections 87 times across 28 runs. Loaded tools are run-local (`apps/server/arden/core/deferred_tools_middleware.py:37-54`) and are cleared on compaction (`apps/server/arden/core/factory.py:181`).

**Inference.** Recurring agents repay a predictable discovery turn on each fire even though their scoped capabilities are stable.

**Fact.** Multi-tool loading already exists below the model-facing contract: `load_tools(names=[...])` accepts up to 100 names, and `tool_search(query='select:a,b,c')` parses comma-separated exact names (`apps/server/arden/tools/deferred.py:323-336,389-397,518-534`). However, the native schema and prompt advertise singular `select:<tool_name>` (`apps/server/arden/tools/deferred.py:96-104,339-344,559-566`). Recent traces used multi-select in **0 of 212** `tool_search` calls.

**Decision.** Expose `names: list[str]` directly on `tool_search` and document one-call multi-preload. Keep loading run-local; do not add a versioned preloaded toolset.

### Exact repeats and stateful workflows

**Fact.** General automation runs had only nine excess identical calls within a run. Most repeated signatures were expected polling across separate scheduled runs.

**Correction.** Wiki Maintenance run `helpful-swallow-168e473a588c` did **not** repeat a completed result. It made 24 stateful `next` calls that each returned `Maintenance decision required`, then completed on call 25. The identical arguments advance changing server-side state, so generic argument deduplication would break this workflow.

**Conclusion.** Blanket deduplication would target the wrong layer. Retryability, workflow state, and cacheability need explicit contracts.

### Progress quality

**Fact.** Of 42 accepted area reports, 24 made durable graph progress and 18 did not. All 18 reported work remaining; 12 also raised no concrete ask. O-1A had 7 reports, 0 graph-progress changes, 7 with work remaining, and 4 with no ask. Quiet-streak cadence stretching exists (`apps/server/arden/areas/custodian.py:300`), but only after the costly runs occur.

**Inference.** A delta/progress gate should run before broad exploration, with cadence decay retained as a backstop.

## Human chats

### Speculative fan-out, not exact duplication

**Fact.** Exact duplicate root calls were minor: one repeated `list_files` in `lucky-crow` and one cross-run reread. The clearest waste was `sage-macaque-87f7070ba8f7`:

- 1,088.5 seconds and 66 model responses;
- 118 root invocations: 98 success, 18 `not_found`, 2 command failures;
- missing-path guesses continued after successful enumeration of the relevant roots;
- several guesses were emitted as parallel batches.

**Inference.** The missing contract is path discovery/resolution before a speculative batch, not a same-arguments cache.

**Why it happened.** The model was optimizing latency by guessing conventional repository paths in parallel. Arden encourages parallel exploration, accepts arbitrary path strings, and launches the whole step together. Successful directory listings return canonical paths, but missing paths return only `not_found`; the device executor does not return the nearest existing ancestor or candidates (`apps/desktop/electron/executor-tools.cjs:262-338,458-468`). Nothing makes an earlier listing a prerequisite for later guessed descendants.

**Tool-behavior proposal.** Extend the existing resource-observation ledger to record paths emitted by `file_list`, `file_find`, and `file_search_text`. Treat user-supplied paths as initially observed. After two misses beneath one observed root, return typed `discovery_required` and require listing/finding from the nearest observed ancestor. Every `not_found` should include the resolved path, nearest existing ancestor, and bounded sibling candidates. This is an explicit path-evidence contract, not a keyword heuristic. The 10-call step cap below is only a safety fuse; the observed bad batches were 3–6 calls and would still pass it.

### Foreground compaction stalls

**Fact.** Four job-chat compactions reduced 350–351 messages to 68–71 and lowered subsequent context, but blocked the run for 67.6–92.9 seconds each. Automatic events are absent from `chat_compactions`.

**Conclusion.** Compaction is effective but late and user-visible.

**Decision.** Leave compaction behavior as-is in this scope. Recording/latency improvements remain deferred, not solved.

### Child attribution obscures performance

**Fact.** Child token events carry `scope='tool'` and `child_run_id`, but are stored under the parent `run_id` (`apps/server/arden/core/spawner.py:711-721`). Tool-call aggregates show similar parent aggregation. Example: `pristine-groundhog` ended after 49 seconds, while 244 attributed provider events continued for another 13 minutes.

**Impact.** Current per-run totals cannot cleanly separate foreground latency/cost from detached child work. Parent aggregate and child identity should be separate dimensions, not overloaded identifiers.

### Parent↔child cascade is only partly durable

**Fact.** Arden already persists useful pieces: `background_agent_runs` stores parent run/tool and child session edges; awaited children use durable suspensions; completions have idempotent IDs and redelivery. But descendant cancellation is discovered through the in-memory `RunRegistry.cancel_subtree()` and only then mirrored to rows (`apps/server/arden/server/state.py:243-260`, `apps/server/arden/tools/background.py:45-59`). The trace also shows timed-out children settled while seven child tool invocations remained open until restart.

**Decision.** Make both directions durable over the existing primitives. Parent→child commands must traverse stored edges and carry one cause/idempotency generation. Child→parent terminal events must settle child state, open child calls, the parent waiting tool/suspension, and completion delivery exactly once. An outbox worker resumes either cascade after restart; in-memory cancellation is only the fast path.

## Harness/resource behavior

### No-progress and budget limits

**Fact.** Defaults allow 200 iterations, no tool-call ceiling, and no wall-time ceiling (`apps/server/arden/constants.py:37-39`). The loop checks hard budgets but has no typed no-progress rule (`apps/server/arden/agent/agent.py:195-286`). In `micro-ibex-04ff5da545c2`, four review steps succeeded, then a generated-region safety invariant failed. The run made 16 more calls: 14 opaque `internal_error` results and two `invalid_maintenance_decision` results.

**Root cause.** `GeneratedRegionConflictError('maintenance cannot change generated page content')` originates at `apps/server/arden/wiki/service.py:953-968`. `wiki_maintenance_review` catches only `WikiMaintenanceError` (`apps/server/arden/tools/wiki_maintenance.py:85-98`), so the conflict escapes. The review task remains terminally failed; later `next()` calls re-raise the same stored task exception (`apps/server/arden/wiki/maintenance/agent.py:40-45,86-92`). The generic runner hides it as non-retryable `internal_error` with the misleading recovery action “Check the target's current state before retrying.”

**Proposal.** Map this invariant to terminal `generated_region_conflict`, expose the workflow as failed/blocked on subsequent calls, and add `StopReason.NO_PROGRESS` after three consecutive steps containing only non-retryable failures. The model continued because the tool erased the permanent workflow state; prompt-only retry advice cannot fix that.

### Per-step fan-out and executor concurrency

**Fact.** Arden accepts every tool call in a model response and launches all of them in one `TaskGroup` (`apps/server/arden/agent/tools/runner.py:129-160`). Its existing `max_tool_calls` is a cumulative run-subtree budget, not a per-step limit (`apps/server/arden/agent/agent.py:369-412`). Across 1,851 recent tool-bearing model steps, batch sizes were 1–9; none exceeded 9.

**Comparison.** None of Letta Code, Letta, Hermes, Codex, or the Claude snapshot enforces a general per-response cardinality cap. They variously use unbounded `Promise.all`/`gather`, an 8-worker queue, read/write locks, or a legacy concurrency default of 10. All distinguish acceptance from execution concurrency incompletely. See [tool-fanout-comparison.md](tool-fanout-comparison.md).

**Decision.** Add `max_tool_calls_per_step=10`, distinct from cumulative `max_tool_calls`. Validate the full batch before any mutation. If exceeded, execute none and emit a typed result for every call ID so transcript parity is preserved; tell the model to regroup into batches of at most 10. Separately cap active execution (start at 6), serialize mutations/conflicting resources, and retain a whole-run budget for unattended automations. The recent trace indicates a limit of 10 would have changed no legitimate observed step.

### Search payload explosion

**Fact.** Desktop `searchText` caps match count but not per-line or total bytes (`apps/desktop/electron/executor-tools.cjs:521-618`). In the four-day window, 109 persisted `search_text` results represented 269.1 MiB raw; the largest was 52.6 MB. The server offloads only after receiving/building the payload (`apps/server/arden/core/tool_executor.py:395-431`).

**Proposal.** Enforce client-side per-match and serialized-byte budgets. Preserve path/line/column, set `has_more`, and direct the agent to a bounded file read.

### Raw-result retention cannot reclaim space

**Fact.** The live store has 908 raw-result manifests, 0.532 GiB raw / 0.153 GiB compressed; all have `expires_at=NULL`. `search_text` accounts for 471.1 MiB lifetime raw. Production persistence assigns `retention_class='session'` and no expiry (`apps/server/arden/context/store.py:3466-3487`); the storage budget only removes unreferenced blobs (`apps/server/arden/storage_budget.py:75-95`).

**Proposal.** Assign TTLs at persistence for transient search/fetch outputs. Promote only explicitly durable evidence. This is separate from payload bounding: one prevents future oversized work, the other makes retained data reclaimable.

## Prioritized behavioral changes

1. Typed Wiki Maintenance failure state and structured no-progress stop.
2. Ten-call per-step acceptance cap plus separate six-call execution concurrency.
3. Durable parent↔child cascade settlement using stored edges and idempotent events.
4. Direct multi-name `tool_search` preload; no versioned toolset.
5. Path-evidence/miss recovery and bounded `search_text` payloads.

Deferred by decision: model/context amplification and compaction changes.
