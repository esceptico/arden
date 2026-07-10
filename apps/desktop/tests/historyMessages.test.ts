import { expect, test } from "bun:test";
import { historyMessagesToUi } from "@/actions/history";
import type { HistoryMessage } from "@/api/chat";
import { turnLayout } from "@/features/chat/lib/turnLayout";
import { projectHistoryResponse } from "@/stores/history-response";
import type { CachedSessionState } from "@/stores/types";

test("keeps one loaded activity group across reasoning-only history messages", () => {
  const messages: HistoryMessage[] = [
    { role: "user", content: "check it", id: "user-1" },
    {
      role: "assistant",
      content: "",
      id: "assistant-tools-1",
      tool_calls: [{ id: "tool-1", name: "ReadFile", arguments: '{"path":"a"}' }],
    },
    {
      role: "assistant",
      content: "",
      id: "assistant-reasoning-1",
      reasoning_content: "thinking",
    },
    {
      role: "assistant",
      content: "",
      id: "assistant-tools-2",
      tool_calls: [{ id: "tool-2", name: "ReadFile", arguments: '{"path":"b"}' }],
    },
  ];

  const items = historyMessagesToUi(messages, null);
  const activityItems = items.filter((item) => item.role === "activity");

  expect(activityItems).toHaveLength(1);
  expect(activityItems[0].activity?.items.map((item) => item.id)).toEqual([
    "tool-1",
    "tool-2",
  ]);
});

test("keeps one loaded activity group across hidden meta user messages", () => {
  const messages: HistoryMessage[] = [
    { role: "user", content: "check it", id: "user-1" },
    {
      role: "assistant",
      content: "",
      id: "assistant-tools-1",
      tool_calls: [{ id: "tool-1", name: "ReadFile", arguments: '{"path":"a"}' }],
    },
    { role: "user", content: "hidden wakeup", id: "meta-user-1", is_meta: true },
    {
      role: "assistant",
      content: "",
      id: "assistant-tools-2",
      tool_calls: [{ id: "tool-2", name: "ReadFile", arguments: '{"path":"b"}' }],
    },
  ];

  const items = historyMessagesToUi(messages, null);
  const activityItems = items.filter((item) => item.role === "activity");

  expect(activityItems).toHaveLength(1);
  expect(activityItems[0].activity?.items.map((item) => item.id)).toEqual([
    "tool-1",
    "tool-2",
  ]);
});

test("keeps assistant content before tool activity when history row has both", () => {
  const messages: HistoryMessage[] = [
    { role: "user", content: "send it", id: "user-1" },
    {
      role: "assistant",
      content: "I'll draft/send it to yourself.",
      id: "assistant-1",
      tool_calls: [{ id: "tool-1", name: "SendEmail", arguments: '{"account":"me"}' }],
    },
    { role: "tool", content: "sent", id: "tool-result-1", tool_call_id: "tool-1" },
  ];

  const items = historyMessagesToUi(messages, null);

  expect(items.map((item) => item.role)).toEqual(["user", "assistant", "activity"]);
  expect(items[1].content).toBe("I'll draft/send it to yourself.");
  expect(items[2].activity?.items[0].result).toBe("sent");
});

test("rehydrates update_todos tool calls as task list messages", () => {
  const messages: HistoryMessage[] = [
    { role: "user", content: "build todo list", id: "user-1" },
    {
      role: "assistant",
      content: "",
      id: "assistant-todos",
      tool_calls: [
        {
          id: "call-todos",
          name: "update_todos",
          arguments: JSON.stringify({
            explanation: "Track rollout",
            items: [
              { content: "Research prior art", status: "completed" },
              { content: "Implement server tool", status: "in_progress" },
              { content: "Polish desktop UI", status: "pending" },
            ],
          }),
        },
      ],
    },
    { role: "tool", content: "Todo list updated.", id: "tool-result-1", tool_call_id: "call-todos" },
  ];

  const items = historyMessagesToUi(messages, null);

  expect(items.map((item) => item.role)).toEqual(["user", "todo"]);
  expect(items[1].id).toBe("assistant-todos-todo");
  expect(items[1].todo?.items.map((item) => item.content)).toEqual([
    "Research prior art",
    "Implement server tool",
    "Polish desktop UI",
  ]);
});

