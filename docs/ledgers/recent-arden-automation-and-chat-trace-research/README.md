<!-- development-ledger:v2 -->

# Recent Arden automation and chat trace research

## Status

| Field | Value |
| --- | --- |
| State | complete |
| Active phase | adopted implementation slice and post-review hardening verified; explicit deferrals remain |
| Created | 2026-08-03T23:46:23+04:00 |
| Last updated | 2026-08-04T12:45:03+04:00 |
| Last consolidated | 2026-08-04T12:45:03+04:00 |
| Codebase branch | main |
| Codebase revision | 88570d0c0e1557f900b22a717bf2f380d595e2ed |
| Working tree | implementation present; not committed |
| Sources checked through | live traces: 2026-08-03T23:00Z; Arden: 4d97a120; five comparison repos: revisions recorded in comparison appendices |

## Original task — verbatim

so i wanted you to do pretty wide research on the current traces of
1. automations (especially system ones)
2. my latest chats with arden
for the last few days

what i need to look for:
- agent failures and its reasons
- which parts of harness might be improved / simplified / adjusted
- other major / mid issues and their potential fixes

use [$development-ledger](/Users/escept1co/.agents/skills/development-ledger/SKILL.md) for it. also you can spawn subagents (terra or luna if available) to delegate small research work to them

---

any questions?

## Amendments — verbatim

let's move from these clear errors to behavioral issues (too many redundant tool calls, bad performance, etc)

> search_text produced up to 52.6 MB per result; retained raw results now occupy 0.53 GiB with no expiry.

research how other harnesses (~/src/letta-code, ~/src/letta, ~/src/hermes-agent, ~/src/codex, ~/src/claude-code-leaked) implemented this tool, uses it, what's the prompt, etc
spawn a subagent per harness as well please

1. but why we had no progress there? which errors we had
2. i think we need to add an ability to preload multiple tools at a time. i don't want to add a versioned preloaded toolset
3. would be nice but not now. keep it as not solved (deferred) for now
4. why model did these missed calls? how we can fix it on terms of tool behavioral?
5. left compaction as is
6. i think we must have durable cascade mechanisms for child<->parent calls
7. we need to have some cap, like max 10 tools calls or something. need to do a research over other harnesses (please research the same harnesses i sent you when asked to check search tool)

start with file search fixes we discussed before (as you planned)
continue with other fixes right after
do not produce any defensive programming (getattr for example) or ad-hoc solutions, code must be scaleable and robust
use development-ledger

## Current synthesis

Window: **2026-07-30 through 2026-08-03 UTC**, using the live `~/.arden/sessions.db`, rotated Arden logs, and current source.

- System automation is broadly healthy: 7 enabled builtins produced 78 completed and 5 failed runs. All builtin failures were Wiki Maintenance safety/identity conflicts; the other six builtins had no failures.
- One transient Aug 3 schema regression caused 22 area-agent failures in 27 minutes. Current schema/startup repair is healthy and later area runs completed; treat this as resolved incident evidence, not an open failure.
- Two broad research subagents genuinely hit the 1800-second ceiling and salvaged partial results. Their terminal state is inconsistent across event/session/background tables, and their in-flight tools remained open until restart.
- Human chat model runs had no provider-level errors. Most completed; cancellations are recorded without actor/cause. The active job chat successfully auto-compacted four times, but those automatic compactions are absent from the durable `chat_compactions` inventory.
- Exact duplicate calls are not the dominant behavioral cost. Correction: `helpful-swallow` advanced 24 stateful maintenance reports and completed on call 25; it did not repeat a completed result.
- `micro-ibex` stopped making progress after a generated-region conflict escaped the maintenance adapter. Four calls succeeded; the next 16 were 14 opaque `internal_error` and 2 rejected decisions. The permanently failed workflow was exposed as retryable-looking generic state.
- Multi-tool loading is already supported internally, but the native prompt/schema advertise singular selection. Traces used multi-select 0/212 times. The fix is a direct `names` field and better prompt, not a persistent/versioned toolset.
- Speculative path misses came from latency-oriented parallel guesses plus weak `not_found` recovery. Add path-evidence state, nearest-ancestor/candidate results, and a typed discovery gate after repeated misses.
- Five-harness comparison confirms Arden's model-facing 50k offload is directionally sound but too late: other systems independently bound scan/capture work, page results, clamp snippets, enforce per-call/per-turn context limits, or expire overflow artifacts. See [search-tool-comparison.md](search-tool-comparison.md).
- The same five harnesses have no general per-response tool-call cap. A separate Arden limit of 10 calls/step is still supported: recent Arden batches maxed at 9, so the proposed cap changes no observed legitimate step. Acceptance, active concurrency, and whole-run budgets must remain separate.

## Decisions

- Live database state is authoritative over code defaults for enabled automation state.
- Resolved incidents and current open defects are reported separately.
- Adopt direct multi-name preload using current run-local loading; no versioned preloaded toolset.
- Leave compaction unchanged. Model/context amplification and delta-first gating remain deferred and unsolved.
- Require durable parent↔child cascade settlement from stored spawn edges and idempotent events; do not depend on the in-memory subtree alone.
- Adopt a 10-call per-model-step safety cap, separate from a recommended active concurrency of 6 and cumulative automation budgets.
- Implement the adopted slice directly on existing typed run/tool/storage contracts; do not add a versioned preloaded toolset.

## Implementation outcome

- File search now streams bounded ripgrep records, supports `content|files_only|count`, opaque cursors, exact serialized payload caps, and seven-day overflow retention.
- File operations carry run-scoped path evidence, actionable missing-path candidates, and a two-miss discovery gate.
- Tool batches are capped at 10 calls per step; active execution is capped at 6 with provider-group mutation exclusion; three terminal failure-only steps stop as `no_progress`.
- `load_tools` and `tool_search` preload multiple exact names in one run-local call.
- Wiki validation, ambiguity, and generated-region conflicts become deterministic non-retryable outcomes.
- Durable child cancellation traverses stored spawn edges with actor/cause/generation/idempotency. Awaited child completion and parent suspension settle atomically. Full timeout/supersede/automation/shutdown cause coverage remains open.
- Late descendants inherit an existing cancellation before execution, and restart reconciliation settles cancelled awaited parents without respawning the child.
- Search input and serialized response limits cover every branch; missing-path inspection is typed, and successful writes/edits emit exact discovery evidence.
- Internal concurrency now uses explicit filesystem/research/workflow conflict domains instead of one global `_system` group; unrelated internal tools can run in parallel.
- Compaction and model/context amplification remain unchanged and deferred.

## Open questions

- Whether to extend causal cascade attribution to every non-user terminal source now or in the broader retry/cancellation taxonomy work.

## Next action

Review and commit the verified working tree when desired. Keep compaction/model-context work deferred.

## Details

- [Research](research.md)
- [Behavioral analysis](behavioral-analysis.md)
- [Search-tool comparison](search-tool-comparison.md)
- [Tool fan-out comparison](tool-fanout-comparison.md)
- [Implementation proposals](implementation.md)
- [Verification](verification.md)
