import { beforeEach, expect, test } from "bun:test";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { TurnGroup } from "@/features/chat/components/TurnGroup";
import { ItemButton } from "@/features/chat/components/ActivityRows";
import { getState, setState } from "@/stores";
import { createBackgroundAgentsDomainState } from "@/stores/background-agent-domain";

beforeEach(() => {
  setState({
    currentSessionId: "session-1",
    running: false,
    sourceRefsRevision: 0,
    messages: new Map(),
    order: [],
    backgroundAgents: createBackgroundAgentsDomainState(),
  });
});

test("live work trace keeps one chronological three-row window", async () => {
  setState({
    messages: new Map([
      ["user-1", {
        id: "user-1",
        role: "user",
        content: "Build reports",
        turn: { startedAt: 1, endedAt: null, durationMs: null },
      }],
      ["reasoning-old", {
        id: "reasoning-old",
        role: "reasoning",
        content: "**Old reasoning**",
      }],
      ["activity-1", {
        id: "activity-1",
        role: "activity",
        content: "",
        activity: {
          label: "Called",
          done: true,
          items: [
            { id: "wiki-old", kind: "wiki_read_page", target: "old", displayTitle: "Old tool", status: "executed" },
            { id: "wiki-new", kind: "wiki_read_page", target: "new", displayTitle: "Latest tool", status: "executed" },
          ],
        },
      }],
      ["reasoning-new", {
        id: "reasoning-new",
        role: "reasoning",
        content: "**Plan report**\n\n**Write report**",
      }],
      ["assistant-live", {
        id: "assistant-live",
        role: "assistant",
        content: "Streaming answer",
      }],
    ]),
    order: ["user-1", "reasoning-old", "activity-1", "reasoning-new", "assistant-live"],
  });

  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  try {
    await act(async () => {
      root.render(
        <TurnGroup
          turnId="user-1"
          userId="user-1"
          childIds={["reasoning-old", "activity-1", "reasoning-new", "assistant-live"]}
        />,
      );
    });

    const trace = host.querySelector<HTMLElement>(".board-trace")!;
    const assistant = host.querySelector<HTMLElement>('[data-id="assistant-live"]')!;
    const rows = Array.from(host.querySelectorAll(".board-trace-row"));
    expect(rows).toHaveLength(3);
    expect(trace.dataset.done).toBeUndefined();
    expect(trace.textContent).toContain("Working");
    expect(trace.compareDocumentPosition(assistant) & Node.DOCUMENT_POSITION_FOLLOWING).not.toBe(0);
    expect(host.textContent).not.toContain("Old reasoning");
    expect(host.textContent).not.toContain("Old tool");
    expect(rows.map((row) => row.textContent?.trim())).toEqual([
      "Latest tool",
      "Plan report",
      "Write report",
    ]);
    expect(host.textContent).toContain("Streaming answer");
  } finally {
    await act(async () => root.unmount());
    host.remove();
  }
});

test("a running agent receives its canonical title and advancing time without navigation", async () => {
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  const startedAt = Date.now();
  try {
    await act(async () => {
      root.render(
        <ItemButton
          item={{
            id: "research-1",
            kind: "research",
            semanticKind: "agent",
            target: "Research",
            status: "ongoing",
            taskStatus: "running",
            startedAt,
          }}
          onOpen={() => {}}
        />,
      );
    });
    expect(host.textContent).toContain("0s");

    await act(async () => {
      getState().upsertBackgroundAgent({
        taskId: "agent-1",
        sessionId: "session-1",
        parentToolCallId: "research-1",
        command: "Review product requirements",
        status: "running",
        createdAt: startedAt,
        updatedAt: startedAt,
      });
    });
    expect(host.textContent).toContain("Review product requirements");

    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 1_100));
    });
    expect(host.textContent).toContain("1s");
  } finally {
    await act(async () => root.unmount());
    host.remove();
  }
});