test("keeps regular activity separate from persisted todo calls", () => {
  const messages: HistoryMessage[] = [
    { role: "user", content: "check it", id: "user-1" },
    {
      role: "assistant",
      content: "",
      id: "assistant-tools",
      tool_calls: [
        {
          id: "call-todos",
          name: "update_todos",
          arguments: JSON.stringify({
            items: [{ content: "Read files", status: "in_progress" }],
          }),
        },
        { id: "tool-1", name: "ReadFile", arguments: '{"path":"a"}' },
      ],
    },
    { role: "tool", content: "Todo list updated.", id: "todo-result", tool_call_id: "call-todos" },
    { role: "tool", content: "file text", id: "tool-result", tool_call_id: "tool-1" },
  ];

  const items = historyMessagesToUi(messages, null);

  expect(items.map((item) => item.role)).toEqual(["user", "todo", "activity"]);
  expect(items[2].activity?.items.map((item) => item.id)).toEqual(["tool-1"]);
});

test("shows all tool args in history activity labels", () => {
  const messages: HistoryMessage[] = [
    { role: "user", content: "search and send", id: "user-1" },
    {
      role: "assistant",
      content: "",
      id: "assistant-tools",
      tool_calls: [
        {
          id: "search-1",
          name: "search_text",
          display_name: "SearchText",
          arguments: '{"query":"ToolCallArgsEvent","path":"."}',
        },
        {
          id: "find-1",
          name: "find_files",
          display_name: "FindFiles",
          arguments: '{"path":".","pattern":"*.tsx"}',
        },
        {
          id: "email-1",
          name: "send_email",
          display_name: "SendEmail",
          arguments: '{"account":"work","to":["me@example.com"],"subject":"Run summary","body":"..."}',
        },
      ],
    },
  ];

  const items = historyMessagesToUi(messages, null);
  const activity = items.find((item) => item.role === "activity");

  expect(activity?.activity?.items.map((item) => item.target)).toEqual([
    'SearchText(query="ToolCallArgsEvent", path=".")',
    'FindFiles(path=".", pattern="*.tsx")',
    'SendEmail(account="work", to=["me@example.com"], subject="Run summary", body="...")',
  ]);
});

test("rehydrates child agent metadata from persisted tool result data", () => {
  const messages = [
    { role: "user", content: "research it", id: "user-1" },
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
          child_session_id: "session-child-1",
          parent_tool_call_id: "agent-call-1",
          agent_type: "background_research",
          wait: false,
          status: "running",
        },
      },
    },
  ] as HistoryMessage[];

  const items = historyMessagesToUi(messages, null);
  const activity = items.find((item) => item.role === "activity");

  expect(activity?.activity?.items[0].semanticKind).toBe("agent");
  expect(activity?.activity?.items[0].childAgent).toEqual({
    childRunId: "child-run-123456",
    childSessionId: "session-child-1",
    parentToolCallId: "agent-call-1",
    agentType: "background_research",
    wait: false,
    status: "running",
  });
});

test("projects normalized persisted source refs onto their matching tool calls", () => {
  const messages: HistoryMessage[] = [
    { role: "user", content: "research it", id: "user-1" },
    {
      role: "assistant",
      content: "",
      id: "assistant-tools",
      tool_calls: [
        { id: "tool-1", name: "slack_thread", arguments: '{"message_id":"C1:1.0"}' },
        { id: "tool-2", name: "web_fetch", arguments: '{"url":"https://docs.example.test/a"}' },
      ],
    },
    {
      role: "tool",
      content: "thread",
      id: "tool-result-1",
      tool_call_id: "tool-1",
      data: {
        source_refs: [
          {
            provider: " slack ",
            kind: " message ",
            ref: " C1:1.0 ",
            title: " Decision ",
            url: "https://user:secret@example.test/private",
            toolCallId: "forged-wire-id",
          },
        ],
      },
    },
    {
      role: "tool",
      content: "docs",
      id: "tool-result-2",
      tool_call_id: "tool-2",
      data: {
        source_refs: [
          {
            provider: "web",
            kind: "page",
            ref: "https://docs.example.test/a",
            title: "Docs",
            url: "https://docs.example.test/a",
          },
        ],
      },
    },
  ];

  const items = historyMessagesToUi(messages, null);
  const activity = items.find((item) => item.role === "activity");

  expect(activity?.activity?.items.map((item) => item.sourceRefs)).toEqual([
    [
      {
        provider: "slack",
        kind: "message",
        ref: "C1:1.0",
        title: "Decision",
        toolCallId: "tool-1",
      },
    ],
    [
      {
        provider: "web",
        kind: "page",
        ref: "https://docs.example.test/a",
        title: "Docs",
        url: "https://docs.example.test/a",
        toolCallId: "tool-2",
      },
    ],
  ]);
});

