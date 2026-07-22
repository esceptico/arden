# Lessons

## Print the VALUE, not just the error — and validate your harness (Jun 2026)

Electron 42 upgrade → "asks for API key every launch". I burned three theories (Keychain ACL/ad-hoc signing, dev-plaintext workaround, duplicate keychain items — including telling the user to delete keychain entries) before one probe that printed `typeof result` + `Object.keys(result)` revealed the truth in 30 seconds: `decryptStringAsync` now resolves `{shouldReEncrypt, result}` instead of a string, and our code passed the object into a string-normalizer that silently coerced it to "". Two rules: (1) when a previously-working API "impossibly fails", inspect the actual returned value/shape FIRST — a breaking API change is more likely than OS-level conspiracies; check the upgrade's breaking-changes/release notes for the EXACT functions you call, not just the ones that error. (2) Validate the test harness before trusting its signal: macOS has no GNU `timeout`, so `timeout 25 electron … || echo "TIMED OUT"` printed a fabricated "hang" (exit 127, command never ran) that I read as a Keychain prompt and built a whole theory on. Check exit codes before interpreting silence or absence as behavior.

## Read the user's literal words; check the WHOLE screenshot (Jun 2026)

User reported quick-capture makes "app in the dock disappear." I pattern-matched to "main window gets hidden" (app.hide()) and ran several screen-driving rounds without once checking the Dock strip at the bottom of my own screenshots. User had to interrupt: "can't you see it disappears from the bottom menu????" — the literal complaint was the DOCK ICON vanishing (`setVisibleOnAllWorkspaces(visibleOnFullScreen)` transforms process type to UIElementApplication; fix `skipTransformProcessType: true`). Rule: when a report names a concrete UI location ("dock"), verify THAT spot explicitly in every screenshot, and confirm early which symptom the user means when two plausible readings exist. Also: budget computer-use rounds — batch assertions per round and read every region of the screenshot; the user tires of watching repeated screen-driving loops.

## Subagent = first-class session; don't band-aid with polling (Jun 2026)

I "fixed" no-live-updates-in-subagent-sessions with a 2s poll-refresh of the viewed child session (`useViewedAgentSessionRefresh` + `appendLatestForSession`). User: "not correct — subagent must look and work the same as default agent, no differences."

Root: subagent events emit to the PARENT bus (foreground shares `calling_ctx.io`) / a throwaway `IOBridge` (background); the child session's own `SessionBus` is never fed, so `/chat/events/{childSessionId}` is silent → the viewed child is static. A poll is a special-case band-aid that makes subagents second-class.

Rule: when a subagent/child concept already maps onto an existing first-class primitive (a session), stream it through the SAME machinery (its own bus + the normal `useEvents` path), not a parallel poll. Ties to [[feedback_no_special_casing_fit_general_primitives]]. Correct fix: route the child agent's events to its OWN session bus at **depth 0** (the desktop hard-gates `!event.depth`, so depth-1 events render blank/orphaned); the parent gets ONLY lifecycle (it treats session-backed agents as trace leaves). Non-obvious trap: bus idle-eviction is keyed on `get_active_run(session_id)` — a child session has no RunState, so navigating away mid-run silently evicts + recreates its bus with reset seq (replay drift); needs a lightweight active-session marker.

## Subagent live view is gated on RUN LIFECYCLE, not content (Jun 2026)

