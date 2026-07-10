import { expect, test } from "bun:test";
import { groupInspectedSources } from "@/features/sources/lib/sourceGroups";
import type { InspectedSource } from "@/features/sources/lib/sourceInspector";
import type { ActivityItem, SourceRef } from "@/stores";

function inspected({
  provider,
  ref,
  toolCallId,
  tool,
}: {
  provider: string;
  ref: string;
  toolCallId?: string;
  tool?: Partial<ActivityItem>;
}): InspectedSource {
  const source: SourceRef = {
    provider,
    kind: "resource",
    ref,
    title: ref,
    ...(toolCallId ? { toolCallId } : {}),
  };
  if (!toolCallId || tool === undefined) return { source };
  return {
    source,
    toolCall: {
      id: toolCallId,
      kind: "lookup_contact",
      target: `query=${ref}`,
      ...tool,
    },
  };
}

test("groups arbitrary MCP sources by provider, action, and call", () => {
  const groups = groupInspectedSources([
    inspected({
      provider: "mcp.crm",
      ref: "alice",
      toolCallId: "call-1",
      tool: { displayName: "Lookup contact" },
    }),
    inspected({
      provider: "mcp.crm",
      ref: "bob",
      toolCallId: "call-2",
      tool: { displayName: "Lookup contact" },
    }),
  ]);

  expect(groups).toMatchObject([{
    provider: "mcp.crm",
    callCount: 2,
    sourceCount: 2,
    actions: [{
      label: "Lookup contact",
      callCount: 2,
      sourceCount: 2,
      calls: [
        { toolCallId: "call-1", target: "query=alice", sourceCount: 1 },
        { toolCallId: "call-2", target: "query=bob", sourceCount: 1 },
      ],
    }],
  }]);
});

test("splits one tool call across its returned source providers", () => {
  const sharedTool: Partial<ActivityItem> = {
    kind: "federated_lookup",
    displayName: "Federated lookup",
    target: "query=quarterly plan",
  };
  const groups = groupInspectedSources([
    inspected({ provider: "mcp.crm", ref: "customer", toolCallId: "call-1", tool: sharedTool }),
    inspected({ provider: "mcp.docs", ref: "plan", toolCallId: "call-1", tool: sharedTool }),
  ]);

  expect(groups.map((group) => ({
    provider: group.provider,
    refs: group.actions[0]?.calls[0]?.sources.map((source) => source.ref),
  }))).toEqual([
    { provider: "mcp.crm", refs: ["customer"] },
    { provider: "mcp.docs", refs: ["plan"] },
  ]);
});

test("omits failed calls and keeps valid refs with missing activity metadata", () => {
  const groups = groupInspectedSources([
    inspected({
      provider: "mcp.crm",
      ref: "failed",
      toolCallId: "call-failed",
      tool: { error: true },
    }),
    inspected({ provider: "future-provider", ref: "opaque-ref", toolCallId: "missing-call" }),
  ]);

  expect(groups).toMatchObject([{
    provider: "future-provider",
    callCount: 1,
    sourceCount: 1,
    actions: [{
      label: "Source action",
      calls: [{ toolCallId: "missing-call", target: "Source action", sourceCount: 1 }],
    }],
  }]);
});

