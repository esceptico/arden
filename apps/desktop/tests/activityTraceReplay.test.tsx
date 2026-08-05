import { beforeEach, expect, test } from "bun:test";
import React from "react";
import { readFileSync } from "node:fs";
import { renderToStaticMarkup } from "react-dom/server";
import { ActivityHeader, ActivityTail, liftWorkflows, orderedTraceEntries } from "@/features/chat/components/ActivityTrace";
import { WorkflowProgressCard } from "@/components/ui/WorkflowProgress";
import { activityTraceStats } from "@/lib/agent";
import { turnHeaderLabel } from "@/features/chat/lib/turnHeader";
import { setState } from "@/stores/index";
import { type Workflow } from "@/stores/workflow-domain";

const traceSource = readFileSync(
  new URL("../src/features/chat/components/ActivityTrace.tsx", import.meta.url),
  "utf8",
);

const items = [
  {
    id: "research-1",
    kind: "research",
    semanticKind: "agent",
    target: "research",
    args: "{}",
    depth: 0,
  },
  {
    id: "tool-1",
    kind: "file_read",
    target: 'read_file(path="a")',
    args: "{}",
    depth: 1,
    parentToolId: "research-1",
  },
];

beforeEach(() => {
  setState({
    messages: new Map(),
    order: [],
    streamReplaying: false,
  });
});

test("rolling activity trace uses exact position-only row motion", () => {
  const html = renderToStaticMarkup(
    <ActivityTail items={items} max={3} motionDisabled />,
  );

  expect(html).toContain('class="board-trace__row min-w-0"');
  expect(traceSource).toContain("useReducedMotion");
  expect(traceSource).toContain("Boolean(motionDisabled || streamReplaying || reducedMotion)");
  expect(traceSource).toContain('className="board-trace__rolling relative"');
  expect(traceSource).toContain('mode="popLayout"');
  expect((traceSource.match(/layout=\{suppressMotion \? false : "position"\}/g) ?? [])).toHaveLength(4);
  expect((traceSource.match(/initial=\{suppressMotion \? false : TRACE_ROW_ENTER\}/g) ?? [])).toHaveLength(3);
  expect((traceSource.match(/exit=\{suppressMotion \? \{ opacity: 0 \} : TRACE_ROW_EXIT\}/g) ?? [])).toHaveLength(3);
  expect(traceSource).toContain("TRACE_ROW_TRANSITION");
  expect(traceSource).not.toContain('layout="size"');
  expect(traceSource).not.toContain("layout={true}");
});

test("rolling activity renders orphaned child rows from restored tails", () => {
  const html = renderToStaticMarkup(
    <ActivityTail
      items={[
        {
          id: "child-1",
          kind: "file_read",
          target: 'read_file(path="a")',
          parentToolId: "missing-research",
          depth: 1,
          status: "ongoing",
        },
      ]}
      max={3}
      motionDisabled
    />,
  );

  // Raw target survives on the hover title; the visible label is the
  // natural-language operation verb.
  expect(html).toContain('read_file(path=&quot;a&quot;)');
  expect(html).toContain("Read");
});

test("activity header shows active calls separately from total calls", () => {
  const html = renderToStaticMarkup(
    <ActivityHeader done={false} count={816} activeCount={1} />,
  );

  expect(html).toContain("Working");
  expect(html).toContain("816");
  expect(html).toContain("calls");
  expect(html).toContain("1");
  expect(html).toContain("active");
});

test("activity header keeps working state while a run waits between tools", () => {
  const html = renderToStaticMarkup(
    <ActivityHeader done={false} count={3} activeCount={0} />,
  );

  expect(html).toContain("Working");
  expect(html).not.toContain("Executed");
});

test("activity header renders the current truthful run phase", () => {
  const html = renderToStaticMarkup(
    <ActivityHeader done={false} count={3} activeCount={0} liveHeading="Awaiting approval" />,
  );

  expect(html).toContain("Awaiting approval");
  expect(html).not.toContain("Working");
});

