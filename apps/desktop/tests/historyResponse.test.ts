import { expect, test } from "bun:test";
import { areaHistoryResponse, type HistoryResponse } from "@/stores/history-response";
import type { UiMessage } from "@/stores/types";

test("active history merge keeps child agent metadata from durable result data", () => {
  const history: HistoryResponse = {
    messages: [
      { role: "user", content: "research", id: "user-1" },
      {
        role: "assistant",
        content: "",
        id: "assistant-tools",
        tool_calls: [
          {
            id: "agent-call-1",
            name: "background",
            arguments: '{"task":"research"}',
            kind: "agent",
          },
        ],
      },
      {
        role: "tool",
        content: "Started background agent.",
        id: "agent-result-1",
        tool_call_id: "agent-call-1",
        data: {
          child_agent: {
            child_run_id: "child-run-123456",
            parent_tool_call_id: "agent-call-1",
            agent_type: "background_research",
            wait: false,
            status: "running",
          },
        },
      },
    ],
    active_run_id: "run-A",
    runtime: {
      session_id: "A",
      latest_event_seq: 20,
      checkpoint_seq: 10,
      active_run: {
        run_id: "run-A",
        status: "running",
        checkpoint_seq: 10,
        latest_event_seq: 20,
        pending_approvals: [],
        queued_messages: [],
      },
      pending_approvals: [],
      queued_messages: [],
    },
    page: { has_more_before: false, has_more_after: false },
  };
  const liveActivity: UiMessage = {
    id: "assistant-tools-activity",
    role: "activity",
    content: "",
    activity: {
      label: "Calling",
      done: false,
      items: [
        {
          id: "agent-call-1",
          kind: "background",
          semanticKind: "agent",
          target: "Background(task='research')",
          status: "ongoing",
        },
      ],
    },
  };

  const areaed = areaHistoryResponse(history, true, {
    messages: new Map([[liveActivity.id, liveActivity]]),
    order: [liveActivity.id],
    activeActivityId: liveActivity.id,
  });
  const activity = areaed.items.find((item) => item.role === "activity");

  expect(activity?.activity?.items[0].childAgent).toEqual({
    childRunId: "child-run-123456",
    parentToolCallId: "agent-call-1",
    agentType: "background_research",
    wait: false,
    status: "running",
  });
});

test("history rebuild keeps durable tool outcomes", () => {
  const history: HistoryResponse = {
    messages: [
      { role: "user", content: "write", id: "user-1" },
      {
        role: "assistant",
        content: "",
        id: "assistant-tools",
        tool_calls: [{
          id: "call-1",
          name: "write_file",
          arguments: '{"path":"a.txt"}',
          outcome: {
            status: "succeeded",
            effect: { operation: "write", target: "a.txt" },
            receipt: "sha256:abc",
          },
        }],
      },
      { role: "tool", content: "done", id: "tool-result", tool_call_id: "call-1" },
    ],
    active_run_id: null,
  };

  const activity = areaHistoryResponse(history, true).items.find((item) => item.role === "activity");

  expect(activity?.activity?.items[0].outcome).toEqual({
    status: "succeeded",
    effect: { operation: "write", target: "a.txt" },
    receipt: "sha256:abc",
  });
});
