# Grouped Sources Inspector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the flat per-turn source list with a compact provider → action → tool-call → source hierarchy that works for native tools, arbitrary MCP servers, and future tools.

**Architecture:** Keep the server and `ToolSourceRef` transport unchanged. Add one pure desktop projection that groups the existing `InspectedSource[]` by explicit provider and activity identity, then render those groups with accessible local disclosures in the current Sources inspector.

**Tech Stack:** React 19, TypeScript, Zustand, Tailwind CSS, Bun test runner.

## Global Constraints

- Render only calls that produced normalized source refs; failed calls remain in Activity.
- Do not branch on known tool names or recursively inspect tool-result JSON.
- Unknown MCP providers and tools must render through provider/tool-id fallbacks.
- Preserve first-seen provider, action, call, and source order.
- Keep safe external links, `Show call`, answer footer, and exact/latest turn scope.
- Show at most five source rows per expanded call until the user selects `Show N more`.
- Do not change server extraction, SSE, persistence, history, or source limits.

---

### Task 1: Generic source grouping projection

**Files:**
- Create: `apps/desktop/src/features/sources/lib/sourceGroups.ts`
- Create: `apps/desktop/tests/sourceGroups.test.ts`
- Modify: `apps/desktop/src/features/sources/lib/sourceInspector.ts`

**Interfaces:**
- Consumes: `InspectedSource { source: SourceRef; toolCall?: ActivityItem }[]` from `sourceInspector.ts`.
- Produces: `groupInspectedSources(items: InspectedSource[]): SourceProviderGroup[]`.
- `SourceProviderGroup` contains ordered `actions`, aggregate `callCount`, and `sourceCount`.
- `SourceActionGroup` contains ordered `calls` grouped by explicit activity presentation identity or raw `kind`.

- [ ] **Step 1: Write failing grouping tests**

Create tests covering stable grouping, repeated calls of one action, mixed-provider calls, failed-call omission, and unknown MCP fallbacks:

```ts
import { expect, test } from "bun:test";
import { groupInspectedSources } from "@/features/sources/lib/sourceGroups";
import type { ActivityItem, SourceRef } from "@/stores";

function inspected(
  provider: string,
  kind: string,
  ref: string,
  toolCallId: string,
  tool: Pick<ActivityItem, "kind" | "displayName">,
) {
  const source: SourceRef = { provider, kind, ref, title: ref, toolCallId };
  const toolCall: ActivityItem = {
    id: toolCallId,
    kind: tool.kind,
    displayName: tool.displayName,
    target: tool.displayName ?? tool.kind,
    sourceRefs: [source],
  };
  return { source, toolCall };
}

test("groups arbitrary MCP sources by provider, action, and call", () => {
  const groups = groupInspectedSources([
    inspected("mcp.crm", "contact", "alice", "call-1", {
      kind: "lookup_contact",
      displayName: "Lookup contact",
    }),
    inspected("mcp.crm", "contact", "bob", "call-2", {
      kind: "lookup_contact",
      displayName: "Lookup contact",
    }),
  ]);

  expect(groups).toMatchObject([{
    provider: "mcp.crm",
    callCount: 2,
    sourceCount: 2,
    actions: [{
      label: "Lookup contact",
      calls: [
        { toolCallId: "call-1", sourceCount: 1 },
        { toolCallId: "call-2", sourceCount: 1 },
      ],
    }],
  }]);
});
```

Also assert that a single call returning refs for `mcp.crm` and `mcp.docs` appears once under each provider with only the matching refs, while `toolCall.error === true` is omitted.

- [ ] **Step 2: Run the new test and verify RED**

Run:

```bash
cd apps/desktop
bun test tests/sourceGroups.test.ts
```

Expected: FAIL because `sourceGroups.ts` does not exist.

- [ ] **Step 3: Implement the minimal grouping projection**

Create the focused types and ordered-map projection:

```ts
export interface SourceCallGroup {
  key: string;
  toolCallId?: string;
  toolCall?: ActivityItem;
  target: string;
  sources: SourceRef[];
  sourceCount: number;
}

export interface SourceActionGroup {
  key: string;
  label: string;
  calls: SourceCallGroup[];
  callCount: number;
  sourceCount: number;
}

export interface SourceProviderGroup {
  key: string;
  provider: string;
  actions: SourceActionGroup[];
  callCount: number;
  sourceCount: number;
}

export function groupInspectedSources(items: InspectedSource[]): SourceProviderGroup[] {
  // Use insertion-ordered Maps. Skip known failed calls. Group by source.provider,
  // then presentation identity, then source.toolCallId. Missing activity metadata
  // falls back to "Source action" without dropping the refs.
}
```

Action identity and label must use explicit fields only:

```ts
const actionIdentity = [
  toolCall?.source ?? source.provider,
  toolCall?.noun ?? toolCall?.displayName ?? toolCall?.kind ?? "source-action",
].join("\u0000");
const actionLabel = toolCall?.displayName
  ?? toolCall?.noun
  ?? toolCall?.kind
  ?? "Source action";
```

Export `InspectedSource` from `sourceInspector.ts` unchanged so the grouping module can consume it.

- [ ] **Step 4: Run grouping tests and verify GREEN**

Run:

```bash
cd apps/desktop
bun test tests/sourceGroups.test.ts tests/sourceInspector.test.ts
```

Expected: all tests pass.

- [ ] **Step 5: Commit the projection**

