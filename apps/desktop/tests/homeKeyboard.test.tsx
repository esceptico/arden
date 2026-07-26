import { afterEach, expect, test } from "bun:test";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import type { AreaAsk, AreasOverview } from "@/api/areas";
import { Home } from "@/features/home/components/Home";
import {
  dispatchHomeKeyboardShortcut,
  type HomeDeckShortcutActions,
} from "@/features/home/hooks/useHomeKeyboard";
import { CommandPalette } from "@/features/command-palette/components/CommandPalette";
import { getState, setState } from "@/stores";

let root: Root | null = null;
let asks: AreaAsk[] = [];
let allAsks = new Map<string, AreaAsk>();
const requests: { path: string; method: string; body?: string }[] = [];
const originalDesktop = window.ardenDesktop;

function ask(id: string, kind: AreaAsk["kind"]): AreaAsk {
  return {
    id,
    area_key: "market-launch",
    area_title: "Market launch",
    text: `${id} decision`,
    kind,
    source: "agent",
    actions: [],
    state: "active",
    created_at: "2026-07-24T00:00:00Z",
    snoozed_until: null,
  };
}

function overview(): AreasOverview {
  return {
    areas: [],
    focus: asks,
    brief: { done: [], in_progress: [], needs_you: asks },
  };
}

function response(data: unknown) {
  return { ok: true, status: 200, statusText: "OK", contentType: "application/json", data, text: "" };
}

function installBridge() {
  window.ardenDesktop = {
    api: {
      request: async (_config, request) => {
        requests.push({ path: request.path, method: request.method ?? "GET", body: request.body });
        if (request.path === "/areas/overview") return response(overview());
        if (request.path.endsWith("/resolve")) {
          const body = JSON.parse(request.body ?? "{}") as { state: AreaAsk["state"] };
          const id = decodeURIComponent(request.path.split("/").at(-2) ?? "");
          const prior = allAsks.get(id);
          if (!prior) return response({});
          const next = { ...prior, state: body.state };
          allAsks.set(id, next);
          asks = body.state === "active"
            ? asks.some((entry) => entry.id === id) ? asks : [next, ...asks]
            : asks.filter((entry) => entry.id !== id);
          return response(next);
        }
        return response({});
      },
    },
  } as Window["ardenDesktop"];
}

function seed(nextAsks: AreaAsk[]) {
  asks = nextAsks;
  allAsks = new Map(nextAsks.map((entry) => [entry.id, entry]));
  requests.length = 0;
  setState({
    connected: true,
    currentSessionId: null,
    draft: "capture draft",
    paletteOpen: false,
    settingsOpen: false,
    automationsOpen: false,
    memoryOpen: false,
    automations: [],
    areas: {
      ...getState().areas,
      overview: overview(),
      overviewPhase: "ready",
      openAreaKey: null,
    },
  });
}

async function renderHome(withPalette = false): Promise<HTMLElement> {
  const app = document.createElement("div");
  app.id = "app";
  document.body.append(app);
  root = createRoot(app);
  await act(async () => {
    root?.render(<>{<Home />}{withPalette && <CommandPalette />}</>);
  });
  await act(async () => {});
  return app;
}

function keydown(key: string, init: KeyboardEventInit = {}) {
  const event = new KeyboardEvent("keydown", { key, bubbles: true, cancelable: true, ...init });
  window.dispatchEvent(event);
  return event;
}

async function waitFor(predicate: () => boolean): Promise<void> {
  for (let attempt = 0; attempt < 40; attempt += 1) {
    if (predicate()) return;
    await Bun.sleep(10);
  }
  throw new Error("Timed out waiting for Home keyboard state");
}

afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
  window.ardenDesktop = originalDesktop;
  setState({
    connected: false,
    paletteOpen: false,
    settingsOpen: false,
    automationsOpen: false,
    memoryOpen: false,
    draft: "",
    areas: { ...getState().areas, overview: null, overviewPhase: "idle", openAreaKey: null },
  });
  document.body.replaceChildren();
});

