# Research

## Surface research

- Window: `2026-07-30T00:00:00Z` through live trace cutoff `2026-08-03T19:47Z`.
- Live sources: `/Users/escept1co/.arden/sessions.db`, `/Users/escept1co/.arden/logs/arden.log*`, compaction manifest, and current SQLite schema/triggers.
- Code: revision `4d97a120`; relevant automation, chat, outbox, wiki-maintenance, compaction, spawner, and invocation paths.
- Privacy: chat content was inspected only where needed to classify failures; findings retain IDs/mechanics, not personal prose.

## Consolidated findings

### P0 — behavioral amplification is larger than exact duplication

**Fact.** The behavioral follow-up found only nine excess identical calls within general automation runs and two minor exact repeats at the human-chat root. Larger measured costs were 611 model responses / 27.64M terminal-run tokens across automation-associated runs, 131 recurring automation `tool_search` calls, one 18-miss speculative path sequence, 68–93 second foreground compactions, and `search_text` payloads as large as 52.6 MB.

**Conclusion.** Do not lead with blanket call deduplication. Add typed progress/resource contracts and address discovery, context, payload, and attribution costs. Full evidence: [behavioral-analysis.md](behavioral-analysis.md).

### P0 — comparison confirms search must be bounded before model projection

**Fact.** Letta Code, Letta, Hermes Agent, Codex, and a leaked/reconstructed Claude Code snapshot all separate at least two of: scan/capture work, model-visible output, continuation, and overflow storage. Strong implementations use modes/paging plus global/per-turn projection caps. None justifies Arden constructing and indefinitely retaining a 52.6 MB search payload before its existing 50k offload.

**Conclusion.** Preserve Arden's typed tool and server backstop, but move the decisive bound to the desktop producer and add continuation/retention contracts. Full comparison: [search-tool-comparison.md](search-tool-comparison.md).

### P0 — terminal state is not one transaction

**Fact.** Two research children under `Discuss job applications` timed out after the configured 1800 seconds:

| Child session | Background task | Runtime | Durable result |
| --- | --- | ---: | --- |
| `20260720_094827_005::18cece44` | `agent-3a2283381e` | 1,833s | failed; partial salvage |
| `20260720_094827_005::550286f3` | `agent-4d45b7d692` | 1,864s | failed; partial salvage |

The salvage path correctly preserves partial work (`apps/server/arden/core/spawner.py:944-990`). However:

- child `sessions.agent_status` and `background_agent_runs.status` are `failed`;
- both child `RUN_FINISHED` events say `workflow_state=completed`;
- 7 child tool invocations remained running until restart, when they became `uncertain/server_restart` roughly 2.5–3 hours after the children failed;
- one cancelled parent run had the same leak: its `search_text` remained open for 36 minutes until restart.

**Inference.** Timeout/cancellation settles the agent surfaces but does not own executor invocation cancellation/settlement. The later restart reconciler then records the wrong cause (`apps/server/arden/execution/gateway.py:87-121`). Child session framing also maps failed completion onto a generic `RunFinishedEvent`; the child finisher emits `RUN_FINISHED` for every non-cancel terminal state (`apps/server/arden/services/chat.py:1768-1770`).

**Impact.** UI/history can disagree about success; running tools survive past their owner; incident attribution changes from timeout/cancel to restart.

### P1 — Wiki Maintenance turns expected conflicts into opaque retry churn

**Fact.** Wiki Maintenance had 35 completed and 5 failed runs. Failures:

| Run IDs | Error | Count |
| --- | --- | ---: |
| `1547` | duplicate title/alias validation | 1 |
| `1658` | ambiguous `job applications` identity | 1 |
| `1710`, `1712`, `1713` | generated-region conflict | 3 |

The three generated-region failures occurred within nine minutes. The same window produced repeated `wiki_maintenance_review` opaque tool errors; a later run `1714` completed.

**Cause.** The mutation invariant is correct (`apps/server/arden/wiki/service.py:949-967`). The review tool only converts `WikiMaintenanceError` to a typed result (`apps/server/arden/tools/wiki_maintenance.py:85-98`); expected wiki validation/ambiguity/generated-content errors escape. The generic tool runner converts them to `internal_error` + uncertain recovery (`apps/server/arden/agent/tools/runner.py:70-94`), hiding the deterministic cause and encouraging retries.

**Impact.** Safe rejection works, but the harness spends model/tool cycles, produces noisy logs, and gives the reviewer no precise recovery.

### P1 — permanent automation failures use the generic retry lane

**Fact.** On Aug 3, 22 area-agent automation runs failed from 13:32–13:59Z with exactly `OperationalError: table session_messages_fts has no column named file_search_text`. The failure hit Job Applications, Mech Interp, O-1A, and United States agents. Eight `run.failed` outbox events also retried once because the terminal event arrived before detached-run binding.

**Current state.** The live table and all three FTS triggers now use `search_text`; later area runs completed. Current startup repair checks/replaces the stale triggers in O(1) (`apps/server/arden/context/store.py:1314-1382`), backed by the regression at `apps/server/tests/test_transcript_search.py:284-328`. Commit `e5b33cc7` is the repair. This incident is resolved.

**Harness gap.** Retry classification does not distinguish:

