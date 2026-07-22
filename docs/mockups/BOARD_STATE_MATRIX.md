# Board state coverage matrix

`DESIGN.md` is authoritative. This file is a verification map: it records where runtime and pressure states must be demonstrated without creating new design rules.

| Surface | Required states | Landing and recovery behavior |
| --- | --- | --- |
| Home | loading, empty, partial data, error, offline, reconnecting, auth required, long content | Preserve universal capture; put failures in Activity; successful handling removes the ask and updates the quiet recap. |
| Chat | loading, empty, interrupted streaming, queued messages, compaction, approval allowed, approval denied, approval expired, cancelled run, stale run, source failure, offline, reconnecting, long content | Keep the answer readable; approvals remain actionable; failures stay in Activity; citations stay in Sources. |
| Automations | never run, running, completed, failed, paused, unsafe trigger, unavailable integration, disabled, destructive confirmation, long content | Preserve explicit Save and Run boundaries; a result opens in the result peek; failures identify the blocked dependency. |
| Memory | loading, empty, partial data, error, offline, stale, destructive confirmation, long content | Preserve the source record; review writes in the diff sheet; success returns to the edited record. |
| Settings | loading, empty, error, offline, reconnecting, auth required, disabled, destructive confirmation, long content | Keep account × service identity visible; success returns to the affected provider, integration, or server row. |
| Area Room | loading, empty, partial data, error, auth required, awaiting input, awaiting approval, running, completed, failed, interrupted, long content | Resolved asks leave Needs you and appear in Outcomes or Activity. |
| Agent Hub | loading, empty, partial data, error, running, awaiting approval, awaiting input, auth required, completed, failed, cancelled, interrupted, stale, long content | Parent-child navigation remains available; completed work offers a route back to its result or source. |
| System overlays | default, loading, empty, error, disabled, inert, destructive confirmation, stacked sheets, long content | Escape closes only the top surface; focus returns to its trigger; success lands on the underlying affected object. |

## Canonical runtime words

Use visible text: `running`, `awaiting approval`, `awaiting input`, `auth required`, `completed`, `failed`, `cancelled`, `interrupted`, and `stale`. Page code may map raw enums to these words, but must not invent parallel aliases.

## Pressure cases applied to every row

- Keyboard only and visible focus.
- Reduced motion with identical state outcomes.
- Light and dark appearance.
- Compact and narrow width, short height, and 200% zoom.
- Long unbroken identifiers and long translated copy.
- Offline during an in-flight action, then reconnecting.
- A disabled primary action with an adjacent reason.
- Destructive confirmation naming the exact target and consequence.
