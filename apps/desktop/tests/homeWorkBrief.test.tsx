import { expect, test } from "bun:test";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { WorkBrief } from "@/features/home/components/WorkBrief";
import type { AreasBrief } from "@/api/areas";
import { getState, setState } from "@/stores";

function setupDom(): { host: HTMLElement; root: Root; restore: () => void } {
  const host = document.createElement("div");
  document.body.append(host);
  return { host, root: createRoot(host), restore: () => host.remove() };
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
  }],
};

test("WorkBrief renders finite sections in chief-of-staff order and routes rows", async () => {
  setState({ areas: { ...getState().areas, openAreaKey: null } });
  const { host, root, restore } = setupDom();
  try {
    await act(async () => root.render(<WorkBrief brief={brief} />));
    const text = host.textContent ?? "";
    expect(text.indexOf("Done for you")).toBeLessThan(text.indexOf("In progress"));
    expect(text.indexOf("In progress")).toBeLessThan(text.indexOf("Needs you"));
    expect(text).toContain("Labs normalized");
    expect(text.match(/That’s it for today\./g)).toHaveLength(1);

    const row = host.querySelector('button[data-area-id="health"]') as HTMLButtonElement;
    await act(async () => row.click());
    expect(getState().areas.openAreaKey).toBe("health");
  } finally {
    await act(async () => root.unmount());
    restore();
  }
});

test("WorkBrief hides empty sections but always shows the end", async () => {
  const { host, root, restore } = setupDom();
  try {
    await act(async () => root.render(<WorkBrief brief={{ done: [], in_progress: [], needs_you: [] }} />));
    expect(host.textContent).not.toContain("Done for you");
    expect(host.textContent).not.toContain("In progress");
    expect(host.textContent).not.toContain("Needs you");
    expect(host.textContent?.match(/That’s it for today\./g)).toHaveLength(1);
  } finally {
    await act(async () => root.unmount());
    restore();
  }
});
