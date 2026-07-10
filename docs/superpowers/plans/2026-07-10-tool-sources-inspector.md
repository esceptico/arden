# Tool Sources Inspector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Carry explicit tool provenance from MCP and native tools into durable chat events, then show per-turn sources in the existing desktop right inspector.

**Architecture:** Tools emit typed, plural source references. The server validates, bounds, transports, and persists those references without inspecting arbitrary nested JSON. The desktop projects the same contract from live SSE and history, aggregates sources by assistant turn, and exposes them through an `Activity | Sources` inspector plus a footer on source-bearing answers.

**Tech Stack:** Python 3.13, dataclasses, Pydantic, FastAPI/SSE, React 19, TypeScript, Zustand, Bun, Motion

## Global Constraints

- A source is an explicit resource returned by a read tool that contributed usable content; it is not a claim-level citation.
- Never recursively scan arbitrary tool JSON for URLs.
- Prefer canonical MCP `search`/`fetch` shapes and MCP resource content; support nonstandard servers only through named adapters.
- Normalize at the server boundary: trim all strings; require `provider`, `kind`, `ref`, and `title`; discard refs whose provider/kind exceed 64 characters or ref exceeds 2048; truncate titles to 256; keep URLs only up to 4096 characters when they use `http`/`https`, have a hostname, and contain no credentials; keep at most 50 references per tool call.
- Deduplicate by `(provider, ref)` while preserving first-seen order.
- Persist source references in existing schemaless tool-message data; do not add a database migration.
- Preserve unrelated user changes and keep the diff scoped to source provenance and its inspector.

---

### Task 1: Add the backend source contract and durable transport

**Files:**

- Modify: `apps/server/ntrp/agent/types/tools.py`
- Modify: `apps/server/ntrp/agent/types/events.py`
- Modify: `apps/server/ntrp/agent/tools/runner.py`
- Modify: `apps/server/ntrp/core/tool_executor.py`
- Modify: `apps/server/ntrp/core/tool_result_data.py`
- Modify: `apps/server/ntrp/agent/tools/dispatch.py`
- Modify: `apps/server/ntrp/events/sse.py`
- Modify: `apps/server/ntrp/server/state.py`
- Modify: `apps/server/ntrp/server/stream.py`
- Modify: `apps/server/ntrp/services/chat.py`
- Modify: `apps/server/ntrp/server/routers/session.py`
- Modify: `apps/server/ntrp/integrations/web/tools.py` (existing singular emitter only)
- Modify: `apps/server/ntrp/tools/files.py` (existing singular emitter only)
- Test: `apps/server/tests/test_tool_sources.py`
- Test: `apps/server/tests/test_streaming_events.py`
- Test: `apps/server/tests/test_web_tools.py` (existing singular assertion only)
- Test: `apps/server/tests/test_file_tools.py` (existing singular assertion only)

- [ ] **Step 1: Write failing source-contract tests**

Cover required fields, unsafe URL removal, length limits, 50-item cap, stable `(provider, ref)` deduplication, and dictionary round-tripping.

```python
refs = normalize_source_refs([
    ToolSourceRef(provider="slack", kind="message", ref="C1:1.0", title="Decision", url="https://example.slack.com/archives/C1/p1"),
    ToolSourceRef(provider="slack", kind="message", ref="C1:1.0", title="Duplicate"),
])
assert [ref.title for ref in refs] == ["Decision"]
```

- [ ] **Step 2: Run the focused tests and confirm they fail**

Run from `apps/server`:

```bash
uv run pytest tests/test_tool_sources.py tests/test_streaming_events.py -q
```

- [ ] **Step 3: Implement the typed source reference**

Add an immutable `ToolSourceRef` and a single normalization function in `agent/types/tools.py`.

```python
@dataclass(frozen=True, slots=True)
class ToolSourceRef:
    provider: str
    kind: str
    ref: str
    title: str
    url: str | None = None

    def to_dict(self) -> dict[str, str]: ...
```

