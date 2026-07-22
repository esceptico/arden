# Workflow engine for arden (Shape A1 — registered Python workflows)

Port ultracode's deterministic orchestration into arden. Workflows are **Python
modules registered like skills**, invoked by name via a `workflow` tool. The LLM
picks one + passes args; the orchestration itself is plain Python (no codegen,
no sandbox).

## Why this is small

`apps/server/arden/tools/research.py` is already a hand-rolled single-purpose
version of this. It: grabs `ctx.spawn_fn`, builds a tools list + system prompt,
calls `spawn_fn(... wait=True)`, returns `spawn.text`. The engine just
generalizes that and adds combinators.

Already exists → reuse:
- `ctx.spawn_fn` = `spawn_child` (spawner.py:281) — a plain `async` callable
  returning `SpawnResult` (`.text/.usage/.cost/.child_run_id`). THIS is the
  deterministic spawn primitive. Inherits depth + cost budgets via `ctx.run.budget`.
- `asyncio.TaskGroup` fan-out — already the pattern in `agent/tools/runner.py:90`.
- `TaskStartedEvent/TaskProgressEvent/TaskFinishedEvent` (events/sse.py) — spawn
  already emits these per child; phase/log piggyback on the same bus.
- Skill scanning pattern (`skills/`, `.skills/`, `~/.arden/skills/`) — mirror for workflows.
- `ToolPolicy` + `tool(...)` registration pattern (research.py:308).

Net new → build:
1. `Orchestra` runtime (combinators over `spawn_fn`).
2. Schema-validated subagent returns.
3. Workflow registry (scan + load modules).
4. `workflow` invoker tool.

## Files

### NEW `apps/server/arden/orchestra/engine.py`
`Orchestra` dataclass holding `ctx` + a concurrency `Semaphore(AGENT_MAX_CONCURRENT)`.
- `agent(task, *, schema=None, tools=None, model=None, system_prompt=None, label=None, phase=None)`
  → `await ctx.spawn_fn(..., wait=True, agent_type="workflow")`; returns
  `spawn.text`, or `coerce(spawn.text, schema)` when a schema is given.
- `parallel(thunks)` → `asyncio.TaskGroup`, barrier, returns results in order.
- `pipeline(items, *stages)` → per-item chains, NO barrier; stage sig
  `(prev, original, index)`; a `None` from a stage drops the item.
- `phase(title)` / `log(msg)` → emit lightweight events on the existing io bridge.

GOTCHA: `asyncio.TaskGroup` cancels ALL siblings on the first exception (unlike
JS `Promise.allSettled`). To match the Workflow tool's "failed item → null,
others survive", wrap every thunk so an exception is caught and returned as
`None`. Callers `.filter`/comprehension out the `None`s.

### NEW `apps/server/arden/orchestra/schema.py`
`coerce(text, schema) -> BaseModel`: extract the JSON blob from the subagent's
final text, `schema.model_validate_json(...)`.
- MVP: on `ValidationError`, `agent()` re-asks the subagent ONCE with the error
  appended ("your output failed validation: …, return valid JSON").
- Hardening (follow-up): plumb a `response_schema` through `spawn_child` →
  `Agent.stream` → `llm_client` using the native `response_format` that already
  exists in `llm/anthropic.py` / `gemini.py` / `openai_codex.py`, OR force a
  `submit(payload)` finishing tool. Removes the parse-and-pray step.

### NEW `apps/server/arden/orchestra/registry.py`
Scan `workflows/` (builtin) + `.workflows/` (project) + `~/.arden/workflows/`
(global), import each module, collect `META` + `run`. `WORKFLOWS: dict[str, Workflow]`.
`Workflow` = `{meta, params: type[BaseModel], run: Callable[[Orchestra, BaseModel], Awaitable]}`.

### NEW `apps/server/arden/tools/workflow.py`
`workflow` tool (`ToolPolicy(action=READ, scope=INTERNAL)`, `kind="agent"`):
- input `{name: str, args: dict}`.
- unknown name → return `is_error` listing available names (self-correcting; do
  NOT dead-end — the model hallucinates ids otherwise).
- validate `args` against `wf.params`; build `Orchestra.for_ctx(ctx)`; `await wf.run(o, params)`;
  return `ToolResult(content=render(result), data={...})`.
- guard `if not ctx.spawn_fn` like research.py:174.

### NEW `apps/server/arden/workflows/review_diff.py` (example)
`META` + `ReviewParams(base="main")` + `run(o, args)`:
pipeline(DIMENSIONS, review→FindingList schema, verify each finding in parallel),
flatten + filter to confirmed. ~40 lines. Reads like the JS demo, in Python.

### EDIT registration
- Register `workflow_tool` wherever the core tool registry is assembled
  (the registry the demo found at `tools/core/registry.py`).
- Call the workflow registry scan at server startup next to the skills scan.

## Verification
- Unit: `parallel`/`pipeline` semantics incl. a throwing thunk → `None`, others survive.
- Unit: `coerce` happy path + one repair retry.
- Integration: run `review-diff` against a seeded diff with a stub `spawn_fn` →
  assert N review + M verify spawns, correct fan-out shape.
- E2E: invoke via the `workflow` tool in a real run; watch Task* events; confirm
  depth/cost budgets enforced (grandchild agents respect `ctx.run.budget`).

## Open decisions
- A1 (registered, this plan) vs A2 (model-authored Python in a sandbox) — A1 chosen.
- Schema returns: MVP parse-retry vs native `response_format` plumb — start MVP, harden after.
- Do workflows get their own progress event type, or reuse `TaskProgressEvent`? — reuse for MVP.

## Lean check
This generalizes Arden's OWN `research.py` orchestration; it does not import a
foreign framework's mechanics. The reasoning loop still drives WHEN to invoke a
workflow; the workflow only makes the fan-out deterministic + reusable. (cf.
lessons: lean over framework mechanics.)