Shipped the above "stream to child bus" fix routing only CONTENT events (text/tool/reasoning). User: STILL static "Worked". The desktop renders a viewed session live-vs-collapsed off the RUN LIFECYCLE, not content: `TurnGroup` collapses to "Worked" when `turn.endedAt != null`; `endedAt` is cleared to null (→ live) ONLY when `runtime.active_run` is foreground on load (history-response.ts), and set by `RUN_FINISHED` on the bus (transcript-projection `endTurn`). A child that streams content but has no run framing loads as done → "Worked". Fix is SERVER-ONLY (client already handles these events — "it's the same events"): emit `RunStarted`/`RunFinished` on the child bus AND surface `runtime.active_run` (mark_session_active → get_active_run → _session_runtime_snapshot). Use the **PARENT run_id** for the bus events — it matches the marker's runtime.active_run, so the load-time signal and the bus signal agree on one id. (A child chat_run with a distinct child_run_id MISMATCHES the marker's parent run_id → `RUN_FINISHED` gated out → spinner never stops; that was my first wrong cut.) Orchestra/workflow agents run via `ctx.spawn_fn` (= spawn_child), so ONE spawner/factory fix covers both. Rule: to make a derived session "work like a normal session," replicate its full RUN LIFECYCLE (RunStarted/RunFinished + runtime.active_run), not just content; verify against the client's live-vs-done gate (`turn.endedAt`/`currentRunId`), not "events arrive."

## React StrictMode: `useRef(true)` + cleanup-only-false "mounted" gate is broken in dev (Jun 2026)

`const mounted = useRef(true); useEffect(() => () => { mounted.current = false; }, [])` is unsafe under `<StrictMode>` (enabled in src/main.tsx). Dev runs mount effects setup→cleanup→setup; the cleanup flips it false and the empty setup never restores true, so the ref is stuck `false` for the component's whole life in dev — every `if (mounted.current)` gate becomes a permanent no-op (stuck spinners, errors never shown, busy never clears). Bit twice: `LensEvidenceSearch`'s `mounted` and the shared `useMountedRef` hook (feeding `useMutationState`).

Rule: prefer DROPPING the gate — setState-after-unmount is a harmless no-op in React 18/19 (the warning that motivated these guards was removed in React 18), so a mounted-gate that only protects setState is pure defensive code (no-defensive-code stance). Only if a gate is genuinely load-bearing (guards a side effect, not setState) set `ref.current = true` at the START of the effect body (the LensPage pattern), never cleanup-only. Audit before deleting a shared hook: grep all callers and confirm none use the ref for a side-effect guard.

## "use /workflows" means orchestrate the implementation too (Jun 2026)

On the lens-search task I used workflows for discovery + adversarial review but implemented the actual changes solo in the main loop (reasoning LensEvidenceSearch.tsx / LensesView.tsx overlapped too much for parallel agents). User: "next time use /workflows please."

Rule: when the user asks to use /workflows, decompose the **implementation** across workflow agents — `isolation: "worktree"` for overlapping-file parallel edits, or `pipeline()`/`parallel()` partitioned by file/area for independent ones — not just the research/review bracket. Solo main-loop editing only for trivial or tightly-coupled single-file changes.

## Memory work — the spec IS the implementation guide; read it FIRST, every time (Jun 2026)

Before ANY memory decision (diagnosis, fix, build, answer), read the governing docs FIRST — never improvise then check:
- `~/vault/Memory Consolidation/Memory — vision (new spec).md` (the model)
- `~/vault/Memory Consolidation/Lens — spec.md` (lenses)
- `tasks/memory-rebuild.md`, `tasks/lens-view-correction.md` (what was built / locked)

Cost of not doing this: I proposed routing `remember()` through Extract (user had explicitly said "remember is just a tool, no logic") and missed that the built lens layer (single `people` lens grouping by `canonical_subject`) DIVERGES from the spec, which says **entity = lens, membership by LLM scoring** (Lens spec §4). Two wrong fixes in a row because I reasoned from code + memory instead of the spec.

Rule: the spec is the source of truth. Read it before forming any opinion. If code diverges from spec, the divergence is the bug — don't rationalize the code.

## Memory rebuild — no hard-rule/lexical heuristics (Jun 2026)

Build/workflow agents repeatedly smuggle lexical heuristics in despite "no heuristic gate" — Stage 3 baked them into the CONTRACT and multiple components: `_PRONOUN_HINTS = {"i","me","user",...}` (reconcile subject recall), `_PROPER_NOUN_RE` regex + `_STOPWORD_CAPS` (extract), and a contract-level "Pronoun/role channel (deterministic)".

