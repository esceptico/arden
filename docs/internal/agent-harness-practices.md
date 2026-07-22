# Agent Harness Architecture Audit

Review lens: full `agents-best-practices` skill and references, applied to current `arden`.

Source: https://github.com/DenisSergeevitch/agents-best-practices

## Verdict

`arden` already has the right base shape: a manual model-tool-observation loop, typed tool middleware, approval UI path, deferred tool loading, durable session/run/event tables, background-agent run records, memory access logging, compaction, MCP integration, and provider adapters.

The main gap is not architecture direction. It is making policy, budgets, rehydration, and eval coverage more mechanical. Several important controls exist as prompt guidance, booleans, or in-memory state rather than typed durable harness state.

## Strong Parts

- `apps/server/arden/agent/agent.py` owns the provider-neutral model/tool loop.
- `apps/server/arden/tools/core/middleware.py` validates tool args and routes approvals before execution.
- `apps/server/arden/tools/core/context.py` routes approval responses by `tool_id`, so parallel mutating calls do not share one response queue.
- `apps/server/arden/tools/deferred.py` implements progressive disclosure for Gmail, calendar, Slack, automations, notifications, directives, file writes, background agents, and MCP servers.
- `apps/server/arden/context/store.py` has durable `chat_runs`, `chat_queued_messages`, `session_events`, `chat_compactions`, and `background_agent_*` tables.
- `apps/server/arden/services/chat.py` checkpoints after agent steps and advances replay watermarks through `SessionBus`.
- `apps/server/arden/core/tool_executor.py` offloads large tool results to temp files and returns compact refs.
- `apps/server/arden/core/prompts.py` separates static and dynamic prompt blocks and uses provider-specific cache controls where supported.
- `apps/server/arden/llm/openai.py`, `apps/server/arden/llm/openai_responses.py`, and `apps/server/arden/llm/anthropic.py` normalize cache-read token usage into `Usage`.
- `apps/server/arden/mcp/manager.py` and `apps/server/arden/mcp/tool.py` namespace MCP tools by server.

## Gaps

### P0: Runtime Safety And Recoverability

1. Budgets are incomplete.
   `Agent` supports `max_iterations`, but `create_agent(...)` does not wire a configured iteration budget. Constants for research, subagent, background-agent, consolidation, and compaction timeouts are `None`. Cost and wall-time budgets are observed, not enforced.

2. Tool policy is implicit.
   Tool behavior is spread across `mutates`, `volatile`, `requires`, `approval_info`, `skip_approvals`, `auto_approve`, and deferred visibility. There is no single `risk_class`, side-effect class, timeout, retry policy, result limit, or audit policy field.

3. MCP risk is too coarse.
   `MCPTool.mutates = True` is conservative, but every MCP tool uses raw remote schema/description and no local risk class. The harness cannot distinguish read-only connector calls from external writes except by blanket mutability.

4. Approval waits are in-memory and unbounded.
   `ToolExecution.request_approval(...)` waits on an in-memory Future. Pending approval state is not durable and has no timeout path.

5. Cancelled parallel tool calls lose cause.
   `dispatch_tools._append_results(...)` fills missing results with generic `"Error: tool execution failed"`, hiding whether the cause was cancellation, timeout, approval denial, or crash.

### P1: Context, Planning, And Legibility

6. Compaction is mostly conversation summarization.
   It records message boundaries, but active approvals, active plan/goal, artifact refs, and unresolved background/tool state are not explicit rehydration inputs. Deferred tool visibility should reset after compaction; the model can reload groups if they are still needed.

7. Planning mode is not a runtime mode.
   There are plan artifacts in `docs/superpowers/plans`, but no harness mode that blocks mutating tools while planning, ties approval to a plan version, or records a durable plan state.

8. Prompt-cache telemetry is partial.
   Cache-read tokens are captured, but prompt/tool bundle hashes, system prompt hash, tool-list hash, cache hit rate, and cache fragmentation are not recorded as first-class telemetry.

9. Skills are progressively visible, but governance is light.
   Skill discovery and `use_skill` exist, and `create_skill` requires approval. Missing pieces: stricter skill metadata validation, directory/name consistency, source/version metadata, activation evals, and stale-skill cleanup.

10. Agent-legible source-of-truth artifacts are not indexed as a system.
    Internal docs, plans, memory docs, and architecture notes exist, but there is no durable index/quality scorecard/freshness metadata that the agent can query as a source-of-truth map.

### P2: Evaluation And Operations

11. Evals are mostly unit tests, not harness scenarios.
    There are focused tests for tools, compaction, events, sessions, MCP, background agents, and deferred tools. Missing adversarial scenario tests: prompt injection in retrieved content, approval bypass, unknown tools, malformed tool args, connector auth expiry during a run, context overflow, huge tool output, and false success claims.

12. Connector call audit is not first-class.
    MCP connection errors are handled, and session events exist, but connector calls do not all produce durable audit rows with risk class, args hash, result summary, and approval decision.

13. Entropy cleanup is not scheduled for harness artifacts.
    There are memory maintenance automations, but no recurring scan for stale skills, stale plans, obsolete tools, repeated tool failures, or weak examples that future agents may imitate.

## Recommended Plan

1. Add tool risk/policy metadata and keep `mutates` as compatibility.
2. Use that metadata for MCP risk mapping and connector audit.
3. Add approval timeout plus durable pending-approval records.
4. Add structured abort/timeout/cancel tool results.
5. Wire real run budgets: max iterations, wall time, tool calls, and cost.
6. Define compaction rehydration metadata and preserve active control-plane state.
7. Add runtime planning mode after the policy/budget layer is stable.
8. Add cache and prompt-bundle telemetry.
9. Add harness scenario evals.
10. Add skill/connector governance and recurring cleanup.

## Non-Goals

- Do not rewrite the agent loop.
- Do not add a generic policy engine yet.
- Do not add multi-agent orchestration because the reference mentions it.
- Do not load all skills/connectors by default.
- Do not rely on prompt text for permissions that code can enforce.