Replace `ToolResult.source_ref` with `ToolResult.source_refs: tuple[ToolSourceRef, ...] = ()`.
Migrate the two pre-existing singular emitters to one-item tuples: `web_fetch` uses `(provider="web", kind="page")`, while `read_file` uses `(provider="filesystem", kind="file")`. Broader native extraction remains Task 2.

- [ ] **Step 4: Propagate references through execution and SSE**

Carry the tuple through `ToolCompleted`, the tool runner, truncation/offload paths, and `ToolCallResultEvent`. The public SSE payload uses `source_refs: list[dict[str, str]]`; do not hide it inside result content.

- [ ] **Step 5: Persist and restore compact references**

Extend `persistable_tool_result_data()` to retain only existing `child_agent` data plus normalized `source_refs`. Ensure dispatch saves those fields and session history returns them unchanged.

- [ ] **Step 6: Run focused tests and commit**

```bash
uv run pytest tests/test_tool_sources.py tests/test_streaming_events.py -q
uv run ruff check ntrp/agent/types/tools.py ntrp/agent/types/events.py ntrp/agent/tools/runner.py ntrp/core/tool_executor.py ntrp/core/tool_result_data.py ntrp/agent/tools/dispatch.py ntrp/events/sse.py ntrp/server/routers/session.py tests/test_tool_sources.py tests/test_streaming_events.py
git add apps/server
git commit -m "feat(server): transport tool source references"
```

---

### Task 2: Extract explicit sources from MCP and native tools

**Files:**

- Modify: `apps/server/ntrp/mcp/results.py`
- Modify: `apps/server/ntrp/mcp/tool.py`
- Modify: `apps/server/ntrp/integrations/web/tools.py`
- Modify: `apps/server/ntrp/integrations/slack/tools.py`
- Modify: `apps/server/ntrp/integrations/gmail/tools.py`
- Modify: `apps/server/ntrp/integrations/calendar/tools.py`
- Modify: `apps/server/ntrp/tools/files.py`
- Test: `apps/server/tests/test_mcp_results.py`
- Test: `apps/server/tests/test_mcp_tool.py`
- Test: `apps/server/tests/test_web_tools.py`
- Test: `apps/server/tests/test_file_tools.py`
- Test: `apps/server/tests/test_integration_tool_sources.py`

- [ ] **Step 1: Write failing MCP extraction tests**

Cover MCP `ResourceLink`, embedded resources, canonical top-level `structuredContent.results[]`, canonical fetched document fields, duplicates, missing URLs, and an arbitrary nested URL that must be ignored.

```python
result = CallToolResult(
    content=[],
    structuredContent={"wrapper": {"results": [{"id": "x", "title": "Hidden", "url": "https://ignored.test"}]}},
)
assert extract_mcp_source_refs(result, provider="demo", tool_name="search") == ()
```

- [ ] **Step 2: Run focused tests and confirm they fail**

```bash
uv run pytest tests/test_mcp_results.py tests/test_mcp_tool.py -q
```

- [ ] **Step 3: Implement explicit MCP extraction**

Change the adapter to accept provenance:

```python
call_tool_result_to_tool_result(result, *, provider: str, tool_name: str)
```

Extract only:

- MCP resource links and embedded resource URIs.
- Top-level canonical `results[]` items with `id`, `title`, and optional `url` for search tools.
- Top-level fetched document objects with explicit identity/title/content fields for fetch tools.

Do not recurse. Keep any server-specific mapping isolated and keyed by server/tool name.

- [ ] **Step 4: Write failing native-tool tests**

Cover web search result URLs, fetched pages, Slack message permalinks, Gmail message IDs, calendar `html_link`, and local file paths. Assert stable provider/kind/ref/title values.

- [ ] **Step 5: Add native source adapters**