test("activity header shows stopped state after run cancellation", () => {
  const html = renderToStaticMarkup(
    <ActivityHeader done count={109} activeCount={0} label="Stopped" />,
  );

  expect(html).toContain("Stopped");
  expect(html).not.toContain("Executed");
});

test("stopped turn header does not say worked", () => {
  expect(turnHeaderLabel(163000, true)).toBe("Stopped after 2m 43s");
  expect(turnHeaderLabel(163000, false)).toBe("Worked for 2m 43s");
});

test("running agent row renders a stop control", () => {
  const html = renderToStaticMarkup(
    <ActivityTail
      items={[
        {
          id: "call-research",
          kind: "research",
          semanticKind: "agent",
          target: "research",
          status: "ongoing",
          taskStatus: "running",
          runId: "run-1",
        },
      ]}
      max={3}
      motionDisabled
    />,
  );

  expect(html).toContain("Stop subagent");
  expect(html).toContain("group-hover/agent:opacity-0");
  expect(html).toContain("group-hover/agent:opacity-100");
  expect(html).toContain("focus-visible:opacity-100");
});

test("generic ongoing agent row waits for lifecycle ownership before stop control", () => {
  const html = renderToStaticMarkup(
    <ActivityTail
      items={[
        {
          id: "call-research",
          kind: "research",
          semanticKind: "agent",
          target: "research",
          status: "ongoing",
        },
      ]}
      max={3}
      motionDisabled
    />,
  );

  expect(html).not.toContain("Stop subagent");
});

test("agent trace row shows generated name but not prompt text", () => {
  const html = renderToStaticMarkup(
    <ActivityTail
      items={[
        {
          id: "call-research",
          kind: "research",
          semanticKind: "agent",
          target: "research",
          args: JSON.stringify({ task: "inspect current eval/test harness opportunities" }),
          displayName: "Eval test harness",
          status: "ongoing",
        },
      ]}
      max={3}
      motionDisabled
    />,
  );

  expect(html).toContain("Eval test harness");
  expect(html).not.toContain("inspect current eval/test harness opportunities");
});

test("agent trace row with child session renders open-session affordance", () => {
  const html = renderToStaticMarkup(
    <ActivityTail
      items={[
        {
          id: "call-research",
          kind: "research",
          semanticKind: "agent",
          target: "research",
          displayName: "Research Event Systems",
          status: "ongoing",
          childAgent: {
            childRunId: "child-run-1",
            childSessionId: "session-child-1",
            agentType: "research",
            wait: true,
            status: "running",
          },
        },
      ]}
      max={3}
      motionDisabled
    />,
  );

  expect(html).toContain("Open agent session");
  expect(html).toContain('data-child-session-id="session-child-1"');
});

test("session-backed agent rows do not inline child tool rows", () => {
  const html = renderToStaticMarkup(
    <ActivityTail
      items={[
        {
          id: "call-research",
          kind: "research",
          semanticKind: "agent",
          target: "research",
          displayName: "Research Event Systems",
          status: "executed",
          childAgent: {
            childRunId: "child-run-1",
            childSessionId: "session-child-1",
            agentType: "research",
            wait: true,
            status: "completed",
          },
        },
        {
          id: "child-tool",
          kind: "file_read",
          target: 'read_file(path="inside-child")',
          parentToolId: "call-research",
          depth: 1,
          status: "executed",
        },
      ]}
      motionDisabled
    />,
  );

  expect(html).toContain("Research Event Systems");
  expect(html).not.toContain("inside-child");
});

