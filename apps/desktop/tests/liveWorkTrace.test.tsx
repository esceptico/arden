import { beforeEach, expect, test } from "bun:test";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { TurnGroup } from "@/features/chat/components/TurnGroup";
import { setState } from "@/stores";

beforeEach(() => {
  setState({
    currentSessionId: "session-1",
    running: false,
    sourceRefsRevision: 0,
    messages: new Map(),
    order: [],
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
    ]),
    order: ["user-1", "reasoning-old", "activity-1", "reasoning-new"],
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
          childIds={["reasoning-old", "activity-1", "reasoning-new"]}
        />,
      );
    });

    const rows = Array.from(host.querySelectorAll(".board-trace-row"));
    expect(rows).toHaveLength(3);
    expect(host.textContent).not.toContain("Old reasoning");
    expect(host.textContent).not.toContain("Old tool");
    expect(rows.map((row) => row.textContent?.trim())).toEqual([
      "Latest tool",
      "Plan report",
      "Write report",
    ]);
  } finally {
    await act(async () => root.unmount());
    host.remove();
  }
});
