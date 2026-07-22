# Outcome-Owning Custodians

Date: 2026-07-10  
Status: approved for implementation by the user on `feat/area-custodians`

## Goal

Turn Custodians from periodic monitors into delegates that continuously advance
Area outcomes. The user should not need to remember what to ask next, manually
translate conversations into plans, or repeatedly check whether work moved.

The primary success metric is: **how many open loops advanced or closed without
the user formulating another prompt?**

## Product contract

Each delegated Area has durable structured work state:

- **Outcomes** describe an achieved world state, not a monitoring topic.
- **Loops** group the ongoing work needed to reach an outcome.
- **Next actions** are concrete steps the Custodian can execute now.
- **Blockers** name the missing judgment, access, or external event.
- **Evidence** records what changed and why work is considered advanced or done.

Custodians infer this state from Area pages, conversations, and events. The user
does not complete an intake form. Inferred state is visible and directly
editable in the Area room.

## Research-derived UX direction

Pulse's strongest UX was a finite, quickly scanned proactive batch with simple
steering. Its retirement into Scheduled Tasks is the more important lesson:
proactive information is most useful when it is personalized, action-oriented,
steerable, and attached to durable work. Motion and Reclaim reduce cognitive
load by continuously maintaining a plan after inputs change. Lindy's useful
unit is a completed loop, such as meeting preparation through follow-up, not a
notification about the loop.

arden therefore keeps the finite Home ending but changes its content from a
notification queue into a work brief.

## Canonical data model

Area work is stored in SQLite by a dedicated `AreaWorkStore`; the Area page is
the human-readable narrative projection, not the source of truth for status.

### Outcome

- stable ID and model-supplied stable key scoped to `area_id`;
- title and explicit success criteria;
- status: `active`, `paused`, `completed`, or `cancelled`;
- priority from 1–5;
- source: inferred, user, or migration;
- created, updated, and completed timestamps.

### Work item

- stable ID and key scoped to `area_id`;
- optional parent outcome;
- kind: `loop`, `action`, or `blocker`;
- status: `active`, `in_progress`, `completed`, or `cancelled`;
- owner: `custodian`, `user`, or `external`;
- concrete text, optional due/next-at time, and timestamps.

### Work event

Append-only evidence: the affected outcome/item, originating run, event type,
summary, source references, and timestamp. Events power the “Done for you”
brief and preserve why the system believes work changed.

Foreign keys cascade with Area deletion only; normal Area archive preserves all
work. Stable keys are unique within an Area. Writes use transactions.

## Reconciliation boundary

The model does not mutate canonical work state piecemeal. Its final structured
`AreaCustodianReport` contains validated operations:

- create/update/complete/cancel an outcome;
- create/update/complete/cancel a work item;
- append evidence;
- nominate durable asks;
- report progress and choose the next check.

The server applies the complete report atomically only after a successful run.
A malformed or interrupted run changes no work state. Existing unresolved work
is never removed because the model omitted it; retirement requires an explicit
operation.

User edits use typed API operations and are authoritative. A later agent run
may advance them but may not silently reopen completed or cancelled user work.

## Execution loop

Before each run, the Custodian receives the current structured outcomes and
work items alongside the Area page and wake events. Its standing instruction is:

1. reconcile new evidence with existing outcomes;
2. choose the highest-leverage unblocked action;
3. execute multiple useful steps in the current run;
4. keep working until it makes material progress, completes the action, or is
   genuinely blocked;
5. commit work-state operations and evidence in the final report;
6. ask the user only for judgment or access it cannot infer.

One run may contain many tool calls. A report can also request a short
continuation check when useful work remains. Continuations obey pause and the
autonomous run cap, but are allowed below the normal attention interval so an
active work burst does not wait hours. Waiting on an external event uses a
specific next-at time instead of polling aggressively.

“Quiet” means no material progress and no executable work, not merely “no ask.”
Progress resets cadence decay; blocked or waiting work backs off.

## Autonomy

Observe mode may research, reconcile work, and update the Area page. Act mode
may additionally run child automations owned by that Area. The outcome engine
must not route work through arbitrary global automations.

This design deliberately leaves generic external side effects for a later
standing-grants layer. It does not block usefulness today: research, synthesis,
planning, page maintenance, and Area-owned workflows can all advance without
the user.

## Home: finite Chief-of-Staff brief

Home has three bounded sections:

1. **Done for you** — material completions from recent work events, expiring
   after 72 hours; no acknowledgement required.
2. **In progress** — at most one highest-priority active item per Area, capped
   globally; opens the Area room rather than creating another task surface.
3. **Needs you** — the existing durable question/review asks, capped and ranked.

The empty state remains a real ending: “That’s it for today.” Informational
notifications do not outrank blockers. Home never becomes a chronological feed.

## Area room

The room adds a compact Work section above narrative page material:

- primary active outcome with progress state;
- current action and blocker;
- collapsed remaining outcomes/loops;
- inline create, edit, complete, pause, resume, and cancel operations;
- evidence detail through the existing Custodian channel/audit trail.

The UI favors current work over project-management chrome. There is no kanban,
manual dependency graph, or required planning ceremony.

## Responsiveness

Area chat activity, page edits, ask replies/resolutions, child-automation
completion, and supported connector events wake the Custodian. Events coalesce,
but they remain durable until a run begins. The run reconciles them against work
state rather than treating each event as an isolated notification.

After the server commits a work report it emits `areas_changed`; Home and an
open room refresh from the same canonical projection.

## Failure behavior

- Failed or malformed runs preserve prior work exactly.
- A report referencing an unknown outcome/item fails validation and is not
  partially applied.
- Duplicate run delivery is idempotent by `run_ref`.
- Conflicting user and agent writes use optimistic `updated_at` checks; user
  edits win and the next run reconciles fresh state.
- A continuation that hits pause or budget remains visible as pending work and
  falls back to the normal next check.

## Verification

Completion requires tests proving:

- schema constraints, Area isolation, atomic application, idempotent run replay,
  archive preservation, and user-edit conflict behavior;
- inferred work survives quiet/malformed runs and only explicit operations
  complete or cancel it;
- progress-aware cadence and bounded short continuation scheduling;
- Home's Done/In progress/Needs you caps and ordering;
- Area-room editing reconciles canonical desktop state;
- full server tests, repo-wide Ruff, desktop tests, typecheck, lint, and build.