test("rolling activity keeps all agent rows while capping ordinary tool rows", () => {
  const html = renderToStaticMarkup(
    <ActivityTail
      items={[
        {
          id: "old-tool",
          kind: "ReadFile",
          target: "ReadFile(path='old')",
          status: "ongoing",
        },
        ...["One", "Two", "Three", "Four"].map((name) => ({
          id: `agent-${name}`,
          kind: "research",
          semanticKind: "agent",
          target: "research",
          displayName: `Agent ${name}`,
          status: "ongoing",
        })),
        {
          id: "tail-tool",
          kind: "WebSearch",
          target: "WebSearch(query='latest')",
          status: "ongoing",
        },
      ]}
      max={3}
      motionDisabled
    />,
  );

  expect(html).toContain("Agent One");
  expect(html).toContain("Agent Two");
  expect(html).toContain("Agent Three");
  expect(html).toContain("Agent Four");
  expect(html).toContain("WebSearch(query=&#x27;latest&#x27;)");
  expect(html).not.toContain("ReadFile(path=&#x27;old&#x27;)");
});

function makeWorkflow(overrides: Partial<Workflow> = {}): Workflow {
  return {
    workflowId: "wf-1",
    sessionId: "sess-1",
    runId: "run-1",
    parentToolCallId: "wf-tool-call",
    name: "Review feature",
    status: "running",
    phasesByName: {
      Review: { name: "Review", status: "running", agentsByTaskId: {}, startedAt: 1, completedAt: null },
      Verify: { name: "Verify", status: "pending", agentsByTaskId: {}, startedAt: null, completedAt: null },
    },
    totalAgents: 5,
    startedAt: 1000,
    updatedAt: 2000,
    ...overrides,
  };
}

test("liftWorkflows: a live domain row wins and is pulled out, rest stays", () => {
  const { workflowRows, rowItems } = liftWorkflows(
    [
      { id: "wf-tool-call", kind: "workflow", target: 'workflow(title="x")' },
      { id: "plain-tool", kind: "file_read", target: 'read_file(path="keep")' },
    ],
    [makeWorkflow()],
    "sess-1",
  );

  expect(workflowRows.map((w) => w.workflowId)).toEqual(["wf-1"]);
  expect(rowItems.map((i) => i.id)).toEqual(["plain-tool"]);
});

test("liftWorkflows lifts a workflow item even with an EMPTY domain (synthesized card)", () => {
  // The decisive case the old domain-gated lift failed: no workflow_started event
  // reached the client (the real reload state), but the tool-call item is tagged
  // semanticKind="workflow" — so the card must still render from the item alone.
  const { workflowRows, rowItems } = liftWorkflows(
    [
      {
        id: "wf-1",
        kind: "workflow",
        semanticKind: "workflow",
        target: "workflow(...)",
        args: '{"title":"Dex Slack research"}',
        status: "executed",
      },
      { id: "plain", kind: "file_read", target: "file_read" },
    ],
    [],
    "sess-1",
  );

  expect(workflowRows).toHaveLength(1);
  expect(workflowRows[0].name).toBe("Dex Slack research"); // title parsed from args
  expect(workflowRows[0].status).toBe("completed"); // from item.status
  expect(workflowRows[0].parentToolCallId).toBe("wf-1");
  expect(rowItems.map((i) => i.id)).toEqual(["plain"]);
});

test("liftWorkflows contains a workflow's leaked subtree (no parent tool rows)", () => {
  // The server rebases a workflow leaf-agent's tool calls under the workflow's
  // tool-call id (depth 1). They belong in the card's drill-in, NOT as parent
  // rows — and must not inflate the "N calls" header.
  const { workflowRows, rowItems } = liftWorkflows(
    [
      { id: "wf", kind: "workflow", semanticKind: "workflow", target: "workflow(...)", args: '{"title":"X"}', status: "ongoing" },
      { id: "t1", kind: "file_list", target: "ListFiles(...)", parentToolId: "wf", depth: 1, status: "executed" },
      { id: "t2", kind: "bash", target: "Bash(...)", parentToolId: "t1", depth: 2, status: "executed" }, // transitive
      { id: "outside", kind: "file_read", target: "file_read", status: "executed" },
    ],
    [],
    "sess-1",
  );

  expect(workflowRows).toHaveLength(1);
  expect(rowItems.map((i) => i.id)).toEqual(["outside"]); // wf + its whole subtree contained
});

