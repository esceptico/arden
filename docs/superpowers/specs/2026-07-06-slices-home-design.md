# Slices — home as entrypoint, life domains as rooms

Date: 2026-07-06
Status: draft, awaiting review
Supersedes: 2026-07-06-home-screen-design.md (briefing/timeline concept — rejected)

## Thesis

Drive from entropy reduction. A **slice** is a life domain (Dex, ntrp,
O-1A, Aside, Health, United States) that persists for months and
accumulates chats, memory, automations, and open loops. Each slice
*compresses* its domain: it absorbs noise and emits either silence or
**one focused ask**. Home is then simple for a principled reason — it is
an entrypoint (a door, not a view):

- one hero input that addresses anything (ask → chat, topic → page,
  "run X" → automation, task → background agent),
- a **focus set**: the top ask from each slice that currently needs the
  user (≤ ~4 lines; quiet slices contribute nothing),
- a **slices strip**: live slices marked, quiet ones dimmed.

Nothing else. Figma reference: file n0nHncW83UnnR0GufEF6u0, frames
"Slices / Home — entrypoint" and "Slices / O-1A room".

Rejected on the way here (do not resurrect): status dashboard, calm
briefing/ledger, timeline tape, triage queue — all "views of ntrp's
state". The user's own memory already states the direction: "personal
AI control center … command palette as the central interaction model"
(active-work.md).

## Placement

Home = the no-session state of the main pane (replaces "What's on your
mind?"). Sidebar unchanged. Type-anywhere already focuses the composer;
the hero input IS the composer, promoted. Slice rooms are main-pane
views navigated from the strip / focus rows / ⌘K.

## The slice entity

Slices map 1:1 to memory **topic pages** (`~/.ntrp/memory/topics/*.md`)
— they already exist, with "Open loops" sections, updated dates, and
related links. A slice adds runtime linkage on top:

```
Slice:
  key: str                 # topic slug (o-1a, dex, health, …)
  page: topic page         # the memory source of truth
  sessions: [session_id]   # chats tagged to this slice
  automations: [id]        # owned automations
  asks: [Ask]              # what currently needs the user
  activity: [event]        # agent runs, page edits, chats (derived)

Ask:
  id, slice_key
  text: str                # one sentence, user-facing
  kind: review | decide | act | drift
  source: open_loop | approval | run_result | automation_failure
  actions: [verb + ref]    # open_memo, open_session, approve, retry, plan
  state: active | done | dismissed | snoozed
```

`drift` is the distinctive kind: a mismatch between a commitment and
reality ("fellowship app says 'currently working' — 0h in 3 weeks").
Mechanical derivation covers the other kinds; drift is the slice
agent's judgment (Layer 2).

## Slice rooms

Room = header (name, autonomy chip, last agent activity + cost) →
attention card (the current ask, with actions) → open loops (the topic
page section, live: status per loop, agent-completed ones checked) →
activity log → related slices → composer scoped to the slice (messages
start/continue a slice-tagged session).

## Focus set selection

Each slice nominates at most ONE ask (highest kind-priority:
approval-blocking > drift > review > act). Home shows all nominations,
capped at 4 by recency of the slice's page update. No feeds, no
calendar, no status rows — an item appears only if a slice is asking.

## Scope: slice agents are core, not a phase

Per user decision, the standing agent per slice ships from the start —
without it, slices are a prettier index; the compression IS the product.
Build order still matters (structure before judgment), but both land in
the initial version:

**Layer 1 — structure (build first).**
- Server: `Slice` projection from topic pages + session tags +
  automation ownership; `GET /slices`, `GET /slices/{key}`;
  mechanically derived asks: pending approvals (mapped via session),
  failed automation runs, agent outputs awaiting review, open loops
  marked as needing the user. Dismiss/snooze persisted.
- Session→slice tagging: reuse the existing project mechanism if it
  fits (projects already group sessions in the sidebar) — investigate
  before building a parallel concept; otherwise `slice_key` on sessions
  with LLM auto-tagging at session-title time.
- Desktop: `features/home/` (entrypoint) + `features/slices/` (room).
  Hero input = existing composer + ⌘K routing semantics.

**Layer 2 — the slice agent (the core).**
- One standing agent per live slice: the slice's topic page + linked
  memory as its working context, tools scoped to its domain, runs
  triggered by schedule AND events (feed updates, finished runs, page
  edits touching its entities) — reuse the automation/schedule and
  event-bus machinery rather than a new runtime.
- Its job per run: absorb what changed in its domain, update the topic
  page (open loops, activity), and decide: silence, or nominate ONE
  ask. Drift detection (commitment vs reality) lives here.
- Autonomy contract v0 is deliberately small: `observe` (may only read
  + write memory + ask) vs `act` (may also run its automations and
  spawn workflows; anything irreversible still goes through the
  existing approval flow). The room's chip reflects and edits it.
- Accountability: every ask traces to the run and sources that
  produced it (provenance in the room's activity log).

Deferred (explicitly): slice-to-slice negotiation, autonomy dials finer
than observe/act, auto-widening trust from track record, mobile.

## Open questions (blocking the plan, need user input)

1. Slice set curation: auto-create from every topic page, or explicit
   user promotion of a page to a slice? (19 topic pages exist; ~6 are
   life slices, the rest are reference topics like `letta.md`.)
2. Does the hero input replace the bottom composer on Home entirely
   (as drafted), or does the bottom composer stay for muscle memory?

## UI reuse from ~/src/interaction-lab

Port faithfully per the established lab→ntrp idiom map; don't re-derive
tuned values.

- **Combobox** → the hero input. "Searchable list, origin-grow surface,
  value commits via text swap" is exactly the entrypoint's routing UX:
  type → suggestions across slices/pages/automations/actions → commit.
- **FocusProgress** → a slice whose agent is running: the ask card /
  strip chip carries progress on its border, content sharpens out of
  blur as the run completes. The most on-thesis piece in the lab.
- **TextSwap** (3-phase, also proven as FieldSwap in InstrumentRuler) →
  a focus row's ask retiring and the slice's next ask taking its place;
  room attention-card content changes.
- **BorderCharge** (hold-to-confirm, canon) → granting a slice `act`
  autonomy; bulk-dismiss.
- **SuccessCheck** → in-place receipt when an ask resolves, before the
  ROW_EXIT pose removes the row.
- **NumberFlow** → live run cost/tokens in the room header.
- **InlineDisclosure** → open-loop rows and focus rows expanding in
  place for detail/provenance without navigation.
- **PopoverForm** → snooze picker; autonomy-contract editor anchored to
  the room's chip.
- **TravelingHighlight** (component) → slices strip hover.
- Not reused: InstrumentRuler (rejected concept), RailNav (already
  ported as ChatRail).

## Verification

- pytest: slice projection (page parsing, ask derivation per source,
  focus-set nomination/cap, dismiss/snooze).
- bun test: store slice, focus-set rendering, routing from input.
- Full desktop gate + preview-harness walkthrough with the user's real
  memory (the screens above were already grounded in it).
