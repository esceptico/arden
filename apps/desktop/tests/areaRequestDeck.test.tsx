import { expect, test } from "bun:test";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import type { AreaAsk } from "@/api/areas";
import { AreaRequestDeck } from "@/features/areas/components/AreaRequestDeck";
import { setState } from "@/stores";

function setupDom(): { host: HTMLElement; root: Root; restore: () => void } {
  const host = document.createElement("div");
  document.body.append(host);
  return { host, root: createRoot(host), restore: () => host.remove() };
}

function ask(id: string, text: string): AreaAsk {
  return {
    id,
    area_key: "market-launch",
    text,
    kind: "question",
    source: "agent",
    actions: [],
    state: "active",
    created_at: "2026-07-23T00:00:00Z",
    snoozed_until: null,
  };
}

test("AreaRequestDeck keeps one focused ask and promotes a compact peer in place", async () => {
  setState({ automations: [] });
  const asks = [
    ask("audience", "Choose the launch brief audience — Pick the first-release signal."),
    ask("publish", "Publish the evidence brief — One file update is ready."),
    ask("notion", "Reconnect Notion — The publisher needs access."),
  ];
  const { host, root, restore } = setupDom();
  try {
    await act(async () => {
      root.render(<AreaRequestDeck asks={asks} onDiscuss={() => undefined} />);
    });

    expect(host.querySelectorAll(".board-area-deck__card")).toHaveLength(1);
    expect(host.textContent).toContain("Choose the launch brief audience");
    expect(host.querySelector(".board-area-deck__eyebrow")?.textContent).toContain("Question");
    expect(host.querySelector("[data-area-request-id] small")?.textContent).toContain("question");
    expect(host.textContent).toContain("1 of 3");
    expect([...host.querySelectorAll("[data-area-request-id]")].map((row) => row.getAttribute("data-area-request-id")))
      .toEqual(["publish", "notion"]);
    const stableCard = host.querySelector(".board-area-deck__card");

    await act(async () => {
      host.querySelector<HTMLButtonElement>('[data-area-request-id="publish"]')?.click();
      await new Promise((resolve) => setTimeout(resolve, 240));
    });

    expect(host.querySelectorAll(".board-area-deck__card")).toHaveLength(1);
    expect(host.querySelector(".board-area-deck__card")).toBe(stableCard);
    expect(host.textContent).toContain("Publish the evidence brief");
    expect(host.textContent).toContain("2 of 3");
    expect([...host.querySelectorAll("[data-area-request-id]")].map((row) => row.getAttribute("data-area-request-id")))
      .toEqual(["audience", "notion"]);
  } finally {
    await act(async () => root.unmount());
    restore();
  }
});
