import { expect, test } from "bun:test";
import { act, createRef } from "react";
import { createRoot, type Root } from "react-dom/client";
import { NewAutomationMenu } from "@/features/automations/components/NewAutomationMenu";
import { useStore } from "@/stores";
import type { AutomationSuggestion, CreateAutomationPayload } from "@/api/types";

function suggestion(overrides: Partial<AutomationSuggestion> = {}): AutomationSuggestion {
  return {
    id: "s1",
    name: "Weekly arden PR digest",
    description: "Summarize merged PRs in arden this week.",
    triggers: [{ type: "time", at: "09:00", days: "mon" }],
    rationale: "You review arden PRs most mornings",
    evidence: ["recent PR reviews"],
    category: "Status reports",
    icon: "GitPullRequest",
    ...overrides,
  };
}

/** The menu portals into #app; render it open and hand back both roots. */
async function renderMenu(onPick: (p: CreateAutomationPayload | null) => void) {
  const appEl = document.createElement("div");
  appEl.id = "app";
  document.body.append(appEl);
  const host = document.createElement("div");
  document.body.append(host);
  const root: Root = createRoot(host);
  const anchor = createRef<HTMLElement>();
  await act(async () => {
    root.render(<NewAutomationMenu open onClose={() => {}} anchor={anchor} onPick={onPick} />);
  });
  return {
    appEl,
    root,
    restore: () => {
      appEl.remove();
      host.remove();
    },
  };
}

// ─── Rendering ───────────────────────────────────────────────────────

test("suggestions lead the New menu with their rationale; templates follow", async () => {
  useStore.getState().setAutomationSuggestions([
    suggestion(),
    suggestion({ id: "s2", name: "Inbox triage sweep" }),
  ]);
  const { appEl, root, restore } = await renderMenu(() => {});
  try {
    const text = appEl.textContent ?? "";
    expect(text).toContain("For you");
    expect(text).toContain("Weekly arden PR digest");
    expect(text).toContain("You review arden PRs most mornings");
    expect(text).toContain("Inbox triage sweep");
    // The raw prompt/description never surfaces in the menu.
    expect(text).not.toContain("Summarize merged PRs in arden this week.");
    expect(text).toContain("Templates");
    expect(text).toContain("Start from scratch");
    // Suggestions sort above templates.
    expect(text.indexOf("For you")).toBeLessThan(text.indexOf("Templates"));

    await act(async () => root.unmount());
  } finally {
    restore();
  }
});

test("with no suggestions the For-you section disappears entirely", async () => {
  useStore.getState().setAutomationSuggestions([]);
  const { appEl, root, restore } = await renderMenu(() => {});
  try {
    const text = appEl.textContent ?? "";
    expect(text).not.toContain("For you");
    expect(text).toContain("Templates");
    await act(async () => root.unmount());
  } finally {
    restore();
  }
});

// ─── Interaction ─────────────────────────────────────────────────────

test("picking a suggestion hands over the mapped payload with from_suggestion_id", async () => {
  useStore.getState().setAutomationSuggestions([suggestion()]);
  let picked: CreateAutomationPayload | null | undefined;
  const { appEl, root, restore } = await renderMenu((p) => (picked = p));
  try {
    const item = [...appEl.querySelectorAll('[role="menuitem"]')].find((el) =>
      el.textContent?.includes("Weekly arden PR digest"),
    );
    if (!item) throw new Error("missing suggestion menu item");
    await act(async () => {
      item.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(picked?.from_suggestion_id).toBe("s1");
    expect(picked?.name).toBe("Weekly arden PR digest");
    await act(async () => root.unmount());
  } finally {
    restore();
  }
});

test("start from scratch hands over a null preset", async () => {
  useStore.getState().setAutomationSuggestions([]);
  let picked: CreateAutomationPayload | null | undefined = undefined;
  const { appEl, root, restore } = await renderMenu((p) => (picked = p));
  try {
    const item = [...appEl.querySelectorAll('[role="menuitem"]')].find((el) =>
      el.textContent?.includes("Start from scratch"),
    );
    if (!item) throw new Error("missing scratch menu item");
    await act(async () => {
      item.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(picked).toBeNull();
    await act(async () => root.unmount());
  } finally {
    restore();
  }
});