test("Home shortcut dispatcher mirrors deck verbs while preserving typing and modifier guards", () => {
  const capture = document.createElement("input");
  capture.value = "replace me";
  document.body.append(capture);
  const calls: string[] = [];
  const deck: HomeDeckShortcutActions = {
    activateSlot: (slot) => calls.push(`slot:${slot}`),
    toggleSnooze: () => calls.push("snooze"),
    closeTransient: () => {
      calls.push("escape");
      return true;
    },
    skip: () => calls.push("skip"),
    undo: () => calls.push("undo"),
  };
  const bindings = {
    captureInput: { current: capture },
    deckActions: { current: deck },
  };
  const dispatch = (key: string, init: KeyboardEventInit = {}) => dispatchHomeKeyboardShortcut(
    new KeyboardEvent("keydown", { key, cancelable: true, ...init }),
    bindings,
  );

  expect(dispatch("1")).toBe(true);
  expect(dispatch("2")).toBe(true);
  expect(dispatch("3")).toBe(true);
  expect(dispatch("Enter")).toBe(true);
  expect(dispatch("h")).toBe(true);
  expect(dispatch("z")).toBe(true);
  expect(dispatch("j")).toBe(true);
  expect(calls).toEqual(["slot:1", "slot:2", "slot:3", "slot:1", "snooze", "undo", "skip"]);

  capture.focus();
  expect(dispatch("1")).toBe(false);
  // ⌘K toggles: focused → release, unfocused → summon with select-all.
  expect(dispatch("k", { metaKey: true })).toBe(true);
  expect(document.activeElement).not.toBe(capture);
  expect(dispatch("k", { metaKey: true })).toBe(true);
  expect(document.activeElement).toBe(capture);
  expect(capture.selectionStart).toBe(0);
  expect(capture.selectionEnd).toBe(capture.value.length);
  expect(dispatch("Escape")).toBe(true);
  expect(dispatch("1", { ctrlKey: true })).toBe(false);
  expect(dispatch("Escape")).toBe(true);
  expect(calls.at(-1)).toBe("escape");
});

test("Home reserves Cmd+K for the router field, rotates the deck with J, and closes its foot with Escape", async () => {
  installBridge();
  seed([ask("First", "question"), ask("Second", "question")]);
  const app = await renderHome(true);
  await waitFor(() => requests.filter((request) => request.path === "/areas/overview").length >= 1);
  const capture = app.querySelector<HTMLInputElement>("#message-input")!;

  capture.blur();
  await act(async () => keydown("k", { metaKey: true }));
  // On Home the chord selects the router field — the field IS the palette
  // here (same entry brain); the floating twin never opens.
  expect(document.activeElement).toBe(capture);
  expect(capture.selectionStart).toBe(0);
  expect(capture.selectionEnd).toBe(capture.value.length);
  expect(getState().paletteOpen).toBe(false);

  // Second press toggles the focus (and its veil) back off.
  await act(async () => keydown("k", { metaKey: true }));
  expect(document.activeElement).not.toBe(capture);
  expect(getState().paletteOpen).toBe(false);

  app.tabIndex = -1;
  app.focus();
  await act(async () => {
    expect(keydown("j").defaultPrevented).toBe(true);
    await Bun.sleep(260);
  });
  expect(Array.from(app.querySelectorAll(".mission-control__focus h2"))
    .some((heading) => heading.textContent?.includes("Second decision"))).toBe(true);

  await act(async () => keydown("h"));
  expect(Array.from(app.querySelectorAll(".mission-control__snooze"))
    .some((snooze) => !snooze.hasAttribute("data-io-hidden"))).toBe(true);
  await act(async () => keydown("Escape"));
  expect(Array.from(app.querySelectorAll(".mission-control__focus-actions"))
    .some((actions) => !actions.hasAttribute("data-io-hidden"))).toBe(true);

  await act(async () => keydown("Enter"));
  expect(Array.from(app.querySelectorAll(".mission-control__reply"))
    .some((reply) => !reply.hasAttribute("data-io-hidden"))).toBe(true);
  await act(async () => keydown("Escape"));
  expect(Array.from(app.querySelectorAll(".mission-control__focus-actions"))
    .some((actions) => !actions.hasAttribute("data-io-hidden"))).toBe(true);
});

test("Z reopens only the last committed Home ask through the active resolve state", async () => {
  installBridge();
  seed([ask("Receipt", "notify")]);
  const app = await renderHome();
  await waitFor(() => requests.filter((request) => request.path === "/areas/overview").length >= 1);

  await act(async () => keydown("1"));
  await waitFor(() => requests.filter((request) => request.path.endsWith("/resolve")).length === 1);
  await waitFor(() => requests.filter((request) => request.path === "/areas/overview").length >= 2);
  await act(async () => {});
  expect(getState().areas.overview?.brief.needs_you).toEqual([]);

  await act(async () => keydown("z"));
  await waitFor(() => requests.filter((request) => request.path.endsWith("/resolve")).length === 2);
  await act(async () => {});

  const mutations = requests.filter((request) => request.path.endsWith("/resolve"));
  expect(mutations.map((request) => JSON.parse(request.body ?? "{}"))).toEqual([
    { state: "done", resolution: "acknowledged" },
    { state: "active" },
  ]);
  expect(app.textContent).toContain("Receipt decision");
});