test("unions live and restored source refs when active history projections merge", () => {
  const history: HistoryMessage[] = [
    { role: "user", content: "research it", id: "user-1" },
    {
      role: "assistant",
      content: "",
      id: "assistant-tools",
      tool_calls: [{ id: "tool-1", name: "search", arguments: "{}" }],
    },
    {
      role: "tool",
      content: "history result",
      id: "tool-result-1",
      tool_call_id: "tool-1",
      data: {
        source_refs: [
          { provider: "slack", kind: "message", ref: "C1:1.0", title: "History first" },
        ],
      },
    },
  ];
  const existing = {
    messages: new Map([
      [
        "live-activity",
        {
          id: "live-activity",
          role: "activity" as const,
          content: "",
          activity: {
            label: "Calling" as const,
            done: false,
            items: [
              {
                id: "tool-1",
                kind: "search",
                target: "Search",
                status: "ongoing" as const,
                sourceRefs: [
                  {
                    provider: "slack",
                    kind: "message",
                    ref: "C1:1.0",
                    title: "Live duplicate",
                    toolCallId: "tool-1",
                  },
                  {
                    provider: "web",
                    kind: "page",
                    ref: "https://docs.example.test/a",
                    title: "Live docs",
                    toolCallId: "tool-1",
                  },
                ],
              },
            ],
          },
        },
      ],
    ]),
    order: ["live-activity"],
    activeActivityId: "live-activity",
  } satisfies Pick<CachedSessionState, "messages" | "order" | "activeActivityId">;

  const projection = projectHistoryResponse(
    { messages: history, active_run_id: "run-active" },
    true,
    existing,
  );
  const activity = projection.items.find((item) => item.role === "activity");

  expect(activity?.activity?.items[0]?.sourceRefs?.map((source) => source.title)).toEqual([
    "History first",
    "Live docs",
  ]);
});

test("keeps persisted goal meta turns visually hidden", () => {
  const messages: HistoryMessage[] = [
    { role: "user", content: "Continue", id: "goal:goal-1:1", is_meta: true },
    { role: "assistant", content: "Working on it.", id: "assistant-1" },
  ];

  const items = historyMessagesToUi(messages, null);

  expect(items.map((item) => [item.role, item.content])).toEqual([
    ["user", "Continue"],
    ["assistant", "Working on it."],
  ]);
  expect(items[0].isMeta).toBe(true);
});

test("preserves persisted switch-back order when assistant text is between tool groups", () => {
  const messages: HistoryMessage[] = [
    {
      role: "user",
      content: "/goal read slack",
      id: "goal-user",
      seq: 1,
    },
    {
      role: "assistant",
      content: "",
      id: "load-tools-assistant",
      seq: 2,
      tool_calls: [{ id: "load-tools", name: "load_tools", arguments: '{"group":"slack"}' }],
    },
    {
      role: "tool",
      content: "Loaded slack tools",
      id: "load-tools-result",
      seq: 3,
      tool_call_id: "load-tools",
    },
    {
      role: "assistant",
      content: "I’ll read the Slack thread, then inspect ~/src/ntrp.",
      id: "progress-assistant",
      seq: 4,
      tool_calls: [{ id: "slack-thread", name: "slack_thread", arguments: '{"message_id":"C:1"}' }],
    },
    {
      role: "tool",
      content: "thread text",
      id: "slack-thread-result",
      seq: 5,
      tool_call_id: "slack-thread",
    },
  ];

  const items = historyMessagesToUi(messages, null);
  const children = items.slice(1).map((item) => ({ id: item.id, role: item.role }));
  const layout = turnLayout({ children, isDone: true });

  expect(children.map((item) => item.role)).toEqual(["activity", "assistant", "activity"]);
  expect(layout.workIds).toEqual(["load-tools-assistant-activity"]);
  expect(layout.afterWorkIds).toEqual(["progress-assistant", "progress-assistant-activity"]);
});

