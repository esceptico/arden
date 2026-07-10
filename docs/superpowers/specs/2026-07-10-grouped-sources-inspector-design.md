# Grouped Sources Inspector — Design

**Date:** 2026-07-10
**Status:** Approved direction; pending written-spec review
**Scope:** Replace the flat per-turn source list with a compact, provider- and
action-grouped provenance view. Keep the existing source transport contract.

## Goal

Make a source-heavy research turn readable without losing inspectability. The
panel should answer:

- which providers contributed context;
- which source-producing actions ran;
- which resources each action returned;
- which exact tool call produced a resource.

Only calls with at least one normalized `ToolSourceRef` appear. Failed,
source-less, and unrelated tool calls remain in Activity rather than Sources.

## Generic contract

Grouping is driven only by existing typed data:

```text
ToolSourceRef.provider
  -> ActivityItem.kind / explicit presentation identity
    -> desktop SourceRef.toolCallId
      -> SourceRef
```

The UI must not branch on known tool names or recursively inspect tool JSON.
This makes the view work for native tools, MCP servers, and future tools as soon
as they return the normal `ToolSourceRef` contract.

Rules:

- Group first by normalized `provider`, preserving first-seen order.
- Within a provider, group calls by the originating activity item's explicit
  presentation identity, falling back to its raw tool id. This produces generic
  summaries such as `Search messages · 7 calls` without a tool-name allowlist.
- Within an action group, group refs by originating `toolCallId`.
- A call returning refs from multiple providers contributes only its matching
  refs to each provider group.
- Calls without a retained `ActivityItem` still render using their refs, with
  `Source action` as the fallback label.
- Prefer explicit tool presentation metadata (`displayName`, `noun`, `source`)
  for labels and icons. Fall back to the raw tool identifier and generic link
  icon; do not maintain provider/tool-name allowlists.
- Counts use normalized, deduplicated refs.

## Information hierarchy

Each provider becomes one calm section:

```text
Web                                      3 calls · 15 sources
  Search web                              2 calls · 14 sources  v
    query="Avanta clinic"                              10 sources  v
      Avanta Innovative Medical Center                  ↗
      Avanta Clinics                                    ↗
      Show 8 more
    query="Avanta dentist"                              4 sources  >
  Fetch page                               1 call · 1 source    v
    avanta.am
```

Provider headers stay visible and compact. Calls sharing an explicit
presentation identity are summarized as one action group. Expanding it shows
the individual source-producing calls, each containing:

- presentation label or raw tool id fallback;
- the existing compact call target/arguments;
- source count;
- disclosure control.

Expanding a call shows its source rows. Show at most five initially, followed
by `Show N more`. Source rows keep the current safe external-link action and
`Show call` behavior. One-source calls start expanded; multi-result calls start
collapsed.

This preserves the reference image's provider/action summary while retaining
the actual resources, which are the provenance contract's source of truth.

## Interaction and state

- Expansion state is local and ephemeral; it is not persisted with sessions.
- Keyboard and screen-reader users can expand every disclosure.
- Switching the selected turn recomputes groups and drops obsolete expansion
  keys.
- The existing answer footer and exact/latest-turn scoping stay unchanged.
- Activity remains the complete operational trace; Sources is the filtered
  provenance projection.

## Conversation-provided sources

Links or attachments supplied directly by the user should eventually appear in
a separate `Provided in conversation` section. They are out of this pass until
the message contract carries explicit link/attachment provenance. The desktop
must not scrape message text with URL or keyword heuristics.

## Error and fallback behavior

- Unknown providers and new MCP servers render automatically with their
  provider string and generic icon.
- Unknown tools render their raw identifier rather than disappearing.
- Unsafe or browser-invalid URLs remain non-clickable and retain `Show call`.
- Missing tool calls do not drop otherwise valid source refs.
- Empty groups are omitted; a source-less turn keeps the existing empty state.

## Implementation seams

- Add a pure grouping projection beside
  `features/sources/lib/sourceInspector.ts`.
- Rework only `features/sources/components/SourcesPanel.tsx` and small source
  presentation helpers.
- Reuse `ActivityItem`, `SourceRef`, provider icons, safe URL validation, and
  the current tool viewer action.
- Do not change server extraction, SSE, persistence, history, or source limits.

## Verification

Desktop tests cover:

- provider, action, and tool-call grouping with stable ordering;
- one call returning multiple providers;
- unknown MCP provider/tool fallback;
- source-less calls omitted;
- collapsed multi-result calls and five-row reveal limit;
- external link and `Show call` behavior;
- exact/latest turn changes resetting obsolete expansion state;
- existing empty state and answer footer behavior.

## Non-goals

- Claim-level citations.
- Hard-coded Web, Slack, Gmail, or MCP layouts.
- Recursive tool-result inspection.
- Mirroring every Activity event in Sources.
- Persisted expansion state.
- Parsing links from user message text.
