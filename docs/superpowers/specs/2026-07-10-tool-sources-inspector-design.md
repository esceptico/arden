# Tool Sources and Inspector — Design

**Date:** 2026-07-10
**Status:** Design approved; pending written-spec review
**Scope:** Add explicit tool-result provenance, preserve it across live streaming
and history reloads, and expose it in the existing right dock.

This supersedes only the “strictly live execution trace” limitation in the
2026-06-03 right-sidebar spec. The existing Activity content remains unchanged;
Sources becomes a separate inspector tab.

## Goal

Show the resources whose content entered the agent’s context without guessing
from tool arguments or recursively scanning arbitrary JSON.

A source is a resource returned by a read tool with content or a snippet the
model could use. This includes search results. It does not claim that every
resource supports a specific sentence; claim-level citation annotations are out
of scope.

## Chosen approach

Use a typed, plural provenance contract at the tool boundary and a tabbed right
inspector.

Alternatives rejected:

- **Client-side JSON/URL scanning:** small diff, but brittle, unsafe, and unable
  to distinguish provenance from unrelated URLs.
- **A separate citation ledger with answer-span annotations:** strongest
  attribution, but requires model/API citation support and a larger persistence
  model. Defer until Arden needs claim-level citations.

## Source contract

Add an immutable `ToolSourceRef` and plural `ToolResult.source_refs`:

```python
@dataclass(frozen=True)
class ToolSourceRef:
    provider: str       # web, slack, gmail, calendar, file, or MCP server name
    kind: str           # page, message, email, event, file, resource
    ref: str            # stable provider id or URI
    title: str          # normalized display title
    url: str | None = None
```

Rules:

- Dedupe by `(provider, ref)`, preserving first-seen order.
- `url` is optional and only stores validated, user-openable HTTP(S) URLs.
- Reject empty refs and HTTP(S) refs containing username/password. If an opaque
  ref has a credential-bearing URL title, replace that title with the ref. Cap
  each tool result at 50 refs; cap provider/kind at 64 Unicode code points, ref
  at 2048, title at 256, and URL at 4096.
- Native web URLs containing userinfo, a query, or a fragment use
  `url-sha256:<digest>` as a non-secret ref and omit `url`. Query-free public
  HTTP(S) URLs may remain normal refs/links. Do not guess secret parameter names.
- Keep opaque ids in `ref`, never in `url`.
- Source metadata is not appended to model-visible tool text.
- Replace legacy singular `source_ref` and its private call sites atomically;
  no compatibility layer or database migration is required.
- Do not reuse memory’s `SourceRef`; that type has different retention and trust
  semantics.

## Extraction

Extraction happens where the result shape is known.

### MCP

`mcp/results.py` receives the MCP server and raw tool name, then extracts only:

- typed `ResourceLink` and embedded-resource URIs;
- exact `search` output: top-level `structuredContent.results[]` entries with
  string `id`, string `title`, and an optional string `url`;
- exact `fetch` output: a top-level object with string `id`, string `title`,
  non-blank string `text`, and an optional string `url`.

Arbitrary nested objects are not traversed. A future non-standard MCP provider
must register an explicit extractor keyed by server/tool; it does not weaken the
generic contract.

### Native tools

Native adapters construct refs directly:

- `web_search`: one page ref per returned result; `web_fetch`: the fetched page.
- Slack search/channel/thread: one message/thread ref per returned item. Use an
  existing permalink when available; otherwise keep a non-clickable stable ref.
- Gmail search/read: one email ref per returned message; URL remains optional.
- Calendar list/search/read: one event ref, using `html_link` when present.
- `read_file`: one local-file ref.

Memory and transcript-search results remain separate future inspector surfaces.

## Data flow and durability

```text
tool adapter
  -> ToolResult.source_refs
  -> ToolCompleted.source_refs
  -> TOOL_CALL_RESULT.source_refs
  -> ActivityItem.sourceRefs
  -> turn aggregation
  -> Sources inspector
```

For reloads, `persistable_tool_result_data()` retains a compact
`source_refs` payload beside `child_agent`. The saved tool message and history
response round-trip the same refs. Raw MCP `structuredContent`, `_meta`, matches,
and arbitrary result data remain live-only. Durable tool events allowlist
child-agent metadata, workflow identity, usage/cost, and HTML-widget fields
while preserving top-level `source_refs`.

`session_events.event_json` and `session_messages.message_json` are schemaless
JSON, so no database migration is required. Existing large-result blob/offload
behavior stays unchanged; truncation and offload helpers must preserve refs.