```bash
git add apps/desktop/src/features/sources/lib/sourceGroups.ts \
  apps/desktop/src/features/sources/lib/sourceInspector.ts \
  apps/desktop/tests/sourceGroups.test.ts
git commit -m "feat(desktop): group tool source provenance"
```

### Task 2: Grouped disclosure UI

**Files:**
- Modify: `apps/desktop/src/features/sources/components/SourcesPanel.tsx`
- Modify: `apps/desktop/tests/sourceFooter.test.tsx`

**Interfaces:**
- Consumes: `groupInspectedSources(selection.sources)` and existing `setViewingTool`.
- Preserves: `browserOpenableSourceUrl`, provider/source icon fallback, and current turn selection.

- [ ] **Step 1: Write failing rendered behavior tests**

Extend `sourceFooter.test.tsx` with rendered interaction tests:

```tsx
test("groups an unknown MCP provider and reveals returned sources", async () => {
  // Store one turn with two `lookup_contact` calls from provider `mcp.crm`.
  // Render <SourcesPanel />.
  expect(host.textContent).toContain("mcp.crm");
  expect(host.textContent).toContain("Lookup contact");
  expect(host.textContent).toContain("2 calls");

  const callDisclosure = host.querySelector(
    'button[aria-label^="Expand source call"]',
  ) as HTMLButtonElement;
  expect(callDisclosure.getAttribute("aria-expanded")).toBe("false");
  await act(async () => callDisclosure.click());
  expect(host.textContent).toContain("Alice");
});
```

Add separate assertions that:

- one-source calls start expanded;
- multi-result calls start collapsed;
- expanded calls show five rows plus `Show N more`;
- selecting `Show N more` reveals all rows;
- browser-invalid URLs remain non-clickable;
- `Show call` still opens the originating tool item.

- [ ] **Step 2: Run rendered tests and verify RED**

Run:

```bash
cd apps/desktop
bun test tests/sourceFooter.test.tsx
```

Expected: FAIL because the current panel renders a flat list and has no disclosures.

- [ ] **Step 3: Implement grouped provider/action/call components**

Keep the store subscription in `SourcesPanel`, derive groups with `useMemo`, and key the grouped body by the selected turn so disclosure state resets naturally:

```tsx
const groups = useMemo(
  () => groupInspectedSources(selection.sources),
  [selection.sources],
);

return <GroupedSources key={selection.turnId ?? "latest"} groups={groups} />;
```

Use small private components in the same file:

```tsx
function ProviderSection({ group }: { group: SourceProviderGroup }) {
  return (
    <section>
      <header>{group.provider} · {group.callCount} calls · {group.sourceCount} sources</header>
      {group.actions.map((action) => <ActionDisclosure key={action.key} action={action} />)}
    </section>
  );
}

function ActionDisclosure({ action }: { action: SourceActionGroup }) {
  const [open, setOpen] = useState(true);
  return (
    <section>
      <button type="button" aria-expanded={open} onClick={() => setOpen((value) => !value)}>
        {action.label} · {action.callCount} calls · {action.sourceCount} sources
      </button>
      {open && action.calls.map((call) => <CallDisclosure key={call.key} call={call} />)}
    </section>
  );
}

function CallDisclosure({ call }: { call: SourceCallGroup }) {
  const [open, setOpen] = useState(call.sourceCount === 1);
  return (
    <div>
      <button type="button" aria-expanded={open} onClick={() => setOpen((value) => !value)}>
        {call.target} · {call.sourceCount} sources
      </button>
      {open && <SourceRows call={call} />}
    </div>
  );
}

function SourceRows({ call }: { call: SourceCallGroup }) {
  const [showAll, setShowAll] = useState(false);
  const visible = showAll ? call.sources : call.sources.slice(0, 5);
  return (
    <div>
      {visible.map((source) => <SourceRow key={`${source.provider}:${source.ref}`} source={source} toolCall={call.toolCall} />)}
      {!showAll && call.sources.length > 5 && (
        <button type="button" onClick={() => setShowAll(true)}>
          Show {call.sources.length - 5} more
        </button>
      )}
    </div>
  );
}

function SourceRow({ source, toolCall }: { source: SourceRef; toolCall?: ActivityItem }) {
  const setViewingTool = useStore((state) => state.setViewingTool);
  const openUrl = browserOpenableSourceUrl(source.url);
  return (
    <div>
      {openUrl
        ? <a href={openUrl} target="_blank" rel="noopener noreferrer">{source.title}</a>
        : <span>{source.title}</span>}
      {toolCall && (
        <button type="button" onClick={() => setViewingTool(toolCall)}>Show call</button>
      )}
    </div>
  );
}
```

Requirements:

- disclosure controls are native buttons with `aria-expanded` and visible focus rings;
- provider headers show `N calls · M sources` with singular/plural copy;
- action headers show the explicit presentation label and aggregate counts;
- call headers show the retained compact `target` and source count;
- source rows retain validated links and `Show call`;
- unknown providers use the generic `Link2` icon;
- no new dependencies or global CSS are introduced.

- [ ] **Step 4: Run focused tests and static checks**

Run:

```bash
cd apps/desktop
bun test tests/sourceGroups.test.ts tests/sourceInspector.test.ts tests/sourceFooter.test.tsx tests/sourceRefs.test.ts
bun run lint
bun run typecheck
```

Expected: all focused tests pass; ESLint and TypeScript exit 0.

- [ ] **Step 5: Commit the grouped UI**

```bash
git add apps/desktop/src/features/sources/components/SourcesPanel.tsx \
  apps/desktop/tests/sourceFooter.test.tsx
git commit -m "feat(desktop): group sources by provider action"
```
