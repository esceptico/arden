# Subagent messaging + recursion — what to steal from codex & claude-code

Research (2026-06) mining `~/src/codex` + `~/src/claude-code-leaked` against Arden's
current `core/spawner.py` + tools. Two questions: (1) send-to/receive-from a running
sub-agent, (2) recursive (child-of-child) handling.

## Three mental models

| | **claude-code** | **codex** | **arden today** |
|---|---|---|---|
| Send to running child | `SendMessage` → `queuePendingMessage` → child drains as `queued_command` attachment at next tool round (async) | `turn/steer{expected_turn_id}` (wire) / `AgentControl.send_input` (in-proc) | **inject_queue exists but only on top-level agent** |
| Child → parent | `idle_notification`/results → lead mailbox; `plan_approval_request` → lead → human | `InterAgentCommunication` mailbox (QueueOnly vs trigger_turn); `wait_agent` blocks on `watch<AgentStatus>` | result text only; **approval Future is the one ask-back primitive (parent/UI-scoped)** |
| Reply model | async fire-and-forget; reply = new inbound msg; priority drain (shutdown>lead>peer) | status decoupled from result (`wait_agent` vs fetch) | — |
| Recursion limit | **tool-filtering**: strip `Agent` tool from children → depth=1 (carve-out for `USER_TYPE=ant` + in-proc teammates) | persisted **edge graph** `thread_spawn_edges` (UNIQUE child, recursive-CTE descendants, Open/Closed) | `AGENT_MAX_DEPTH=8`, threaded `current_depth+1`, hard-gated in `Agent.stream` |
| Tree tracking | flat team (`members[]`, one lead, no nesting) | edge table + in-mem live tree; depth encoded in `SessionSource::SubAgent` | `::` session-id chain + `parent_session_id`; **per-run `subagents` = DIRECT children only** |
| Budgets | per-agent `maxTurns`; UI mirror cap (50) after a 36.8GB/292-agent blowup | **vertical** depth + **horizontal** `agent_max_threads` (atomic RAII reservation) | shared `RunBudget` by ref = global tool-call cap; **no horizontal fan-out cap** |
| Cancel cascade | per-task AbortController; flat kill map; peer teammates deliberately UNLINKED | persist Closed → DFS-shutdown live subtree | **no explicit cascade** — grandchildren die only via asyncio CancelledError |
| Lifecycle cleanup | `registerTeamForSessionCleanup` kills panes + dirs | RAII release on child death | salvage-on-fail/timeout/cancel (good) |

## arden gaps (precise)
- **Messaging**: `inject_queue` (`RunState.queue_injection`/`drain_injections`) is wired only to the
  top-level agent (`chat.py:1302`); subagent Agents get `on_response`+`on_step_finish` but never
  `get_pending_messages`, so `Agent._drain_pending` is a no-op for them (`agent.py:250`). The only
  inbound signal to a running child is **cancel (1 bit)**. Child text is suppressed for depth>0
  (`_SUPPRESSED_NESTED_SSE`), so no partial stream/question flows up.
- **Recursion**: works, but cancellation has **no descendant cascade** (`RunRegistry.subagents` tracks
  only direct children, `state.py:287`); no multi-level result aggregation; tree not queryable;
  `background()` spawns aren't depth-soft-gated like `research()` is.

## What to port (prioritized)
1. **Steer (parent→running child)** — ✅ DONE. `BackgroundTaskRegistry` now has a per-agent inbox
   (`queue_injection`/`drain_injections`, context.py); background child Agents drain it each step via
   `get_pending_messages` (spawner.py); `send_to_agent(agent_id, msg)` tool (agentic, self-correcting
   on bad id) + `POST /chat/child-agents/{id}/inject` route + desktop `sendToChildAgentApi` + a "send
   to this agent" composer on running rows in the hub. Full loop PROVEN deterministically:
   test_spawn_salvage.py::test_background_agent_drains_steering_message_mid_run spawns a bg agent,
   queues a steering message, asserts it lands in the child's prompt at the next step. 62 server tests
   pass across spawn/background/deferred/runtime; desktop tsc clean + 354/0. Scope: only BACKGROUND
   (detached) agents are steerable (awaited/foreground are not — the parent is blocked on them anyway).
   Only unverified bit: the running-row composer's React render (no live agent at hand); its server
   path is proven.
2. **Ask-back (child→parent/human)** — generalize the approval Future (`context.py:425`) into a
   `ask(question)` request/await/resolve channel, surfaced in the right-sidebar agent hub
   (the breadcrumb panel). (= CC `plan_approval_request`→lead→human, codex `InterAgentCommunication`.)
3. **Spawn-edge registry** — record parent_run→child_run edges (Open/Closed) so cancel cascades via
   DFS (fixes grandchild leak), the UI shows the full tree (extends the breadcrumb), and "all
   descendants" is one query. (= codex `thread_spawn_edges` recursive CTE.) Reuses `parent_session_id`.
4. **Horizontal fan-out cap** — `agent_max_threads`-style atomic reservation alongside the shared
   `RunBudget`; normalize depth gate across `background()`/`research()`. (Avoid the CC whale blowup.)
5. **Wake-on-message / resurrect** — a message to a *finished* child replays over its durable
   transcript as a new turn (= CC `resumeAgentBackground`). arden child sessions are already durable →
   cheap; ties into the hub's "send to this agent" box.
6. **Control-plane split** — keep structured control msgs (cancel/pause/answer) out of LLM context
   (= CC `isStructuredProtocolMessage`); arden half-does this already (BackgroundTaskEvent + Future).

Full agent findings: workflow `whggpn4j2`.