The run-level collection can continue feeding internal completion events, but
the per-tool refs are the UI source of truth because they retain origin-call
association.

## Desktop model

Add `SourceRef` to the desktop contract and `sourceRefs?: SourceRef[]` to
`ActivityItem`. Live and history projections normalize untrusted payloads through
one helper. Turn aggregation:

- walks activity items belonging to the turn;
- dedupes by `(provider, ref)`;
- preserves first-seen order;
- retains the originating tool-call id for “Show call”.

Inspector UI state is ephemeral:

- active tab: `activity | sources`;
- scoped turn id for Sources;
- opening Sources manually selects the latest source-bearing turn;
- clicking an answer footer selects that exact turn;
- changing sessions resets the scope.

The panel does not follow scroll position in v1.

Segments carry an internal `turnId` separately from visible `userId`. Hidden
meta-user boundaries therefore own their visible assistant/activity children
without rendering a user row; status/error segments remain unkeyed.

A monotonic `sourceRefsRevision` invalidates source derivation on source-bearing
activity changes, transcript/history structure changes, session switches, and
edit truncation. Source components subscribe to that primitive and read the
message map only during memoized derivation, so text deltas do not rescan it.

## UI

Reuse the existing resizable right dock and shared `Tabs` primitive.

### Header

Replace the `Active` caption with compact underline tabs:

```text
Activity    Sources
```

Activity renders the current approvals/todos/agents/workflows body unchanged.
Sources uses the same solid panel, scrollbar, resize handle, and reduced-motion
behavior.

### Sources panel

Each row has:

- provider/category icon;
- source title;
- secondary provider, hostname, channel, or resource label;
- primary open action when a safe URL exists;
- secondary “Show call” action that opens/focuses the originating tool call.

Rows use padding and soft hover separation, not individual cards or full-width
dividers. Do not fetch favicons. Empty state: “No sources for this answer.”

### Answer footer

Final assistant messages with sources show a quiet `N sources` action between
Markdown and message actions. It opens the dock, selects Sources, and scopes it
to that answer. No footer is rendered for zero sources.

Remove the current `stepSources()` input-URL heuristic and its non-interactive
domain chips. Provenance belongs in the inspector.

## Error and safety behavior

- Invalid refs are dropped without failing the tool call.
- Unsafe/non-openable URIs can remain as stable refs but never become clickable;
  desktop anchors additionally require WHATWG `new URL()` HTTP(S) validation,
  a hostname, and no credentials.
- Never persist auth tokens or Slack private-download URLs.
- Source counts reflect normalized, deduped refs.
- The panel says “Sources”, not “Citations”; no inline claim markers are added.
- Older sessions naturally show no sources unless their saved tool messages
  contain the new metadata.

## Implementation seams

Server:

- `agent/types/tools.py`, `agent/types/events.py`, `agent/tools/runner.py`
- `core/tool_executor.py`, `core/tool_result_data.py`
- `mcp/results.py`, `mcp/tool.py`
- native integration tool adapters
- `events/sse.py`, `agent/tools/dispatch.py`, `server/routers/session.py`

Desktop:

- `api/events.ts`, `api/chat.ts`, `stores/types.ts`
- one source normalization/aggregation helper
- live/history transcript projection
- `AgentRightSidebar.tsx`, new `SourcesPanel.tsx`
- `TurnGroup.tsx`, `Message.tsx`, `AssistantMessage.tsx`
- remove `stepSources()` from `operationLabel.ts` and `ActivityRows.tsx`

## Verification

Server tests:

- canonical MCP search/fetch extraction;
- `ResourceLink` extraction;
- arbitrary nested URLs ignored;
- empty/unsafe URLs not clickable;
- native web/Slack/calendar/Gmail/file producers;
- truncation and offload preserve refs;
- SSE serialization/replay;
- tool-message persistence and history reload;
- source refs coexist with `child_agent` data.

Desktop tests:

- live and history projection normalize refs identically;
- turn aggregation ordering and dedupe;
- footer count and exact-turn scoping;
- inspector tab/empty/openable/non-openable row behavior;
- “Show call” focuses the originating activity;
- zero-source turns render no footer;
- existing Activity tab and tab keyboard behavior remain intact.

## Non-goals

- Claim-level or inline citations.
- Recursive JSON inspection.
- A new database table or source-retention policy.
- A new modal, drawer, or shell dependency.
- Scroll-following source scope.
- Memory-used/context/trace inspector tabs.
- Backfilling provenance into old sessions.
