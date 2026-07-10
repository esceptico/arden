import { expect, test } from "bun:test";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import type { AreaAsk } from "@/api/areas";
import { setState } from "@/stores";
import { AskCard } from "@/features/areas/components/AskCard";

// Neither test clicks dismiss, so AskCard's real resolveAsk/fetchAreaDetail
// (network calls) never fire — no need to mock @/actions/areas. bun's
// mock.module is process-global and would otherwise leak into every other
// test file that imports named exports from that module later in the run.

function setupDom(): { host: HTMLElement; root: Root; restore: () => void } {
  const host = document.createElement("div");
  document.body.append(host);
  return { host, root: createRoot(host), restore: () => host.remove() };
}

function ask(verb: string, ref: string): AreaAsk {
  return {
    id: "ask1",
    area_key: "o-1a",
    text: "Review counsel memo",
    kind: "review",
    source: "agent",
    actions: [{ verb, ref }],
    state: "active",
    created_at: "2026-07-06T00:00:00Z",
    snoozed_until: null,
  };
}

test("AskCard hides the primary action for an open_page ask (no-op inside its own room)", async () => {
  setState({ automations: [] });
  const { host, root, restore } = setupDom();
  try {
    await act(async () => {
      root.render(<AskCard ask={ask("open_page", "topics/o-1a.md")} />);
    });
    const buttons = Array.from(host.querySelectorAll("button")).map((b) => b.textContent);
    expect(buttons).not.toContain("Review");
  } finally {
    root.unmount();
    restore();
  }
});

test("AskCard still shows the primary action for a non-open_page ask", async () => {
  setState({ automations: [] });
  const { host, root, restore } = setupDom();
  try {
    await act(async () => {
      root.render(<AskCard ask={ask("open_session", "sess-1")} />);
    });
    const buttons = Array.from(host.querySelectorAll("button")).map((b) => b.textContent);
    expect(buttons).toContain("Open");
  } finally {
    root.unmount();
    restore();
  }
});