- Web search: one ref per result; web fetch: one ref for the fetched URL.
- Slack: use returned message IDs and existing `metadata.permalink`; never make an extra API call only for provenance.
- Gmail: use message/thread IDs even when no safe browser URL exists.
- Calendar: use event IDs plus `html_link` when present.
- Files: use the normalized local path as `ref`, with no external URL.

- [ ] **Step 6: Run focused tests and commit**

```bash
uv run pytest tests/test_mcp_results.py tests/test_mcp_tool.py tests/test_web_tools.py tests/test_file_tools.py tests/test_integration_tool_sources.py -q
uv run ruff check ntrp/mcp ntrp/integrations/web/tools.py ntrp/integrations/slack/tools.py ntrp/integrations/gmail/tools.py ntrp/integrations/calendar/tools.py ntrp/tools/files.py tests/test_mcp_results.py tests/test_mcp_tool.py tests/test_web_tools.py tests/test_file_tools.py tests/test_integration_tool_sources.py
git add apps/server
git commit -m "feat(server): extract explicit tool sources"
```

---

### Task 3: Project and aggregate source references on desktop

**Files:**

- Modify: `apps/desktop/src/api/events.ts`
- Modify: `apps/desktop/src/api/chat.ts`
- Modify: `apps/desktop/src/stores/types.ts`
- Create: `apps/desktop/src/stores/sourceRefs.ts`
- Modify: `apps/desktop/src/stores/transcript-projection.ts`
- Modify: `apps/desktop/src/stores/history-response.ts`
- Test: `apps/desktop/tests/sourceRefs.test.ts`
- Test: `apps/desktop/tests/streamEvents.test.ts`
- Test: `apps/desktop/tests/historyMessages.test.ts`

- [ ] **Step 1: Write failing normalization and turn-aggregation tests**

Test malformed input rejection, safe URL handling, cap/deduplication parity with the server, tool-call association, and aggregation across the activity items belonging to one assistant turn.

```ts
expect(sourceRefsForTurn(messages, turnId).map((source) => source.ref)).toEqual([
  "C1:1.0",
  "https://docs.example.test/a",
]);
```

- [ ] **Step 2: Run focused tests and confirm they fail**

Run from `apps/desktop`:

```bash
bun test tests/sourceRefs.test.ts tests/streamEvents.test.ts tests/historyMessages.test.ts
```

- [ ] **Step 3: Add the desktop source contract**

```ts
export interface SourceRef {
  provider: string;
  kind: string;
  ref: string;
  title: string;
  url?: string;
  toolCallId?: string;
}
```

Centralize validation, safe URL handling, deduplication, and turn aggregation in `stores/sourceRefs.ts`.

- [ ] **Step 4: Project live and historical references identically**

Read live references from `TOOL_CALL_RESULT.source_refs`. Read restored references from `tool.result.data.source_refs`. Attach both to the matching `ActivityItem`, and update activity merging so later partial records cannot erase them.

- [ ] **Step 5: Run focused tests and commit**

```bash
bun test tests/sourceRefs.test.ts tests/streamEvents.test.ts tests/historyMessages.test.ts
bun run typecheck
git add apps/desktop
git commit -m "feat(desktop): project tool source references"
```

---

### Task 4: Build the per-turn Sources inspector

**Files:**

- Create: `apps/desktop/src/features/sources/components/SourcesPanel.tsx`
- Create: `apps/desktop/src/features/sources/lib/sourceInspector.ts`
- Modify: `apps/desktop/src/components/AgentRightSidebar.tsx`
- Modify: `apps/desktop/src/stores/types.ts`
- Modify: `apps/desktop/src/stores/index.ts`
- Modify: `apps/desktop/src/features/chat/components/TurnGroup.tsx`
- Modify: `apps/desktop/src/features/chat/components/Message.tsx`
- Modify: `apps/desktop/src/features/chat/components/AssistantMessage.tsx`
- Modify: `apps/desktop/src/features/chat/components/ActivityRows.tsx`
- Modify: `apps/desktop/src/features/chat/lib/operationLabel.ts`
- Test: `apps/desktop/tests/sourceInspector.test.ts`
- Test: `apps/desktop/tests/operationLabel.test.ts`

