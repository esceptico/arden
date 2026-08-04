# Tool fan-out comparison

Read-only comparison of calls emitted in one model response, executor concurrency, and whole-run limits. Revisions were current local checkouts on 2026-08-04.

| Harness | Calls accepted per model response | Concurrent execution | Whole-run guard |
| --- | --- | --- | --- |
| Letta Code `bd06074` | no cap | unbounded `Promise.all` for parallel-safe calls; conflicting writes bucketed | optional model-turn cap only |
| Letta `b76da909` | no cap when parallel enabled; otherwise first call only | unbounded `asyncio.gather` for parallel-safe calls | 50 model steps by default |
| Hermes `cb06017` | no generic cap; `delegate_task` limited to 3 by default | 8 workers with an unbounded queue; 420s batch deadline | 500 model iterations per user turn; children get separate budgets |
| Codex `95637f7` | no cap | parallel-safe read lock vs unsafe write lock; no numeric semaphore | no default step/call/time budget; experimental shared token budget |
| Claude snapshot `4b9d30f` | no cap | legacy executor defaults to 10; streaming executor has no numeric cap | optional turns/USD/token budgets |

## Evidence

### Letta Code

- Full response retained: `/Users/escept1co/src/letta-code/src/agent/check-approval.ts:72`.
- Parallel-safe calls enter one `Promise.all`: `/Users/escept1co/src/letta-code/src/agent/approval-execution.ts:355,462-475`.
- Prompt says to maximize parallel calls: `/Users/escept1co/src/letta-code/src/agent/prompts/source_claude.md:92` and `source_codex.md:22`.

### Letta

- Default `max_steps=50`: `/Users/escept1co/src/letta/letta/constants.py:74-75`.
- Parallel calls gathered without a semaphore: `/Users/escept1co/src/letta/letta/agents/letta_agent_v3.py:1764-1896` and `/Users/escept1co/src/letta/letta/services/tool_executor/tool_execution_manager.py:94-160`.
- Responses schema has `max_tool_calls`, but the request builder does not populate it: `/Users/escept1co/src/letta/letta/schemas/openai/responses_request.py:20-45` and `/Users/escept1co/src/letta/letta/llm_api/openai_client.py:419-476`.

### Hermes

- Generic calls retained after exact duplicate removal: `/Users/escept1co/src/hermes-agent/run_agent.py:4210`.
- Eight-worker executor and submission queue: `/Users/escept1co/src/hermes-agent/agent/tool_executor.py:93,695`.
- Parallel-safe classification and path-conflict handling: `/Users/escept1co/src/hermes-agent/agent/tool_dispatch_helpers.py:41-105`.

### Codex

- Every completed call becomes an ordered future: `/Users/escept1co/src/codex/codex-rs/core/src/stream_events_utils.rs:295` and `/Users/escept1co/src/codex/codex-rs/core/src/session/turn.rs:2049,2185`.
- Shared/exclusive execution lock: `/Users/escept1co/src/codex/codex-rs/core/src/tools/parallel.rs:41,131`.
- Provider contract has a boolean, not a numeric maximum: `/Users/escept1co/src/codex/codex-rs/codex-api/src/common.rs:251`.

### Claude snapshot

- All streamed `tool_use` blocks are queued: `/Users/escept1co/src/claude-code-leaked/src/query.ts:826-844`.
- Legacy concurrency defaults to 10: `/Users/escept1co/src/claude-code-leaked/src/services/tools/toolOrchestration.ts:8-12,152-176`.
- Streaming executor starts all currently safe calls: `/Users/escept1co/src/claude-code-leaked/src/services/tools/StreamingToolExecutor.ts:74-150`.
- Prompt says to maximize independent parallel calls: `/Users/escept1co/src/claude-code-leaked/src/constants/prompts.ts:304-313`.

## Arden recommendation

Use independent controls:

1. Accept at most 10 calls from one model response.
2. Execute at most 6 concurrently, with lower provider/resource keys and serialized conflicts.
3. Preserve cumulative run/subtree budgets for calls, model steps, time, and tokens/cost.
4. On overflow, reject the whole batch before mutation and produce one typed result per call ID; never silently truncate.

The cap is a safety boundary, not the primary cure for speculative calls or no-progress loops.