Rule: **every decision AND every recall channel is LLM/embedding/FTS — never a hand-maintained word list, keyword set, or regex-for-meaning.** Subject/coreference identity comes from the LLM resolving a canonical subject at extract time + embedding/FTS recall + LLM judge — not a pronoun list. (English-only word lists are also brittle/non-general.)

Prevention: workflow build prompts MUST say this explicitly (not just "no opaque gate"); a verify step MUST grep the pipeline for `frozenset`/`_WORDS`/`_TERMS`/`_HINTS`/`re.compile`/keyword-`in {`-sets and fail on any decision/recall use. Watch capture's idle/time boundary — a mechanical chunking trigger is OK; a *semantic* gate is not.

## Lens page: don't make the model reproduce opaque ids; don't block the GET on synthesis (Jun 2026)

Two coupled bugs shipped because the test stub was too friendly:

1. **Raw-list fallback fired on every faithful render.** `project.py` required the synthesizer to echo `<!--claim:ID-->` anchors verbatim and fell back to raw bullets if any were missing. Real models drop opaque ids (lessons above), so the page was never the synthesized markdown. Fix: the model cites claims by the numbered `[n]` tag it was given (it never sees the id); the projector injects anchors deterministically post-synthesis (`_inject_anchors`, index→id substitution — structural, not a heuristic). Raw fallback now means genuine failure only (blank output / cites no claim at all).

2. **The page GET ran multi-call synthesis synchronously and timed out.** Membership refresh + re-validate + one strong-model synthesis per subject = 6–14s on the request path. Fix: `LensPageGenerator.ensure` — cache hit returns the page (200); miss/dirty/refresh starts ONE deduped background `asyncio.create_task` and returns a `generating` status (HTTP 202); the projector reports stage/subject/i-n progress through a callback; a `/page/status` endpoint is the UI poll target. New fast-path `LensProjector.cached_page()` reads the materialized page with zero synthesis/judge.

