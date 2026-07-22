# Home screen — briefing + instrument timeline

Date: 2026-07-06
Status: draft, awaiting review

## Purpose

Arden is not a coding agent; its persistent value is the state of the user's
world (approvals, automations, agents, calendar, memory), and today all of
that hides in modals and sidebars orbiting the chat. Home makes that state
the primary surface — and it must be a screen the user *works from*, not a
dashboard they glance at. Every item is actionable; the screen is a queue
that empties toward "All clear."

Prior art that shaped this: ChatGPT Pulse (read-only cards; sunset — the
cautionary tale), Gemini Daily Brief (every item carries executable
actions; the survivor's shape).

## Placement

Home is **the no-session state of the main pane**. No new navigation, no
mode switch, no shell changes:

- App opens with no session selected → Home (replaces the current
  "What's on your mind?" empty state in `Chat.tsx`).
- "New session" in the sidebar → Home.
- Selecting a session → chat, as today. Sidebar unchanged.
- The composer stays where it is; typing starts a new chat (existing
  type-anywhere behavior already works here).

## Layout (top to bottom of the main pane)

1. **Header** — date line + headline. Headline states the queue size:
   "Two things need you." / "All clear." The screen's entire mood comes
   from this sentence.
2. **Needs-you cards** — priority-ordered, NOT time-ordered. At most ~3
   visible; overflow collapses behind "and 2 more". Sources: pending
   approvals, failed runs, agent outputs awaiting review (e.g. drafts).
   Each card: severity dot, title, one-line context, one primary inline
   action (Review / Retry / Open), and dismiss on hover. Acting on a card
   retires it with the ROW_EXIT pose.
3. **The tape** — an InstrumentRuler-grown vertical timeline filling the
   remaining height. Three-day window (Yesterday / Today / Tomorrow), ink
   now-mark, wheel pans, opens centered on now.
   - Past marks: automation runs, agent completions, memory writes.
   - Future marks: calendar events, scheduled automations.
   - Running agents: a live mark at the now-line.
   - Severity colors only on marks that need attention (amber/red);
     everything else monochrome per the design language.
4. **Inspector** — panel beside the tape (the study's mechanic: tape is
   the index, panel is the work surface). Opens on mark click or arrow-key
   navigation. Per-kind content and actions:
   - agent/automation run → result summary, "open session", retry
   - calendar event → details, "prepare me for this"
   - memory write → what changed, "open page" (MemoryModal deep-link)
   - scheduled automation → "run now" / "skip this run"
   - every inspector has a message box that starts a chat *about this
     item* (item context attached as the opening message)
5. **Composer** — unchanged, pinned at the bottom. Ask-anything.

Light-first, monochrome, tonal fills — per docs/design-language.md.

## Data model

New server-side aggregate; one endpoint, no client-side stitching.

```
BriefingItem:
  id: str
  kind: approval | run_failed | run_done | agent_output | calendar |
        scheduled | memory_write
  ts: datetime            # position on the tape
  duration_s: int | None  # events with extent (calendar, long runs)
  title: str
  detail: str             # one-line context
  severity: info | attention | error
  needs_you: bool         # True → also a card above the tape
  actions: list[str]      # verbs the client may render (approve, retry,
                          # open_session, open_page, run_now, skip)
  ref: dict               # session_id / run_id / page / event_id — what
                          # the verbs operate on
  state: active | done | dismissed | snoozed
```

- `GET /briefing?window=3d` → `{ items: [...] }`. Aggregates from
  existing stores: approval queue, automation run history, agent runs,
  calendar tool, schedule, memory journal. No new persistence for the
  items themselves — they are projections of existing records.
- `POST /briefing/{id}/dismiss` and `/snooze` — the only new persisted
  state, a small table/file of (item_id, state, until). Acting via an
  existing mechanism (approving, retrying) retires the item naturally on
  the next projection.
- Push: reuse the existing SSE bus — briefing-relevant events
  (approval created, run finished, …) already flow; the client refetches
  the projection on those events rather than maintaining item deltas.

Feeds are deferred from v1: they are volume, not obligations, and the
tape should start with things that are either scheduled or happened.

## Frontend structure

- New feature folder `apps/desktop/src/features/home/` (bulletproof
  structure like the other seven).
- `Home.tsx` rendered by Chat's no-session branch.
- `Tape.tsx` — port InstrumentRuler faithfully from
  `~/src/interaction-lab/src/studies/InstrumentRuler.tsx` (idiom map per
  the FF/lab porting convention), then grow: full-height, data-driven
  events, day boundaries labeled, keyboard nav kept (arrows between
  events, Escape closes inspector).
- `Inspector.tsx` — the side panel, per-kind bodies, FieldSwap content
  transition from the study.
- `NeedsYouCards.tsx` — card stack with inline actions.
- Store slice: `briefing` (items, selection, loading), actions for
  fetch/dismiss/snooze/act.

## Growing the study up (known hard parts)

The lab demo is 430x248 with 6 hardcoded events. Promotion requires:

- **Mark density**: overlapping marks cluster into one wider mark with a
  count; the inspector shows the cluster as a list. Threshold in px, not
  minutes, so zoom level decides.
- **Label collision**: the study only labels ticks; event labels appear
  only on hover/selection (readout chip), never persistently — density
  stays bounded.
- **Empty stretches**: acceptable; the tape's calm IS the product. No
  filler content.
- **Live now-mark**: advances on a minute timer; running-agent mark
  animates only while an agent is actually running (no ambient motion).

## v1 scope

- Home replaces the empty state; header + needs-you cards + tape +
  inspector + existing composer.
- Sources: approvals, automation runs (done/failed), agent runs +
  outputs, calendar, scheduled automations, memory writes.
- Dismiss + snooze on cards; inline approve/retry; open-session /
  open-page deep links; "chat about this" from the inspector.
- Deferred: feeds on the tape, drag-to-reschedule, multi-day zoom
  levels, mobile.

## Verification

- Backend: pytest over the projection (each source kind produces correct
  BriefingItem; dismiss/snooze filter; window bounds).
- Frontend: bun test for the store slice + projection rendering; the
  full gate (typecheck + lint + test + build) from apps/desktop.
- Usability check in the running app via the preview harness with seeded
  store state: cards act, tape pans, inspector opens, composer still
  starts chats, type-anywhere intact.