- [ ] **Step 1: Write failing inspector-state tests**

Cover opening Sources for an exact turn, expanding the existing right panel, manual Sources selection falling back to the latest source-bearing turn, empty state, and a non-URL source retaining its tool-call target.

- [ ] **Step 2: Run focused tests and confirm they fail**

```bash
bun test tests/sourceInspector.test.ts tests/operationLabel.test.ts
```

- [ ] **Step 3: Add ephemeral inspector state**

Add:

```ts
rightInspectorTab: "activity" | "sources";
sourceTurnId: string | null;
setRightInspectorTab(tab): void;
openSourcesForTurn(turnId): void;
```

`openSourcesForTurn` selects Sources, records the turn, and expands the existing right panel. Do not persist the selected tab or turn.

- [ ] **Step 4: Render `Activity | Sources` in the existing sidebar**

Keep current activity behavior intact. The Sources tab shows compact rows grouped only when grouping improves scanning, with provider icon, title, secondary identity, and external-link affordance for safe URLs. For non-URL references, offer `Show call` when a matching activity item exists.

- [ ] **Step 5: Add the answer footer**

Aggregate sources for each assistant turn in `TurnGroup`. On the final assistant message only, render `N sources` below the answer and above existing message actions. Activating it calls `openSourcesForTurn(turnId)`.

- [ ] **Step 6: Remove URL-input pseudo-sources**

Delete `stepSources()` and its chips from activity rows. Tool arguments are not evidence unless the tool result explicitly emits a source reference.

- [ ] **Step 7: Run focused tests, typecheck, and commit**

```bash
bun test tests/sourceInspector.test.ts tests/operationLabel.test.ts tests/sourceRefs.test.ts tests/streamEvents.test.ts tests/historyMessages.test.ts
bun run typecheck
git add apps/desktop
git commit -m "feat(desktop): add sources inspector"
```

---

### Task 5: Verify the end-to-end contract and review the diff

- [ ] **Step 1: Run focused server verification**

From `apps/server`:

```bash
uv run pytest tests/test_tool_sources.py tests/test_streaming_events.py tests/test_mcp_results.py tests/test_mcp_tool.py tests/test_web_tools.py tests/test_file_tools.py tests/test_integration_tool_sources.py -q
uv run ruff check ntrp/agent/types/tools.py ntrp/agent/types/events.py ntrp/agent/tools/runner.py ntrp/core/tool_executor.py ntrp/core/tool_result_data.py ntrp/agent/tools/dispatch.py ntrp/events/sse.py ntrp/server/routers/session.py ntrp/mcp ntrp/integrations/web/tools.py ntrp/integrations/slack/tools.py ntrp/integrations/gmail/tools.py ntrp/integrations/calendar/tools.py ntrp/tools/files.py
```

- [ ] **Step 2: Run focused desktop verification**

From `apps/desktop`:

```bash
bun test tests/sourceRefs.test.ts tests/streamEvents.test.ts tests/historyMessages.test.ts tests/sourceInspector.test.ts tests/operationLabel.test.ts
bun run typecheck
```

- [ ] **Step 3: Inspect behavior manually**

Use a source-bearing tool result and verify:

- The activity row completes normally.
- The final answer shows the correct source count.
- Clicking the footer expands the sidebar on Sources for that exact turn.
- Reloading the session restores the same references.
- External links open only for safe URLs.
- A tool input URL alone creates no source.

- [ ] **Step 4: Request code review and address findings**

Review specifically for accidental recursive extraction, live/history drift, references lost during truncation/offload, stale inspector state after session changes, keyboard/accessibility regressions, and unrelated UI changes.

- [ ] **Step 5: Final clean-tree check**

```bash
git status --short
git log --oneline -5
```
