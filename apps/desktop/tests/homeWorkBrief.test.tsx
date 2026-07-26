import { expect, test } from "bun:test";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { WorkBrief } from "@/features/home/components/WorkBrief";
import { FocusRow } from "@/features/home/components/FocusRow";
import type { AreasBrief } from "@/api/areas";
import { getState, setState } from "@/stores";

function setupDom(): { host: HTMLElement; root: Root; restore: () => void } {
  const host = document.createElement("div");
  document.body.append(host);
  return {
    host,
    root: createRoot(host),
    restore: () => host.remove(),
  };
}

const brief: AreasBrief = {
  done: [{ area_id: "visa", area_title: "Visa", stable_key: "draft", text: "Drafted petition", type: "work" }],
  in_progress: [{
    area_id: "health", area_title: "Health", stable_key: "book", text: "Book follow-up panel",
    outcome_title: "Labs normalized", status: "in_progress", owner: "custodian",
  }],
  needs_you: [{
    id: "ask1", area_key: "finance", text: "Choose an accountant", kind: "question", source: "agent",
    actions: [], state: "active", created_at: "2026-07-10T00:00:00Z", snoozed_until: null,
  }, {
    id: "ask2", area_key: "travel", text: "Choose a flight", kind: "review", source: "agent",
    actions: [], state: "active", created_at: "2026-07-11T00:00:00Z", snoozed_until: null,
  }],
};

test("WorkBrief focuses one ask, keeps peers compact, and routes ambient work", async () => {
  setState({ areas: { ...getState().areas, openAreaKey: null } });
  const { host, root, restore } = setupDom();
  try {
    await act(async () => root.render(<WorkBrief brief={brief} />));
    const text = host.textContent ?? "";
    expect(host.querySelector('[data-region="deck"]')).not.toBeNull();
    expect(host.querySelector(".mission-control__deck-chrome [aria-label]")?.getAttribute("aria-label"))
      .toBe("1 of 2");
    expect(text).toContain("Choose an accountant");
    expect(text).toContain("Choose a flight");
    expect(text).toContain("Working");
    expect(text).toContain("Scheduled");
    expect(text).toContain("Done");
    expect(text).toContain("Aside");
    expect(text).not.toContain("That’s it for today.");
    expect(host.querySelector('[data-kind="working"] .mission-control__strip-preview em')?.textContent)
      .toBe("now:");

    const working = host.querySelector('[data-kind="working"] > button') as HTMLButtonElement;
    await act(async () => working.click());
    const peek = document.body.querySelector(".mission-control__ambient-peek");
    expect(working.getAttribute("aria-expanded")).toBe("true");
    expect(peek?.textContent).toContain("Labs normalized");

    const row = peek?.querySelector('button[data-area-id="health"]') as HTMLButtonElement;
    await act(async () => row.click());
    expect(getState().areas.openAreaKey).toBe("health");
  } finally {
    await act(async () => root.unmount());
    restore();
  }
});

test("working preview reads as elapsed duration, never as a past recency stamp", async () => {
  const openedAt = new Date(Date.now() - 11 * 3_600_000).toISOString();
  const datedBrief: AreasBrief = {
    ...brief,
    in_progress: [{ ...brief.in_progress[0], updated_at: openedAt }],
  };
  const { host, root, restore } = setupDom();
  try {
    await act(async () => root.render(<WorkBrief brief={datedBrief} />));
    const head = host.querySelector('[data-kind="working"] .mission-control__strip-preview');
    expect(head?.textContent).toBe("Health · for 11h");
    expect(head?.textContent).not.toContain("ago");
    expect(head?.querySelector("em")).toBeNull();

    const working = host.querySelector('[data-kind="working"] > button') as HTMLButtonElement;
    await act(async () => working.click());
    const meta = document.body.querySelector(".mission-control__ambient-peek .mission-control__ambient-row-meta");
    expect(meta?.textContent).toBe("for 11h");

    const done = host.querySelector('[data-kind="done"] .mission-control__strip-preview');
    expect(done?.querySelector("em")?.textContent).toBe("latest:");
  } finally {
    await act(async () => root.unmount());
    restore();
  }
});

test("lane counts carry unread deltas and opening the lane marks it seen", async () => {
  setState({
    areas: { ...getState().areas, openAreaKey: null },
    prefs: {
      ...getState().prefs,
      ambientSeen: { working: "2026-07-01T00:00:00Z", scheduled: "2026-07-01T00:00:00Z", done: "2026-07-01T00:00:00Z", aside: "2026-07-01T00:00:00Z" },
    },
  });
  const unreadBrief: AreasBrief = {
    ...brief,
    done: [{
      area_id: "visa", area_title: "Visa", stable_key: "draft", text: "Drafted petition", type: "work",
      completed_at: "2026-07-20T00:00:00Z",
    }],
  };
  const { host, root, restore } = setupDom();
  try {
    await act(async () => root.render(<WorkBrief brief={unreadBrief} />));
    const count = host.querySelector('[data-kind="done"] .mission-control__strip-count');
    expect(count?.textContent).toBe("1 new");
    expect(count?.hasAttribute("data-new")).toBe(true);

    const doneHead = host.querySelector('[data-kind="done"] > button') as HTMLButtonElement;
    await act(async () => doneHead.click());
    expect(getState().prefs.ambientSeen.done).toBe("2026-07-20T00:00:00Z");

    await act(async () => doneHead.click());
    expect(host.querySelector('[data-kind="done"] .mission-control__strip-count')?.textContent).toBe("1");
  } finally {
    await act(async () => root.unmount());
    restore();
  }
});

test("WorkBrief keeps a compact all-clear state without a dashboard recap", async () => {
  const { host, root, restore } = setupDom();
  try {
    await act(async () => root.render(<WorkBrief brief={{ done: [], in_progress: [], needs_you: [] }} />));
    expect(host.querySelector('[data-region="deck"]')?.getAttribute("aria-label")).toBe("Needs you");
    expect(host.textContent).toContain("All requests handled");
    expect(host.textContent).toContain("Agent activity");
    expect(host.textContent).not.toContain("That’s it for today.");
  } finally {
    await act(async () => root.unmount());
    restore();
  }
});

test("focused question swaps its verbs for the inline reply slot", async () => {
  const { host, root, restore } = setupDom();
  try {
    await act(async () => root.render(
      <FocusRow
        ask={brief.needs_you[0]}
        areaTitle="Finance"
        expanded
      />,
    ));

    const answer = Array.from(host.querySelectorAll<HTMLButtonElement>("button"))
      .find((button) => button.textContent?.includes("Answer"));
    expect(answer?.textContent).toContain("1");

    await act(async () => answer?.click());
    expect(host.querySelector('[aria-label="Reply to this request"]')).not.toBeNull();
    expect(host.textContent).toContain("↵ send · esc back");
    expect(host.querySelector(".mission-control__focus-actions")?.hasAttribute("data-io-hidden")).toBe(true);
  } finally {
    await act(async () => root.unmount());
    restore();
  }
});