- schema/invariant failures that require repair before fleet retry;
- expected ordering (`DetachedRunBindingPending`) that should retry quietly;
- transient provider/network errors.

Expected detached ordering is deliberately raised at `apps/server/arden/automation/scheduler.py:634-648`, but the generic worker logs every exception as an outbox handler error (`apps/server/arden/outbox/worker.py:162-187`).

### P1 — failure/cancellation attribution is too lossy

**Fact.** Other automation failures in the window included:

- one detached run after 3 hours: `run never reported back`;
- two `chat run cancelled` outcomes;
- two historic scope-resolution failures: `'str' object has no attribute 'matches'`;
- one Wiki Maintenance identity ambiguity.

Human-chat cancellation rows also store only `stop_reason=cancelled`. The cancel route and terminal writers persist no actor/source/reason distinction (`apps/server/arden/server/routers/chat.py:630-653`, `apps/server/arden/services/chat.py:1347-1366`).

**Impact.** Traces cannot distinguish user Stop, superseding message, shutdown cascade, automation cancellation, or timeout without reconstructing surrounding logs—and often cannot distinguish them at all.

### P2 — automation/outbox health mixes current and historical debt

**Fact.** The current window has 219 outbox events, all completed; 18 retried. The DB also has 47 `dead` events, all `run.completed` from May 20–27. Runtime health reports the all-time dead count (`apps/server/arden/server/runtime/outbox.py:176-188`), while pruning only covers completed events (`apps/server/arden/outbox/worker.py:123-154`, `apps/server/arden/outbox/store.py:613-619`).

**Impact.** Current health permanently looks degraded because of obsolete historical incompatibilities. Blind replay would be unsafe.

### P2 — automatic compaction is effective but incompletely inventoried

**Fact.** The active job chat has 1,257 persisted messages and 60 runs in the window (53 complete, 7 cancelled). It automatically compacted four times:

| Start | Before → after | Duration |
| --- | ---: | ---: |
| Aug 1 22:13Z | 351 → 68 | 93s |
| Aug 2 15:27Z | 350 → 71 | 90s |
| Aug 2 19:19Z | 350 → 71 | 74s |
| Aug 3 19:00Z | 351 → 71 | 68s |

The highest observed prompt was 125,158 tokens. The selected model advertises a 1,050,000-token context, so no context-limit failure is supported. The configured `max_messages=350` explains the cadence.

**Gap.** Automatic compactions emit `compaction_*` events but do not write `chat_compactions`; only the manual context route records that table (`apps/server/arden/server/routers/context.py:115-126`). A table-only audit therefore falsely reports no job-chat compaction.

### P2 — recent chat tool failures are mostly surface ergonomics

Human chat runs had no provider-level terminal errors. Tool errors clustered around:

- repeated browser/session launch variants;
- speculative missing paths/files;
- four invalid Coast CLI grammar attempts before recovery;
- one blocked destructive shell command (expected safety behavior).

**Proposal direction.** Prefer small typed adapters or help/capability cards for recurring shell-only integrations. Do not weaken the destructive-command gate.

## Automation inventory

All seven live builtins are enabled. Code defaults are not authoritative for existing enable state because seeding intentionally preserves the user's pause control (`apps/server/arden/automation/builtins.py:221-238`).

| Builtin | Completed | Failed | Average seconds |
| --- | ---: | ---: | ---: |
| Memory Capture | 6 | 0 | 31.6 |
| Memory Dream | 4 | 0 | 46.0 |
| Memory Maintenance | 4 | 0 | 5.4 |
| Memory Retention | 5 | 0 | 19.9 |
| Memory Storage Maintenance | 1 | 0 | 3.8 |
| Memory Synthesis | 23 | 0 | 15.4 |
| Wiki Maintenance | 35 | 5 | 38.0 |

## Recent human-chat inventory

| Session | Runs in window | Outcome |
| --- | ---: | --- |
| `Discuss job applications` | 60 | 53 complete, 7 cancelled; 2 child timeouts |
| `scratchpad` | 31 | 28 complete, 3 cancelled |
| `Model-Native Concepts Beyond J-Space` | 17 | 16 complete, 1 cancelled |
| `Local embedding models for Arden` | 4 | all complete |
| `Study frontier agent optimization techniques` | 2 | all complete |
| `Find ORIJEN Cat & Kitten Yerevan` | 1 | complete |
| `What you know about me` | 2 | all complete |

Twelve empty/ephemeral chat records were also created; most were archived. This is minor data/UI hygiene, not an agent failure.

## Negative evidence

- SQLite integrity check: `ok`.
- No running automation rows at cutoff.
- No current pending/running/dead outbox events; all 219 events from the trace window completed.
- No automation event queue/dead-letter rows.
- No provider-level human chat failure in the window.
- Current approval/suspension, raw-result persistence, bounded timeout salvage, durable spawn specs, and split outbox workers showed no new correctness failure in this trace.

## Conflicts and gaps

- Rotated logs have time-of-day but no date field; DB timestamps were used as the primary chronology.
- Generic `cancelled` records do not carry enough evidence to assign cause.
- Tool-error counts mix expected user/safety failures with harness faults; findings classify them separately.
