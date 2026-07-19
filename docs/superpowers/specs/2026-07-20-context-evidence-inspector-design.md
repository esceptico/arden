# Context and Evidence Inspector Design

## Goal

Make completed agent work inspectable without cluttering the transcript: show a compact, collapsible proof summary under meaningful answers and a per-turn Context inspector backed by the run sidecars already persisted by the server.

## Constraints

- Reuse `ContextManifest`, `RunEvidence`, `ToolOutcome`, the existing right inspector, and the existing Sources panel.
- Do not add another persistence model, event stream, tracing system, dependency, or dashboard.
- Never expose context contents or private reasoning. Show manifest metadata and operational evidence only.
- Render nothing under an answer when it has no meaningful outcome evidence.
- Keep existing Sources behavior and user-owned desktop changes intact.

## User Experience

The right inspector gains a third tab: `Activity | Sources | Context`.

Completed answers gain a collapsed proof row only when their turn contains a source or a structured tool outcome with an effect, receipt, verification, limitation, or non-success status. The collapsed row uses honest language:

- `Evidence recorded` when durable evidence exists without a limitation.
- `Needs attention` when any outcome is failed, denied, or uncertain.

The row includes compact counts such as `2 actions · 1 check · 3 sources`. Expanding it shows a bounded list of actions, checks, and limitations derived from the already-projected turn activity. A single `Inspect context` action opens the right inspector for that turn.

The Context tab shows:

1. **Context used** — content type, source/ref, freshness, selection reason, and human-readable size.
2. **Outcome evidence** — approvals, effects, receipts, verification checks, and limitations.
3. **Sources** — a count and `View sources` action that opens the existing Sources tab for the same turn instead of duplicating its renderer.

If a manually opened Context tab has no explicit turn, it inspects the latest visible turn. Missing sidecars, old sessions, and runs without evidence use a small empty state; they do not produce transcript cards or errors.

## Data Flow

### Inline summary

The desktop derives a `TurnProofSummary` from the selected turn's existing `ActivityItem.outcome` and `sourceRefs`. This is synchronous and local, so opening a long transcript does not issue one request per message.

### Full inspector

The desktop requests one read-only endpoint only while the Context tab is visible:

```text
GET /sessions/{session_id}/turns/{turn_id}/inspector
```

The server resolves the turn to a run without a schema migration:

1. initiating user turn: `chat_runs.client_id = turn_id`;
2. queued/ingested user turn: `chat_queued_messages.client_id = turn_id`, then its `run_id`;
3. meta turn: `meta-user-{run_id}`.

The run must belong to the requested session. The endpoint then returns the existing run sidecar or `null` when no exact sidecar exists. It never falls back to another run.

## Boundaries

The API response uses explicit response models for manifest entries and evidence groups. The desktop still normalizes unknown JSON at its API boundary, caps rendered rows, and treats malformed optional fields as absent. Long refs, receipts, observations, and recovery actions use wrapping/truncation in the UI; raw context content is never returned.

No UI label claims verification merely because a receipt exists. Failed, denied, and uncertain outcomes stay visible and take precedence over positive counts.

## Components

- Server store: exact turn-to-run lookup using existing indexed records.
- Session router: nullable per-turn inspector endpoint.
- Desktop API: response types, normalization, and fetch function.
- Turn proof helper: pure aggregation from transcript activity.
- `ProofSummary`: collapsed-by-default answer footer with a bounded expanded body.
- `ContextPanel`: lazy full-sidecar renderer in the right inspector.
- Store: selected context turn and `openContextForTurn` action, parallel to Sources selection.

## Testing

- Store tests prove initiating, queued, meta, wrong-session, and missing turn resolution.
- Router tests prove exact nullable responses and no cross-session leakage.
- Pure desktop tests prove summary counts, attention precedence, empty suppression, and row caps.
- Component tests prove collapsed default, expansion, Context-tab navigation, empty/error states, and Sources handoff.
- Existing source-inspector, transcript projection, and right-sidebar tests remain green.

## Non-goals

- Context excerpts or private reasoning.
- Editing context/evidence.
- Comparing runs, filtering, search, export, telemetry, or dashboards.
- Replacing the Sources inspector or activity trace.
- Inventing model-authored proof claims.
