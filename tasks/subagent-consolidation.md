# Consolidated subagent control — "facade + edge" (recommended)

Decision doc from a 3-architect + adversarial-critic design pass (workflow wl55uxqui).
Question: should Arden's fragmented subagent control (two registries, 3 cancel routes,
inbox only on bg, inject_queue only on top-level run) converge onto one model?

## Answer: yes, but the SWEET SPOT for a single-user system is "facade + edge"
Not the full codex graph (over-built here), not the full-thread rewrite (rewrites RunState
~40 sites for root-vs-child symmetry the single user never feels), and NOT plain minimal
(can't cancel-cascade or walk descendants — the two things that motivate consolidating).

### The shape
1. **AgentControl** — one front door built from the existing `RunRegistry`, holds no state:
   `resolve(agent_id) -> handle` with `inject(msg)`, `cancel()`, `status()`, `children()`.
   `agent_id` accepts the id forms already in use: run_id, foreground tool_call_id, bg child_run_id.
   resolve() dispatches to whichever registry owns it (RunState / RunState.subagents / per-session
   BackgroundTaskRegistry). Skip the Protocol+3-adapter ceremony — a small union + internal branch
   is enough at single-user scale.
2. **Uniform steer** — give `SubagentHandle` the same inbox the bg registry got, and mirror the
   one-line `sub_agent.hooks.get_pending_messages = _drain_steering` into spawner.py's FOREGROUND
   branch (today it exists only in the bg branch ~spawner.py:811; the root wiring at chat.py:1302
   never reaches sub_agent, so foreground children have NO inbox). Now steer is one pattern everywhere.
3. **One edge primitive** — `parent_agent_id` + a `descendants()` walk, sourced from columns ALREADY
   persisted (`sessions.parent_session_id`, `background_agent_runs.parent_run_id/parent_tool_call_id/
   child_session_id`) via recursive-CTE or in-Python walk. No new in-memory graph → cascade survives
   restart for free. cancel-cascade = resolve each descendant and call its cancel() (hits both the DB
   `request_background_agent_cancel` and in-memory `registry.cancel`).
4. **Collapse routes** — the 3 cancel routes + the inject route → `/chat/agents/{id}/{cancel,inject,
   status,children}`. Old routes become shims, then deleted.

### Deliberately deferred (until a concrete feature needs it)
status-watch / `wait_agent` (codex `watch<AgentStatus>`), node-scoped ask-back Futures, fan-out cap,
inter-agent mailbox. Each becomes a method on the SAME AgentControl surface later — not new plumbing.
AgentControl is the seam to put a real in-memory graph behind IF wake-on-message ever needs it.

### Why this and not the others
- MINIMAL (facade only): under-delivers — no cascade/descendants.
- GRAPH (in-mem node graph): pays for cascade+descendants by ALSO building status-watch, ask-back
  Futures, fanout budget, inter-agent mailbox — none exercised by a handful of agents; ask-back/await
  add real deadlock + lifecycle-leak surface. Only its parent_id edge earns its keep, and you can have
  that edge without the graph (from the DB).
- FULL THREAD: rewrites RunState (most-referenced runtime object) for symmetry the user never feels.

## Sequencing — strictly additive, nothing thrown away
- **Step 1 ✅ DONE**: BackgroundTaskRegistry inbox + `/chat/child-agents/{id}/inject` (the bg steering
  channel). Becomes the BackgroundHandle's backing store — not a throwaway prototype, it's literally
  the first per-agent mailbox the end state is built from.
- **Step 2**: mirror the inbox onto `SubagentHandle` + wire foreground `get_pending_messages`
  (1-line, additive — foreground children gain steering they lacked). Steer uniform.
- **Step 3**: `agent_control.py` wrapping RunRegistry — pure read/dispatch, no route changes.
- **Step 4**: `parent_agent_id` edge + `descendants()` (from existing DB cols) + cancel-cascade;
  collapse routes to `/chat/agents/{id}/*`; point desktop at them; delete old routes.

Every step only adds. The graph tier, if ever wanted, slots behind AgentControl without changing callers.
