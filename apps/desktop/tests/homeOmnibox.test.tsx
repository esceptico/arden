import { afterEach, expect, test } from "bun:test";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import type { AreasOverview } from "@/api/areas";
import { Home } from "@/features/home/components/Home";
import { getState, setState } from "@/stores";

let root: Root | null = null;
const requests: { path: string; method: string; body?: string }[] = [];
const originalDesktop = window.ardenDesktop;

function overview(): AreasOverview {
  return { areas: [], focus: [], brief: { done: [], in_progress: [], needs_you: [] } } as AreasOverview;
}

function response(data: unknown) {
  return { ok: true, status: 200, statusText: "OK", contentType: "application/json", data, text: "" };
}

/** Enough of the API surface for `sendMessage`'s create-session → send flow
 *  to resolve without throwing — the router reuses that exact path
 *  unchanged, so these tests only need it to not blow up, not to be exact. */
function installBridge() {
  window.ardenDesktop = {
    api: {
      request: async (_config, request) => {
        requests.push({ path: request.path, method: request.method ?? "GET", body: request.body });
        if (request.path === "/areas/overview") return response(overview());
        if (request.path === "/sessions" && request.method === "POST") {
          return response({ session_id: "new-session", started_at: "", last_activity: "", name: null, message_count: 0 });
        }
        if (request.path.startsWith("/session/history")) return response({ messages: [], active_run_id: null });
        if (request.path.endsWith("/goal")) return response(null);
        if (request.path === "/chat/message") return response({ run_id: "run-1" });
        return response({});
      },
    },
  } as Window["ardenDesktop"];
}

function seed() {
  requests.length = 0;
  setState({
    connected: true,
    currentSessionId: null,
    draft: "",
    sessions: [{
      session_id: "roadmap-1",
      started_at: "2026-07-01T00:00:00Z",
      last_activity: "2026-07-01T00:00:00Z",
      name: "Project roadmap chat",
      message_count: 3,
    }],
    automations: [],
    paletteOpen: false,
    settingsOpen: false,
    automationsOpen: false,
    memoryOpen: false,
    areas: { ...getState().areas, overview: overview(), overviewPhase: "ready", openAreaKey: null },
  });
}

async function renderHome(): Promise<HTMLElement> {
  const app = document.createElement("div");
  app.id = "app";
  document.body.append(app);
  root = createRoot(app);
  await act(async () => root?.render(<Home />));
  await act(async () => {});
  return app;
}

function keydown(key: string, init: KeyboardEventInit = {}) {
  const event = new KeyboardEvent("keydown", { key, bubbles: true, cancelable: true, ...init });
  window.dispatchEvent(event);
  return event;
}

/** Arrow/Enter live in the input's own onKeyDown (React-delegated), so the
 *  event has to actually target the input — unlike the global Escape/Cmd+K
 *  shortcuts, which key off `document.activeElement` and don't care where
 *  the event was dispatched. */
function keydownOnInput(input: HTMLInputElement, key: string, init: KeyboardEventInit = {}) {
  const event = new KeyboardEvent("keydown", { key, bubbles: true, cancelable: true, ...init });
  input.dispatchEvent(event);
  return event;
}

/** `expect(domNode).toBeNull()` is fine when it passes, but a REAL element
 *  failing that assertion makes bun's failure-diff printer walk the node's
 *  full prototype chain (ownerDocument → defaultView → the whole `window`),
 *  which is enormous and effectively hangs. Route every such check through a
 *  boolean first so a failure is always a cheap `true !== false`. */
function isPresent(el: Element | null): boolean {
  return el !== null;
}

async function waitFor(predicate: () => boolean): Promise<void> {
  for (let attempt = 0; attempt < 40; attempt += 1) {
    if (predicate()) return;
    await Bun.sleep(10);
  }
  throw new Error("Timed out waiting for Home omnibox state");
}

async function typeQuery(app: HTMLElement, text: string): Promise<HTMLInputElement> {
  const input = app.querySelector<HTMLInputElement>("#message-input")!;
  input.focus();
  await act(async () => {
    // React tracks the DOM node's previous value itself, so a plain
    // `input.value = text` is invisible to it — go through the native
    // setter (house pattern, see memoryFind/memoryQuickSwitcher tests) so
    // the subsequent "input" event actually reaches onChange.
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set?.call(input, text);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  return input;
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
    sessions: [],
    areas: { ...getState().areas, overview: null, overviewPhase: "idle", openAreaKey: null },
  });
  document.body.replaceChildren();
});

