import { afterEach, expect, test } from "bun:test";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { ArchiveTab } from "@/features/settings/components/ArchiveTab";
import { getState, setState } from "@/stores";

let root: Root | null = null;
const originalDesktop = window.ardenDesktop;
const originalState = getState();

function response(data: unknown) {
  return { ok: true, status: 200, statusText: "OK", contentType: "application/json", data, text: "" };
}

const sessions = [
  {
    session_id: "one",
    name: "Tax paperwork",
    archived_at: "2026-08-01T00:00:00+00:00",
    message_count: 12,
    storage_state: "warm",
    cold_bundle_bytes: null,
  },
  {
    session_id: "two",
    name: "Bike service",
    archived_at: "2026-07-20T00:00:00+00:00",
    message_count: 1,
    storage_state: "cold",
    cold_bundle_bytes: 4_096,
  },
];

async function mount(archived: unknown[]) {
  window.ardenDesktop = {
    api: {
      request: async (_config, request) => {
        if (request.path === "/sessions/archived") return response({ sessions: archived });
        return response({});
      },
    },
  } as Window["ardenDesktop"];
  setState({ connected: true, archivedSessions: archived as never });
  const host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  await act(async () => {
    root?.render(<ArchiveTab />);
    await Bun.sleep(0);
  });
  return host;
}

afterEach(async () => {
  if (root) await act(async () => root?.unmount());
  root = null;
  window.ardenDesktop = originalDesktop;
  setState({ connected: originalState.connected, archivedSessions: originalState.archivedSessions });
  document.body.replaceChildren();
});

test("the tab is only archived chats — no storage budget bleeds in", async () => {
  const host = await mount(sessions);
  expect(host.textContent).toContain("Tax paperwork");
  expect(host.textContent).toContain("Bike service");
  expect(host.textContent).not.toContain("Preview cleanup");
  expect(host.textContent).not.toContain("Maximum Arden space");
  expect(host.querySelector('button[role="switch"]')).toBeNull();
});

test("search lives inside the section head and filters the list", async () => {
  const host = await mount(sessions);
  const search = host.querySelector<HTMLInputElement>('.settings-section-action input[type="text"]')!;
  expect(search.getAttribute("aria-label")).toBe("Search archived chats");

  await act(async () => {
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
    setter.call(search, "bike");
    search.dispatchEvent(new Event("input", { bubbles: true }));
  });

  expect(host.textContent).toContain("Bike service");
  expect(host.textContent).not.toContain("Tax paperwork");
  expect(host.textContent).toContain("1 of 2");
});

test("an empty archive uses the designed empty state", async () => {
  const host = await mount([]);
  expect(host.textContent).toContain("Nothing archived yet.");
  expect(host.querySelector(".settings-archive-list")).toBeNull();
});
