# Mission Control for arden

## Product thesis

Mission Control is the trust layer for personal agent delegation: a global cockpit that shows what Arden is doing on the user's behalf, what needs human judgment, and how completed work turns into replies, memory, follow-ups, artifacts, or automations.

## MVP wedge

Do **not** start with traces, analytics, charts, or enterprise governance.

Start with one job:

> I have arden doing several things. Show me what is happening, what needs me, and let me intervene without hunting through chats.

The MVP should be an attention router:

1. **Needs You** — approvals, blockers, risky actions, auth issues, failed automations.
2. **Running Now** — active goals, workflows, background agents, loops, subagents.
3. **Scheduled / Recurring** — loops and automations with next run, last result, pause/resume.
4. **Recent Outcomes** — completed results that need handoff, memory, follow-up, or dismissal.

## Recommended first UI

Build the calm overview first, not the dense ops console.

Default IA:

```txt
Mission Control
├─ Overview
├─ Needs You
├─ Running
├─ Loops
└─ Outcomes
```

Default card grouping should be by **attention**, not implementation type. Users should not need to know whether something is a workflow, background task, subagent, automation, or loop.

## Core read model

```ts
type SupervisionKind =
  | "approval"
  | "objective"
  | "run"
  | "loop"
  | "artifact"
  | "recent";

type AttentionLevel =
  | "none"
  | "passive"
  | "needs_user"
  | "critical";

interface SupervisionItem {
  id: string;
  kind: SupervisionKind;
  title: string;
  subtitle?: string;
  detail?: string;
  status: string;
  attention: AttentionLevel;
  updatedAt?: number;
  origin?: {
    kind: "session" | "automation" | "background_agent" | "workflow" | "memory";
    id: string;
    label?: string;
  };
  trust?: {
    label:
      | "Observe only"
      | "Can act with approval"
      | "Can act automatically"
      | "Blocked"
      | "Needs sign-in";
    risk?: "low" | "medium" | "high";
  };
  actions: Array<
    | "open"
    | "approve"
    | "deny"
    | "guide"
    | "stop"
    | "retry"
    | "pause"
    | "resume"
    | "pin"
    | "route"
    | "dismiss"
    | "convert_to_automation"
  >;
}
```

## Mockups

- `mission-control-overview-a.html` — calm default MVP overview.
- `mission-control-ops-console-b.html` — dense power-user / air-traffic-control mode.
- `mission-detail-timeline-c.html` — one mission's timeline, artifacts, evidence, decisions.
- `intervention-inbox-d.html` — approval/blocker inbox; likely the strongest MVP slice.

## Product rules

- Lead with **Needs You**, not activity feeds.
- Show evidence for completed work; no fake green-check autonomy theater.
- Every visible card should have a next action.
- Compress tool noise into decisions, deltas, incidents, outcomes, and artifacts.
- Keep raw traces as drill-down, never the default UI.
- Dismissing a toast must not dismiss the underlying supervision item.

## Suggested implementation path

1. Define a frontend `SupervisionItem` projection from existing goals, loops, workflows, background tasks, approvals, automations, and recent results.
2. Evolve `apps/desktop/src/components/AgentRightSidebar.tsx` into a small Agent Hub / Work sidebar.
3. Add a dedicated Mission Control workspace with Overview / Needs You / Running / Loops / Outcomes.
4. Build Intervention Inbox after the overview.
5. Add mission detail timeline only once artifacts/evidence are clear enough to be useful.