test("projects provider tool calls with embedded result", () => {
  const items = historyMessagesToUi(
    [
      { role: "user", content: "search tools", id: "user-1" },
      {
        role: "assistant",
        content: "",
        id: "assistant-1",
        tool_calls: [
          {
            id: "tsc_1",
            name: "tool_search",
            display_name: "Search Tools",
            arguments: '{"tools":["slack_search"]}',
            result: "Matched tools: slack_search",
          },
        ],
      },
    ],
    null,
  );

  const activity = items.find((item) => item.role === "activity");
  expect(activity?.activity?.items[0]).toMatchObject({
    id: "tsc_1",
    kind: "tool_search",
    target: 'Search Tools(tools=["slack_search"])',
    result: "Matched tools: slack_search",
  });
});

test("reopens newest trailing history activity for active runs", () => {
  const messages: HistoryMessage[] = [
    { role: "user", content: "keep checking", id: "user-1" },
    {
      role: "assistant",
      content: "",
      id: "assistant-tool-1",
      tool_calls: [{ id: "tool-1", name: "Bash", arguments: '{"command":"date"}' }],
    },
    { role: "tool", content: "ok", id: "tool-result-1", tool_call_id: "tool-1" },
    {
      role: "assistant",
      content: "",
      id: "assistant-reasoning-1",
      reasoning_content: "thinking",
    },
  ];

  const items = historyMessagesToUi(messages, "run-active");
  const activity = items.find((item) => item.role === "activity");

  expect(activity?.activity).toMatchObject({ done: false, label: "Calling" });
});

test("does not reopen non-newest history page activity for active runs", () => {
  const messages: HistoryMessage[] = [
    { role: "user", content: "older work", id: "user-1" },
    {
      role: "assistant",
      content: "",
      id: "assistant-tool-1",
      tool_calls: [{ id: "tool-1", name: "Bash", arguments: '{"command":"date"}' }],
    },
    { role: "tool", content: "ok", id: "tool-result-1", tool_call_id: "tool-1" },
  ];

  const items = historyMessagesToUi(messages, "run-active", { isNewestPage: false });
  const activity = items.find((item) => item.role === "activity");

  expect(activity?.activity).toMatchObject({
    done: true,
    label: "Called",
  });
});

test("does not reopen active history activity before visible final assistant", () => {
  const messages: HistoryMessage[] = [
    { role: "user", content: "older work", id: "user-1" },
    {
      role: "assistant",
      content: "",
      id: "assistant-tool-1",
      tool_calls: [{ id: "tool-1", name: "Bash", arguments: '{"command":"date"}' }],
    },
    { role: "tool", content: "ok", id: "tool-result-1", tool_call_id: "tool-1" },
    { role: "assistant", content: "done", id: "assistant-final" },
  ];

  const items = historyMessagesToUi(messages, "run-active");
  const activity = items.find((item) => item.role === "activity");

  expect(activity?.activity).toMatchObject({
    done: true,
    label: "Called",
  });
});

test("reopens newest trailing history activity across hidden meta user messages", () => {
  const messages: HistoryMessage[] = [
    { role: "user", content: "keep checking", id: "user-1" },
    {
      role: "assistant",
      content: "",
      id: "assistant-tool-1",
      tool_calls: [{ id: "tool-1", name: "Bash", arguments: '{"command":"date"}' }],
    },
    { role: "tool", content: "ok", id: "tool-result-1", tool_call_id: "tool-1" },
    { role: "user", content: "hidden wakeup", id: "meta-user-1", is_meta: true },
  ];

  const items = historyMessagesToUi(messages, "run-active");
  const activity = items.find((item) => item.role === "activity");

  expect(activity?.activity).toMatchObject({ done: false, label: "Calling" });
});