test("empty field shows no sheet, quiet as today", async () => {
  installBridge();
  seed();
  const app = await renderHome();
  const input = app.querySelector<HTMLInputElement>("#message-input")!;

  expect(isPresent(app.querySelector('[role="listbox"]'))).toBe(false);
  expect(input.getAttribute("aria-expanded")).toBe("false");
  expect(app.querySelector(".mission-control__capture-field")?.hasAttribute("data-sheet-open")).toBe(false);
  expect(app.textContent).toContain("K");
  // Both hint states stay mounted (constant slot width); quiet state is the
  // one without data-ask — visibility is the attribute, not DOM presence.
  expect(app.querySelector(".mission-control__capture-shortcut")?.hasAttribute("data-ask")).toBe(false);
});

test("typing opens a docked sheet grouped by chats and actions, capped and combobox-wired", async () => {
  installBridge();
  seed();
  const app = await renderHome();
  const input = await typeQuery(app, "chat");

  const listbox = app.querySelector('[role="listbox"]');
  expect(isPresent(listbox)).toBe(true);
  expect(input.getAttribute("aria-expanded")).toBe("true");
  expect(input.getAttribute("aria-controls")).toBe(listbox!.id);
  expect(app.querySelector(".mission-control__capture-field")?.hasAttribute("data-sheet-open")).toBe(true);
  expect(app.textContent).toContain("Chats");
  expect(app.textContent).toContain("Project roadmap chat");
  expect(app.textContent).toContain("Actions");
  expect(app.textContent).toContain("New chat");

  const activeId = input.getAttribute("aria-activedescendant");
  expect(activeId).not.toBeNull();
  // Entry ids carry colons (e.g. "suggested:new-session") — look up by id
  // rather than a CSS selector, which would misparse the colon.
  expect(document.getElementById(activeId!)?.getAttribute("data-selected")).toBe("true");
});

test("Enter opens the pre-selected top result and clears the field", async () => {
  installBridge();
  seed();
  const app = await renderHome();
  const before = getState().prefs.sidebarHidden;
  const input = await typeQuery(app, "sidebar");
  expect(isPresent(app.querySelector('[role="listbox"]'))).toBe(true);

  await act(async () => keydownOnInput(input, "Enter"));

  expect(getState().prefs.sidebarHidden).toBe(!before);
  expect(getState().draft).toBe("");
  expect(input.getAttribute("aria-expanded")).toBe("false");
});

test("Cmd+Enter always starts a new chat, ignoring whatever is selected", async () => {
  installBridge();
  seed();
  const app = await renderHome();
  const before = getState().prefs.sidebarHidden;
  const input = await typeQuery(app, "sidebar");

  await act(async () => keydownOnInput(input, "Enter", { metaKey: true }));

  expect(getState().prefs.sidebarHidden).toBe(before);
  expect(getState().draft).toBe("");
  await waitFor(() => requests.some((r) => r.path === "/sessions" && r.method === "POST"));
  const created = requests.find((r) => r.path === "/sessions" && r.method === "POST");
  expect(created).toBeDefined();
});

test("no-results Enter still starts the chat — the router never dead-ends", async () => {
  installBridge();
  seed();
  const app = await renderHome();
  const input = await typeQuery(app, "zzz-nothing-matches-this-query-qqq");
  expect(isPresent(app.querySelector('[role="listbox"]'))).toBe(false);

  await act(async () => keydownOnInput(input, "Enter"));

  expect(getState().draft).toBe("");
  await waitFor(() => requests.some((r) => r.path === "/sessions" && r.method === "POST"));
});

test("Escape closes the sheet keeping the text, a second Escape clears it", async () => {
  installBridge();
  seed();
  const app = await renderHome();
  const input = await typeQuery(app, "sidebar");
  expect(isPresent(app.querySelector('[role="listbox"]'))).toBe(true);

  // aria-expanded is the field's own state, set the instant the sheet
  // dismisses — unlike DOM presence, it isn't held open by the sheet's exit
  // animation, so it's the reliable "is it closed" signal here.
  await act(async () => keydown("Escape"));
  expect(input.getAttribute("aria-expanded")).toBe("false");
  expect(getState().draft).toBe("sidebar");

  await act(async () => keydown("Escape"));
  expect(getState().draft).toBe("");

  // Typing again reopens the sheet — dismissal doesn't stick.
  await typeQuery(app, "sidebar");
  expect(isPresent(app.querySelector('[role="listbox"]'))).toBe(true);
});

test("the shortcut chip swaps from ⌘K to ⌘↩ ask while the sheet is open", async () => {
  installBridge();
  seed();
  const app = await renderHome();
  const slot = app.querySelector(".mission-control__capture-shortcut")!;
  expect(slot.hasAttribute("data-ask")).toBe(false);

  await typeQuery(app, "sidebar");
  expect(slot.hasAttribute("data-ask")).toBe(true);
  expect(slot.querySelector('[data-hint="ask"] .mission-control__capture-hint-label')?.textContent).toBe("ask");
});
