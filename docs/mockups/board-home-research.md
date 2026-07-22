# Mission Control — design research synthesis (2026-07-20)

Six-lens web research for the board-home surface. Full reports in the session scratchpad; this file keeps the durable conclusions. Lenses: Linear (Triage/Inbox/Pulse/agent sessions), email triage (Superhuman/HEY/Shortwave), agent-supervision products (GitHub Agents panel, OpenAI Codex, Cursor, Devin, Claude Code, Factory, LangChain Agent Inbox), personal today-surfaces (Things/Todoist/Sunsama/Akiflow/Motion/Amie), ADHD & cognitive-load evidence (W3C COGA, WCAG 2.2.2, NN/g, interruption science, spatial-memory HCI), ops glanceability (Stephen Few, Grafana/Datadog, GitHub merge box, Vercel, incident.io, dark-cockpit aviation).

## Convergent findings (every lens agrees)

1. **Dark cockpit.** Nominal state is silent: healthy work aggregates into one faint line; only deviations become rows. "Glanceable confidence = the absence of annunciation, not a wall of green." (Aviation; GitHub merge box "failing on top, passing aggregated"; Motion exception-pinning; Devin confidence-gating.)
2. **The page states its own answer.** A headline that resolves the #1 question — "Nothing needs you" / "2 things need you" — top-left, before any list. (COGA "make the purpose clear"; Few pitfall #9: top-left is prime real estate.)
3. **One decision at a time, tiny verb set, auto-advance.** Triage flows (Linear `1/2/3/H`, Superhuman J/H/E) beat lists: next item appears on resolve; never return to the list to re-choose. 3–4 fixed verbs, same order everywhere. (GDS one-thing-per-page; Hick's law; ADHD task-initiation.)
4. **Two lanes, never merged.** Decisions-needing-me ≠ FYI/ambient. Needs-me subdivides: *Question* (agent blocked on info) / *Review* (approval gates an action) / *Notify* (no action) — and *blocked mid-run* ≠ *finished awaiting direction*. (LangChain interrupt modes; Claude Code permission_prompt vs idle_prompt; Linear Triage vs Inbox vs Pulse.)
5. **No perpetual animation. Ever.** Shimmer/pulse/spinners on a status page are the exact WCAG 2.2.2 failure class, which names ADHD. Liveness = a changing verb phrase + elapsed time + last-activity age + diff chips (`+42 −18`). (WCAG; NN/g peripheral-motion mechanism; Cursor "current step + elapsed"; Claude Code diff chips.)
6. **No completion toasts; page-arrival is the notification moment.** Interruptions cost ~23min of refocus; expired toasts are lost information for a working-memory-limited user. Batch changes; a HEY-style "new since you looked" band absorbs them. (Mark CHI'08; Iqbal & Horvitz; NN/g intrusiveness-matching.)
7. **Spatial stability is sacred.** Fixed regions, deterministic order, no live re-sorting, reserved space for empty states — reflow/reorder is the empirically confirmed spatial-memory breaker. Zero entrance animation on a 20×/day surface. (Scarr et al.; UXmatters; Rauno frequency & novelty.)
8. **Aggregate the normal, itemize the abnormal.** N healthy automations = one quiet line with a count; the one blocked item gets a row. Summarize what's collapsed ("4 running, all healthy") — never silently hide. (GitHub merge box; COGA + ADHD out-of-sight-out-of-mind.)
9. **Snooze must have a guaranteed wake** — time OR new activity, whichever first; asks self-clear when reality resolves them; empty runs auto-archive. (Linear snooze; Superhuman self-clearing reminders; Codex "runs with no results are automatically archived".)
10. **Item header = the proposed action with its arguments** ("Publish the release note", not "Agent paused"); approval buttons state exactly what they authorize; decline asks why. (LangChain Agent Inbox; Edilec approval UX; Linear decline-with-comment.)
11. **Rows carry provenance and receipts.** Originating intent rides with each run ("you asked this morning"); done = outcome-linked ("brief saved to Memory · +12 −3"), not "agent stopped". (NN/g complex apps #4; HatchWorks action receipts; Devin PR-linked status.)
12. **Time made concrete, mono, tabular.** "running 4m · last event 40s ago", "waiting 2h" — durations not timestamps, no seconds precision. (ADHD time-blindness; Few pitfall #3; Berkeley-Mono lesson: mono for values, not prose.)
13. **The earned zero state is the product's reward.** Reachable, celebrated within the design language (depth/awe, not confetti), different each time. (Superhuman zero images; Todoist Zero; "categorization isn't progress — resolved asks are".)
14. **Capture lives on top of the status surface** and is zero-decision: one field, lands in inbox, NL-parsed with live token highlight. (GitHub agents panel; Akiflow ⌘E; Todoist Quick Add.)

## Kill-list for the current draft (evidence-driven, direction-independent)

- Shimmer on "working" → replace with changing step-phrase + mono elapsed + last-activity age.
- Toast on completion/routing where avoidable → new-since-last-look band + receipts.
- Equal-weight sections for Running/Scheduled/Outcomes → aggregate lines, itemize exceptions only.
- Ticking per-second timers → minute-granularity; batch redraws.
- "Recent outcomes" as a permanent co-equal region → a quiet receipts line/digest.
- Snooze without a wake condition.

## The open direction choice (user picks)

A. **Dark-cockpit ledger** — one lane; answer-headline; only exceptions itemized; everything healthy is one faint aggregate line each; reward state when dark.
B. **Triage deck** — the focus card IS the queue head with 3 keyed verbs + auto-advance ("2 of 3"); ambient strip below; process-to-zero mechanic.
C. **Disciplined instrument panel** — current fixed-viewport two-column layout, kill-list applied, needs-you itemized, ambient tiers aggregated.

A and B compose naturally (deck for the needs-me moment on a dark-cockpit baseline).

## 2026-07-20 (evening) — deck built; settled mechanics

A+B implemented in board-home.html/css/js + a `motion.deck` primitive in board-motion.js.
Second research fan-out (ADHD motion/reward, eye-candy craft, triage-deck mechanics) settled these:

- **Keys**: 1/2/3 positional, printed on the buttons; verb labels vary by kind (review: Approve/Reject…/Later;
  question: Answer…/Open/Later; fyi: Got it-class/Open/Later). Enter = 1, H = Later, Z = undo. Slot 2's io/redirect
  opens an inline one-line input in the SAME foot slot — never a modal.
- **Counter counts down**: "1 of 3" → "1 of 2" → "last one" → zero. Get-to-zero is the progress system; no scores.
- **Direction is semantic and constant**: affirmative exits right, decline exits left, set-aside drops down.
  Exit 180ms accelerate; next card rises on the peek spring (460ms `linear()`); content fades in with it.
  Slivers = tonal-receding edges (color-mix toward paper), never content previews.
- **One focal mover per beat**: card owns the first beat; headline/strips dissolve in place 140ms later.
- **Arrivals append to the tail**: only the denominator and headline count roll; the head card never moves.
- **Undo is physical**: Z returns the departed card along its exit path and reverts ledger/tally. Depth 1.
- **Earned zero**: last exit reveals a seeded generative instrument mark (4 families × per-day seed — horizon,
  ruler, arcs, field; monochrome hairlines) + tally recap. One-shot draw-on; static on load-into-zero. No confetti.
- **Ambient strips** (Out working / Asleep / Landed today / Set aside) are one-line aggregates at the bottom;
  expansion is an overlay popover above the strip (translate+blur, origin bottom) — the deck never reflows.
- **Capacity line** appears only past a usual day ("More than a usual day — still one at a time.").
- **Voice**: no overdue/missed/failed-you; ages are data ("waiting 6 days"); agents "out working / back — brought you".