Anti-stub testing rule (this is WHY the bugs hid): the existing tests passed with a stub that echoed anchors instantly. New tests MUST defeat that — a synth stub that cites by `[n]` and provably emits NO `<!--claim:` (prove injection, not echo), and a GATED/slow synth stub awaited via `httpx.ASGITransport` (so the route's background task runs in the test loop) to prove the GET returns 202 fast and never blocks. A `TestClient` runs its own loop in a worker thread and orphans the `create_task`, so use `AsyncClient(ASGITransport)` + `gen.drain()` when asserting background completion.


## Design-system tokens (May 2026)

### @property registration is required to transition CSS custom properties
A bare `--my-var: 0.3` declared in `:root` is a *string* to the browser — CSS
transitions on it snap rather than interpolate. To get smooth easing across
custom props (e.g. glass rim alpha drifting on hover), declare them with
`@property` so the browser knows the syntax/type:

```css
@property --rim-alpha {
  syntax: "<number>";
  inherits: true;
  initial-value: 0.18;
}
```

Bit us in Phase 5 of the tokens migration — the rim drift looked like a step
function until we registered the prop.

### Token migration: ship aliases first, retire later
When renaming or restructuring tokens (motion, color, elevation) across a large
component surface, the discipline that kept each phase reviewable was:

1. Land the new token module alongside the old one — re-export the old name as
   a back-compat alias from the new module.
2. Sweep call sites to the new name in a follow-up phase.
3. Delete the alias only in a final cleanup phase, after `tsc --noEmit` confirms
   zero remaining importers.

This keeps each commit small and bisectable. Trying to rename + sweep + delete
in one pass forces every consumer through review at once and makes the diff
unreadable. Phase 4 (color) deferred its alias-retirement entirely because
per-palette `:root.palette-*` blocks still override the defaults — aliases are
load-bearing until a separate palette-block sweep lands.

### Hooks go above the early return — always
Adding `useStore` / `useWorkflows` *below* `ActivityMessage`'s early
`if (!message?.activity) return null` introduced a Rules-of-Hooks violation:
3 hooks ran on the null path, 5 on the full path, so React crashes on the
transition between them. `tsc` and the existing test suite were both green —
the bug only fires at runtime when a message flips between empty/non-empty
activity. Rule: when adding a hook to a component that has any early return,
put it (and every hook) above the return, even if the data it needs is only
used after the guard — compute the non-hook parts later. A review agent caught
this, not the tests; render-tests that never exercise the early-return branch
won't either.

### Don't key process-global caches on a fixed name under concurrency
The dynamic-workflow traceback first rendered source via
`linecache.cache["<workflow-script>"]` — a single shared key, overwritten per
run. Two concurrent workflows (background + foreground) clobber each other, so
one run's error renders another run's source text (read lazily at format time,
after `await`s yield control). Fix: pass the script string into
`format_script_traceback` and render source from it directly — no shared global
state. When a "cache" is keyed on a constant and read across `await` points,
assume interleaving will corrupt it; key per-run or pass the data explicitly.

### Never overwrite a durable doc with raw, unguarded LLM output
The memory Curator wrote `resp.choices[0].message.content or ""` straight over
`user.md`. A content-less but non-erroring completion (refusal, content filter,
truncation, reasoning-only finish → `content=None`/`""`/whitespace) collapsed to
`""`, atomically blanked the whole doc, AND advanced the watermark so the lost
turns never re-curated — silent, permanent memory loss, exactly the durability
the rebuild exists to provide. Fix: `_complete` returns `None` for any falsy/
whitespace body so it takes the failure path (no write, watermark not advanced,
retried next session). Rule: any path that overwrites durable state with a full
LLM response must treat empty/blank output as a FAILURE, not as "the new value" —
guard before the write, and only advance progress markers after a real write. An
adversarial review agent caught this (3 independent verifiers converged on it);
the builder's own tests passed because no test exercised the empty-response path.

### Workflow agents with a strict `schema` silently drop huge free-text outputs
Two recon agents asked for a large structured object via `agent({schema})` and
"completed without calling StructuredOutput" — the whole result was lost (null).
Big, discursive architecture dumps don't reliably round-trip through the forced
StructuredOutput tool. Rule: use `schema` only for compact, enumerable results
(findings lists, verdicts); for long prose/maps, let the agent return plain text
and parse/synthesize in a later step. Recovered both by re-running them schema-less.

### Workflow leaf-agent tools: model passes NAMES, not schemas
A dynamic workflow that wrote `agent(q, tools=['slack_search','slack_thread',...])`
came back empty for every agent. Root cause: `agent(tools=...)` → spawner
expected tool-schema DICTS, so `"slack_search".get("function")` raised
AttributeError, which `_safe` swallowed to None inside `parallel()` — surfacing
as "subagents came back empty/null (tool access weirdness)", not a crash. Two
fixes: (1) spawner resolves a list of name strings against the full toolset
(self-correcting interface — the model thinks in names); (2) the tool
description now says agents inherit ALL tools by default and `tools=` only
RESTRICTS. The model had passed tools trying to *grant* access; default-None
already grants everything. Instance of [[feedback_self_correcting_tool_interfaces]]:
when the model's natural input shape differs from the interface, accept the
natural shape — don't let a type mismatch fail silently through an error sink.

### Verify the REAL failure mode, not a seed that bypasses the broken layer
The workflow card "didn't render" for three rounds because each time I verified by
SEEDING the client workflow domain directly — which masked the actual bug. Real
root cause was two chained breaks: (1) liftWorkflows gated entirely on the domain
(`if workflows.length === 0 return no-lift`), never consulting the always-present
tool-call ITEM; (2) the domain is an SSE-stream-only projection with no durable
rehydration — on reload the event cursor sits at the checkpoint and the
`workflow_started`/`task_*` rows (seq ≤ checkpoint) are never replayed, and
loadHistory never repopulates it. So the card only existed in a narrow same-turn
window; any reload dropped it. Seeding the domain injected exactly the rows the
real flow never produced. Lesson: when verifying a fix, reproduce the user's
ACTUAL state (here: empty domain after reload), not a convenient fixture that
pre-populates the layer under test. If you can't run the true end-to-end path
(server was import-broken), at least seed only the INPUT the real path produces
(the tool-call item) and leave the suspect layer (the domain) empty. The robust
fix made the card item-driven (renders from the tool call, enriches from the
domain when live) so it no longer depends on the fragile/un-rehydrated stream.

### Tests that construct the object directly mask broken production wiring
The child_io drill-in feature shipped "fixed" (1197 tests passing) but never worked
in production for ~2 rounds: chat.py set `child_io_factory` on a `RunState`, but the
spawner reads it off the agent's `RunContext` — different objects, so it was a dead
write. Every streaming test passed because each constructs `RunContext(child_io_factory=
...)` ITSELF, exercising the spawner in isolation but never the real
`run_chat → create_agent → tool_ctx.run` wiring that was broken. Same shape as the
workflow-domain bug earlier this session (seeded the domain, never the real reload
path). Rule: for a wiring/integration bug, add at least one test that exercises the
PRODUCTION assembly (here: `create_agent(child_io_factory=f)` → assert it lands on the
run the consumer reads), not just the consumer fed a hand-built input. When two
dataclasses both plausibly hold a field (RunState vs RunContext), assert object
identity — `a.x is the_thing` — at the seam.

### Supervision surfaces never earn a scroll (2026-07-20, board-home)
Redesigned board-home as a scrolling page (sections stacked vertically, whole room
scrolls) because that's what the sibling boards do. User verdict: "scroll for
mission control page (with 100500 noisy stuff) for adhd person — ok bummer."
Reading/config pages (memory, settings) can scroll; a STATUS/triage surface cannot —
its entire job is one-glance state, so it must fit the viewport with stable
geometry. Rule: for any home/dashboard/monitor surface, budget the vertical space
FIRST (fit at ~800px height), use columns before stacking, cap each list with a
quiet "+N more" instead of overflow, and treat prose-y detail lines as click-depth,
not glance-surface. Idiom consistency with sibling pages does not override the
page's job.

### Design direction is architecture: research → options → ask (2026-07-20, board-home round 2)
Rebuilt the home mockup's IA twice without asking (scroll room → two-column
no-scroll panel, plus a unilateral "Mission Control"→"Home" rename) and did zero
external research — only internal mockup-idiom archaeology. User: "unbelievable —
you even didn't ask me — pure slop — can't see you researched anything in the web."
Two standing rules were already in memory and got ignored: "present architectural
options" and "research existing before new". Rules restated: (1) For any "make this
good/modern" design ask, WEB research of best-in-class references comes first and is
shown as evidence. (2) Layout paradigm / IA / renames / what's-on-screen are
architectural — present 2-4 researched directions with tradeoffs and STOP for the
pick. (3) Terse negative feedback names a problem; it does not authorize a
unilateral re-architecture. Autonomy is for execution inside a chosen direction.

### Thrash: treating each user message as a diff instead of holding one design (2026-07-20, board-home rounds 5-6)
Six rebuilds in one session. Each user message triggered an immediate rebuild/deletion
pass; the user watched elements appear and vanish without a narrative: "why are you
fucking removing it or do something — i can't understand what the hell are you doing —
you had a research and shape — and do shit anyways." Rule: once research + user verdicts
define the shape, synthesize ONCE into a stated contract, execute it ONCE with craft,
then FREEZE. After the freeze, apply only the user's explicit deltas — never
reinterpret, never re-architect, never polish unprompted. Every change must be legible:
say what changed and why in one line before touching the file again.

### "You can't understand what's happening" meant AS A USER (2026-07-20, board-home)
Read a plain usability complaint as philosophy and wrote an essay back. The actual bug:
shared syncAll() re-rendered + re-animated every region on every state change, so
acting on one row replayed the focus panel's entrance — the user literally could not
tell what was happening on screen. Rules: (1) when a frustrated message is ambiguous,
check the UI for a concrete defect BEFORE interpreting it as meta-commentary; (2) any
shared sync must be change-guarded per region — a region animates only its OWN change
(key the rendered state, skip when unchanged); touching X must never move Y.
