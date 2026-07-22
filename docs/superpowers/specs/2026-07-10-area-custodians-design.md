# Area Custodians — product shape

2026-07-10. Product spec for delegating the information side of a life-domain
to its area's standing agent. IMPLEMENTED on branch feat/area-custodians
(same date) — see the implementation notes at the bottom for what shipped
and the mechanics chosen for each (adjustable). Shape is locked here; mechanics marked
(adjustable) are implementation details to settle at plan time. Grounded in a
5-lens research sweep (labs, chief-of-staff startups, devtools agents,
ambient-agent architecture, interrupt UX) — findings + sources inline;
condensed steal/traps list at the bottom.

## The promise

You stop checking on your life-domains; they report to you. Delegating an
area means: *"I keep the full picture of this domain current, I work on it
between your visits, and I interrupt you only when something genuinely needs
you."* The user's job shrinks to answering proposals and occasionally
steering. Ownership never transfers: the user stays accountable, the agent is
a delegate (Linear's agent model — "an agent cannot be held accountable").

Research validation: nobody ships "delegate a domain." Every product
converges on "prompt + trigger + scoped tools" (a cron'd prompt). The one
attempt at domain-level proactivity — ChatGPT Pulse — was sunset in June 2026
because its delegation was *implicit* (inferred interest graph → generic,
repetitive, unaccountable). Areas are the fix Pulse needed: an explicit,
named, visible, pausable delegation unit. **Explicit beats ambient** is the
category's one hard-learned lesson; areas are explicit by construction.

## What the custodian owns

1. **The picture** — the topic page is always current. Anyone (the user, a
   filed chat, another agent) reading it sees the true state of the domain.
   Event-diffed maintenance, not periodic regeneration (Dosu/Swimm's
   event-diffed docs stay current; DeepWiki's cron-regenerated wikis lag
   hours-to-days).
2. **The work** — open loops on the page are advanced, not just tracked:
   research the evidence, find the comparable cases, draft the thing.
   Progress lands on the page. Pre-researched loops also make eventual
   proposals grounded: idle-time pre-research cut hallucination 28% (ProAct).
3. **The proposals** — things needing the user become asks. The autonomy
   gradient in one sentence (Swimm/Dosu): **auto-apply trivial drift
   silently; propose significant drift.** Page edits are the silent tier —
   edit-then-log-then-revert, never approval-gated, because page edits are
   cheap to undo (Notion custom agents); asks are the proposal tier;
   external actions are never silent.

The custodian owns ITS sandbox — the page, its channel, its watch-list —
and touches nothing the user considers theirs. Motion (reshuffles your whole
calendar, users felt dispossessed, 2.7★) vs Reclaim (moves only its own
flexible blocks, cleanest trust record in the space) is the controlled
experiment: **the bound matters more than the intelligence.**

## When it acts

**Events first, heartbeat as fallback.**

- **Events wake it**: an email matching the area, a chat filed into it, its
  page edited, a calendar item approaching, an ask resolved. Every event
  passes a **cheap triage gate first** — ignore / note-for-next-run / wake
  now — before the expensive agent loop spins (LangChain's triage-before-
  agent; small-model event gates beat LLM wake-decisions 4–83× cheaper with
  better precision, arXiv 2605.30152). (adjustable: gate implementation)
- **Self-paced heartbeat**: each run ends with the agent choosing its next
  check + a one-line reason ("interview window opens Tuesday"), clamped by
  operator bounds. Precedent: Claude Code /loop's dynamic pacing (the only
  shipped self-paced cadence) and OpenClaw's split — **agent owns WHAT to
  watch and when to look; operator owns the clamps and the channels.**
- **Decay is automatic**: consecutive quiet runs stretch the interval
  regardless of what the agent asks for; activity (asks produced, page
  deltas, events) earns faster cadence. Assume self-importance drift — even
  a model fine-tuned on 6,790 human accept/reject labels misfires on ~1/3 of
  proactive offers (ProactiveBench). (adjustable: decay curve)
- **The cadence is the agent's problem, not the user's.** No cron
  configuration anywhere in the UX; the room just shows "last checked /
  next check: … — why".
- **Woken means visibly awake**: on wake the room reflects activity within
  seconds even when the real work takes minutes (Linear agents' 10-second
  acknowledge rule) — silent non-execution killed more standing-task
  products (ChatGPT Tasks, Gemini) than wrong execution did.

## How it reaches the user

Three touchpoints, escalating attention; **one ask, one canonical channel**
(multi-channel stacking trains users to ignore channels — Eleken/Slack).

1. **Push notification** (Telegram/email/etc.) — only for asks that need the
   user: `question` (agent blocked on their judgment) and `review` (proposed
   action awaiting approval). Earned, not chatty. Silence is the wire
   default: a run that found nothing substantive delivers nothing
   (OpenClaw's HEARTBEAT_OK suppression). Batching at delivery can't fix
   over-generation at the source (Apple scheduled-summaries lesson) — the
   custodian earns each item or writes it to the page instead.
2. **Home** — the standing queue of asks across areas, grouped by area (mode-
   batching, Superhuman split-inbox), each answerable in place with exactly
   the affordances its kind allows. The queue visibly **ends** ("that's it
   for today" — Pulse's one universally praised idiom). Every item carries
   its action — the briefing-to-action gap is the documented digest killer.
3. **The room** — the audit trail: what it did, what changed on the page
   (diffs), what it's watching, when it looks next and why, what it chose
   NOT to surface. Written as a story grouped by goal, not a log (Smashing
   Magazine transparency patterns). A visibly alive "last checked" line is
   mandatory — a silent-stalled custodian destroys trust faster than a noisy
   one.

**Ask taxonomy** — three kinds, arrived at independently by LangChain
(notify/question/review) and PRISM (act/defer/ask), replacing the current
review/decide/act/drift:
- **notify** — FYI, no decision; lands in Home, no push. One tap to clear.
- **question** — the agent is blocked on the user's judgment; push.
- **review** — proposed action awaiting approve / edit / reject-with-reason;
  push. Approval is at **plan level, not step level** (Devin checkpoints).

Every ask names the concrete object, the why-now, and the what-happens-next
("Agent requests approval" is unanswerable and trains rubber-stamping).
Asks/cards carry contextual tuning — "fewer like this" — which doubles as
training signal (Gmail importance marker, Pulse thumbs). (adjustable: how
feedback feeds back)

**External actions**: draft-first is the industry's de facto safety default
(Lindy/Fyxer/Shortwave), and the startup evidence is brutal — one wrong
external action (Lindy's wrong-recipient emails) outweighs hundreds of
correct ones, and silent errors are worse than loud ones. Custodians never
auto-execute outward-facing actions; `act` autonomy means running the area's
own automations/workflows, and even there irreversible steps go through
`review`. Escalation is tied to **action class**, not the global dial
(OpenAI Operator): reversible-and-internal auto-runs and logs; consequential
confirms; credential/payment-class is out of scope entirely.

## Operator controls (mini-settings per area)

Principle: **the defaults are the product; overrides are the escape hatch**
(<5% of users ever change settings; Slack's rebuild treated per-channel
override usage as a failure metric). One compact panel per room:

- **Autonomy** — observe / act (exists today; master switch).
- **Attention** — dormant / ambient / active. The one dial that sets all
  other defaults: heartbeat bounds, event-gate sensitivity, push policy.
  (adjustable: exact preset values)
- **Interrupts** — what earns a push: question+review (default) / everything
  / nothing (Home-only).
- **Sources** — which inputs feed and wake it: email, calendar, web research.
  Off-by-default for personal-data sources (Pulse's connectors precedent).
- **Instructions** — freeform standing brief ("focus on the EB-1 angle";
  "never draft emails"). Already exists on areas.
- **Pause** — vacation switch; the room shows paused state honestly.
- **Budget line** — bounded per-wake cost × bounded frequency = computable
  worst-case spend per area, displayed here. Notion's agent-pricing backlash
  and Lindy's credit-burn churn show cost surprise kills these products
  before quality does. Layered guards (per-run ceiling, daily cap, repeat-
  call circuit breaker) live in the harness, not the prompt. (adjustable:
  amounts)
- **Auto-pause when ignored** (ChatGPT Tasks): asks that go unanswered long
  enough decay the area toward dormant instead of shouting louder. Attention
  is the budget signal. notify-kind asks can also expire quietly (Pulse's
  24h card expiry) instead of accumulating. (adjustable: windows)
- **Clarify-at-intake** (Duckbill): delegating an area (attaching the agent)
  front-loads the judgment questions — what to watch, what never to touch,
  what counts as urgent — so autonomous work doesn't stall on mid-flight
  questions later. This seeds the Instructions field.

## Architecture posture (shape-level only)

- The custodian is the **curator half of a curator/converser split** (Letta
  sleep-time compute): it holds the page-edit rights and runs off the
  latency path; user-facing chats in the area read the page but don't tend
  it. Its channel session is the durable transcript; every run logs a
  cycle-level decision trace — trigger, evidence, decision, why-not-notify
  (Springdrift audit-trail pattern).
- Wake pipeline: event → cheap gate → (maybe) run → salience-scored findings
  (Inner Thoughts: relevance / information-gap / expected-impact) → below
  threshold: page update only; above: ask of the right kind → channel by
  kind. (adjustable: scoring mechanics)
- Re-verify world state at wake before acting on anything queued from a
  previous cycle (stale-context actions are a silent failure class).

## Traps this design must not walk into (condensed)

1. Implicit delegation → generic content → distrust (Pulse's death).
2. Wrong external action → instant trust collapse (Lindy); silent errors
   worse than loud ones (Fyxer misfiling).
3. Rubber-stamping: approval scrutiny measurably declines with exposure —
   keep the review queue to the genuinely uncertain band, raise judgment
   altitude (plans/outcomes, not steps).
4. Notification fatigue via channel stacking or delivery-layer batching.
5. Self-importance drift: marginal findings go to the page, never the phone.
6. Runaway cost: retry loops + unbounded iteration are the #1 documented
   production failure; harness-level caps, visible per-area spend.
7. Silent stall: "green run ≠ success" — the room must show liveness and
   the trace must be inspectable.
8. Settings farm: attention presets must make the panel optional.
9. Prompt-bloat heartbeats: the standing watch-list stays tiny; quiet beats
   skip.
10. Review burden as the scaling wall (GitHub: agent PRs multiply faster
    than reviewers) — proposal *taste* is the product, not proposal volume.

## The headline from research

**Nobody has shipped the per-life-domain standing custodian.** Labs ship
whole-life-implicit (Pulse, dead) or single-cron-prompts (Tasks, Scheduled
Actions); startups ship per-workflow (Lindy) or per-inbox (Fyxer); devtools
ship per-ticket/per-repo. The domain-scoped unit with per-area operator
settings is an empty slot, and both the graveyard (Pulse, Mariner, Copilot
Workspace) and the survivors (Reclaim, Notion agents, Linear's doctrine)
point at the same recipe this spec encodes: explicit delegation,
event-driven waking, bounded surfaces, inbox-not-interrupt delivery,
self-decaying attention.

## Out of scope (for now)

- Outward high-stakes actions (payments, sending as the user) — not in v1.
- Cross-area synthesis (one custodian noticing another area's relevance).
- Learned wake-gates / trained salience models — start heuristic.
- iOS surface (explicitly deprioritized by the user).

## Sources

Full cited reports live in the research agents' outputs (5 lenses:
labs, startups, devtools, architecture, interrupt UX; 2026-07-10 session).
Headline sources: LangChain ambient agents + Agent Inbox; Letta sleep-time
compute; OpenClaw heartbeat; Claude Code /loop + Routines; ChatGPT
Pulse/Tasks (sunset postmortem coverage); OpenAI Operator; Linear agents;
Swimm/Dosu; Lindy/Fyxer/Reclaim/Duckbill reviews; ProactiveBench (ICLR'25),
Inner Thoughts (CHI'25), PRISM, ProAct, Codellaborator (CHI'25), arXiv
2605.30152 (cheap trigger gates), arXiv 2604.04660 (decision traces).

## Implementation notes (feat/area-custodians)

Shipped 2026-07-10, commits 2efe8405 (server) + bc516eca/... (desktop):
- Asks: notify/question/review + salience 1-5 (threshold 3), ≤3/run, why_now
  + what_next, notify TTL 72h; legacy kinds migrate on state-file load.
- Attention presets: active 2h–48h/8 runs·day, ambient 12h–7d/3, dormant
  3d–14d/1; quiet decay ×1.5 from the 2nd quiet run; ignored asks (3 runs)
  step attention down.
- Events wired: chat filed into area, topic page edited (memory watch), ask
  resolved; 10-min debounce coalescing; WOKEN BY lines injected into runs;
  budget + pause gate wakes (heartbeat still carries noted events).
- Self-pacing: run's structured output picks next_check_hours + reason;
  post-run set_next_run overrides the trigger advance; fallback = ceiling.
- Notifications: all configured notifiers, per-area interrupts policy
  (asks = question+review default / all / none).
- Intake: first run (iteration_count 0) gets the intake addendum.
- Pause disables the agent automation outright; resume fires promptly.
- Room: AgentStatusLine + settings popover (attention/interrupts/pause +
  runs-today budget); ask cards typed (Got it / Reply / Approve+Reject);
  "Fewer like this" appends to area instructions; Home queue ends visibly.
- NOT shipped (per spec's out-of-scope): outward high-stakes actions,
  cross-area synthesis, learned gates/salience.

## E2E verification (2026-07-10, isolated sandbox server on live-data copy)

Booted the branch server against a copy of the live ARDEN dir (neutralized
notifiers, API-key model). Proven end-to-end, over HTTP, with a real LLM run:
migrations (settings columns, ask-kind fold: 15 question + 2 notify, 0
legacy); PATCH attention/interrupts; pause disables the agent automation,
resume re-enables; chat-filed event pulled next_run from tomorrow 03:00 to
+10 min with the event coalesced into custodian state; the run consumed
WOKEN BY, executed INTAKE (wrote a WATCHING section to the page), nominated
one question ask with why_now/what_next, pushed it through every configured
notifier with the ask-anatomy copy, persisted report + next-check reason,
and self-paced to the active preset's 48h ceiling; room API serves the full
agent block; resolving the ask wakes the custodian.

Bug found by e2e and fixed (26a8699d): **self-echo loop** — the run's own
page write triggered the page-edited watcher, scheduling another run in 10
minutes (bounded only by the daily budget). Page-edit events are now
dropped while the agent runs or within 5 minutes after (in-window edit
verified ignored; out-of-window external edit verified to wake).

Honest note: the room's audit trail is the channel transcript + report +
woken-by + next-check reason; per-edit page DIFF rendering in the room
itself is not built (the transcript shows the edits).
