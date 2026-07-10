import { expect, test } from "bun:test";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { AreaWork } from "@/features/areas/components/AreaWork";
import type { AreaWorkSnapshot } from "@/api/areas";

function setupDom(): { host: HTMLElement; root: Root; restore: () => void } {
  const host = document.createElement("div");
  document.body.append(host);
  return { host, root: createRoot(host), restore: () => host.remove() };
}

const work: AreaWorkSnapshot = {
  outcomes: [{
    outcome_id: "outcome:o-1a:file", area_id: "o-1a", stable_key: "file",
    title: "Petition filed", success_criteria: "Receipt exists", status: "active",
    priority: 5, source: "user", created_at: "t0", updated_at: "v1", completed_at: null,
  }],
  work_items: [
    {
      item_id: "work:o-1a:draft", area_id: "o-1a", stable_key: "draft", outcome_id: "outcome:o-1a:file",
      kind: "action", text: "Draft final petition", status: "in_progress", owner: "custodian",
      due_at: null, next_attempt_at: null, created_at: "t0", updated_at: "v2", completed_at: null,
    },
    {
      item_id: "work:o-1a:counsel", area_id: "o-1a", stable_key: "counsel", outcome_id: "outcome:o-1a:file",
      kind: "blocker", text: "Choose filing counsel", status: "active", owner: "user",
      due_at: null, next_attempt_at: null, created_at: "t0", updated_at: "v3", completed_at: null,
    },
  ],
  events: [],
};

test("AreaWork prioritizes the outcome, current action, and user blocker", async () => {
  const { host, root, restore } = setupDom();
  try {
    await act(async () => root.render(<AreaWork areaKey="o-1a" work={work} />));

    expect(host.textContent).toContain("Petition filed");
    expect(host.textContent).toContain("Draft final petition");
    expect(host.textContent).toContain("Choose filing counsel");
    expect(host.textContent).toContain("Needs you");
    expect(host.querySelector('button[aria-label="Complete outcome Petition filed"]')).not.toBeNull();
  } finally {
    await act(async () => root.unmount());
    restore();
  }
});

test("AreaWork reveals a compact outcome form", async () => {
  const { host, root, restore } = setupDom();
  try {
    await act(async () => root.render(<AreaWork areaKey="o-1a" work={work} />));
    const add = Array.from(host.querySelectorAll("button")).find((button) => button.textContent?.includes("Add outcome"));
    await act(async () => add?.click());

    expect(host.querySelector('input[name="outcome-title"]')).not.toBeNull();
    expect(host.querySelector('input[name="success-criteria"]')).not.toBeNull();
  } finally {
    await act(async () => root.unmount());
    restore();
  }
});