test("liftWorkflows leaves non-workflow items untouched", () => {
  const items = [{ id: "a", kind: "file_read", target: "file_read" }];
  const plain = liftWorkflows(items, [], "sess-1");
  expect(plain.workflowRows).toEqual([]);
  expect(plain.rowItems.map((i) => i.id)).toEqual(["a"]);
  // A workflow domain row whose parent tool-call isn't present is ignored.
  const unmatched = liftWorkflows(items, [makeWorkflow({ parentToolCallId: "absent" })], "sess-1");
  expect(unmatched.workflowRows).toEqual([]);
  expect(unmatched.rowItems.map((i) => i.id)).toEqual(["a"]);
});

test("orderedTraceEntries keeps the workflow card at its chronological position", () => {
  // workflow called AFTER two setup tools and BEFORE one more — the card must
  // sit between the rows segments, not lifted above the whole trace.
  const entries = orderedTraceEntries(
    [
      { id: "a", kind: "slack_thread", target: "SlackThread(...)" },
      { id: "b", kind: "load_tools", target: 'Load Tools(group="slack")' },
      { id: "wf-tool-call", kind: "workflow", target: "workflow(...)" },
      { id: "c", kind: "file_read", target: "file_read" },
    ],
    [makeWorkflow()],
    "sess-1",
  );

  expect(entries.map((e) => e.kind)).toEqual(["rows", "workflow", "rows"]);
  expect(entries[0].kind === "rows" && entries[0].items.map((i) => i.id)).toEqual(["a", "b"]);
  expect(entries[1].kind === "workflow" && entries[1].workflow.workflowId).toBe("wf-1");
  expect(entries[2].kind === "rows" && entries[2].items.map((i) => i.id)).toEqual(["c"]);
});

test("ActivityTail renders the workflow card after the rows that precede it", () => {
  const html = renderToStaticMarkup(
    <ActivityTail
      items={[
        { id: "a", kind: "slack_thread", target: "SlackThread(...)", status: "executed" },
        {
          id: "wf-1",
          kind: "workflow",
          semanticKind: "workflow",
          target: "workflow(...)",
          args: '{"title":"cross-reference"}',
          status: "ongoing",
        },
      ]}
      max={3}
      motionDisabled
    />,
  );

  expect(html.indexOf("SlackThread")).toBeGreaterThan(-1);
  expect(html.indexOf("group/workflow")).toBeGreaterThan(html.indexOf("SlackThread"));
});

test("workflow progress card renders status + a segmented phase bar, not a tool row", () => {
  const html = renderToStaticMarkup(
    <WorkflowProgressCard workflow={makeWorkflow()} onOpen={() => {}} />,
  );

  // Name + status badge + open affordance on a bordered card surface.
  expect(html).toContain("Review feature");
  expect(html).toContain("running"); // status badge
  expect(html).toContain("group/workflow");
  // border-line, not border-line-soft: soft == surface-sunken (#ebebeb) in light
  // mode, so a soft border on this sunken card is invisible.
  expect(html).toContain("border-line");
  expect(html).toContain("Review feature — open");
  // Real progress: a segment per phase (Review running → accent, Verify pending →
  // sunken) plus the agent-completion fraction.
  expect(html).toContain("bg-accent");
  expect(html).toContain("bg-surface-sunken");
  expect(html).toContain("0/5");
  // It is NOT the flat font-mono tool-line used by ordinary tool rows.
  expect(html).not.toContain("tool-line");
});

test("activity stats include subagent rows and their child tools", () => {
  expect(activityTraceStats([
    {
      id: "research-1",
      kind: "research",
      semanticKind: "agent",
      target: "research",
      status: "ongoing",
    },
    {
      id: "child-done",
      kind: "ReadFile",
      target: "ReadFile(path='a')",
      parentToolId: "research-1",
      depth: 1,
      result: "ok",
      status: "executed",
    },
    {
      id: "child-running",
      kind: "SlackSearch",
      target: "SlackSearch(query='x')",
      parentToolId: "research-1",
      depth: 1,
      status: "ongoing",
    },
  ])).toEqual({ totalCount: 3, activeCount: 2 });
});
