import { afterAll, beforeAll, beforeEach, expect, test } from "bun:test";
import { getState, setState } from "@/stores";
import { NavigationHistory } from "@/lib/navigationHistory";
import { setNavigationGuard, withNavigationGuard } from "@/lib/navigationGuard";
import {
  appHistory,
  currentDestination,
  goBack,
  goForward,
  recordCurrentDestination,
} from "@/actions/navigation";

const sameKind = (a: { kind: string }, b: { kind: string }) => a.kind === b.kind;

test("pushing truncates the forward branch", () => {
  const history = new NavigationHistory<{ kind: string }>(sameKind);
  history.push({ kind: "a" });
  history.push({ kind: "b" });
  history.push({ kind: "c" });
  history.back();
  history.back();

  expect(history.current).toEqual({ kind: "a" });
  expect(history.canForward).toBe(true);

  history.push({ kind: "d" });

  expect(history.canForward).toBe(false);
  expect(history.length).toBe(2);
});

test("revisiting the current location is not a new entry", () => {
  const history = new NavigationHistory<{ kind: string }>(sameKind);
  history.push({ kind: "a" });
  history.push({ kind: "a" });

  expect(history.length).toBe(1);
});

test("the tail is capped without disturbing the cursor's meaning", () => {
  const history = new NavigationHistory<{ kind: string }>(sameKind, 3);
  for (const kind of ["a", "b", "c", "d"]) history.push({ kind });

  expect(history.length).toBe(3);
  expect(history.current).toEqual({ kind: "d" });
  expect(history.locations().map((l) => l.kind)).toEqual(["d", "c", "b"]);
});

test("a guard can block navigation and replay it later", () => {
  let ran = 0;
  let held: (() => void) | null = null;
  setNavigationGuard((resume) => {
    held = resume;
    return false;
  });

  withNavigationGuard(() => { ran += 1; });
  expect(ran).toBe(0);

  held!();
  expect(ran).toBe(1);

  setNavigationGuard(null);
  withNavigationGuard(() => { ran += 1; });
  expect(ran).toBe(2);
});

// ── The shell's own history ────────────────────────────────────────────────

const SESSION = { session_id: "s1", title: "One", updated_at: "", created_at: "" };

// Entering a session destination loads its transcript. That request is
// incidental here — this file tests the trail, not history loading — and left
// unstubbed it escapes as a floating rejection whose timing depends on which
// test file ran first.
const originalDesktop = window.ardenDesktop;
beforeAll(() => {
  window.ardenDesktop = {
    ...(window.ardenDesktop ?? {}),
    api: {
      request: async () => ({ ok: true, status: 200, contentType: "application/json", data: { messages: [] } }),
    },
  } as typeof window.ardenDesktop;
});
afterAll(() => { window.ardenDesktop = originalDesktop; });

function resetShell() {
  setNavigationGuard(null);
  while (appHistory.canBack) appHistory.back();
  // Drop the forward branch too, so each test starts from one entry.
  appHistory.push({ kind: "home" });
  setState({
    sessions: [SESSION as never],
    currentSessionId: null,
    memoryOpen: false,
    memoryCurrentPath: null,
    automationsOpen: false,
    automationTargetId: null,
    automationCurrentId: null,
    settingsOpen: false,
  });
}

beforeEach(resetShell);

test("Settings is a takeover, so it never becomes a history entry", () => {
  getState().setCurrentSession("s1");
  recordCurrentDestination();
  expect(currentDestination()).toEqual({ kind: "session", session_id: "s1" });

  getState().openSettings();
  recordCurrentDestination();

  // Opening Settings did not move the route: you are still in the session.
  expect(currentDestination()).toEqual({ kind: "session", session_id: "s1" });
  expect(appHistory.current).toEqual({ kind: "session", session_id: "s1" });
});

test("leaving Memory returns to the session it was opened from, not Home", () => {
  // The regression this replaces: Memory's back called goToNewSessionHome(),
  // which cleared currentSessionId. Opening Memory from a chat and leaving
  // evicted the user to Home and dropped the chat they were reading.
  getState().setCurrentSession("s1");
  recordCurrentDestination();

  getState().openMemory();
  recordCurrentDestination();
  expect(currentDestination()).toEqual({ kind: "memory", path: null });

  goBack();

  expect(getState().memoryOpen).toBe(false);
  expect(getState().currentSessionId).toBe("s1");
});

test("forward retraces what back walked away from", () => {
  getState().setCurrentSession("s1");
  recordCurrentDestination();
  getState().openMemory();
  recordCurrentDestination();

  goBack();
  expect(getState().memoryOpen).toBe(false);

  goForward();
  expect(getState().memoryOpen).toBe(true);
});

test("a guard holds route changes until the route releases them", () => {
  getState().setCurrentSession("s1");
  recordCurrentDestination();
  getState().openMemory();
  recordCurrentDestination();

  let resume: (() => void) | null = null;
  setNavigationGuard((retry) => {
    resume = retry;
    return false;
  });

  goBack();
  expect(getState().memoryOpen).toBe(true);

  setNavigationGuard(null);
  resume!();
  expect(getState().memoryOpen).toBe(false);
});

test("wiki pages are addresses, so back steps between them before leaving", () => {
  getState().setCurrentSession("s1");
  recordCurrentDestination();

  getState().openMemory();
  getState().setMemoryCurrentPath("areas/health.md");
  recordCurrentDestination();
  getState().setMemoryCurrentPath("areas/ops.md");
  recordCurrentDestination();

  // One trail: back walks page → page → out of the vault, rather than
  // leaving the route on the first press and stranding the page trail in a
  // second, private history only the keyboard could reach.
  goBack();
  expect(getState().memoryTargetPath).toBe("areas/health.md");
  expect(getState().memoryOpen).toBe(true);

  getState().setMemoryCurrentPath("areas/health.md");
  goBack();
  expect(getState().memoryOpen).toBe(false);
  expect(getState().currentSessionId).toBe("s1");
});

test("an automation selection is an address too", () => {
  getState().setCurrentSession("s1");
  recordCurrentDestination();

  getState().openAutomations();
  getState().setAutomationCurrentId("task-a");
  recordCurrentDestination();
  getState().setAutomationCurrentId("task-b");
  recordCurrentDestination();

  expect(currentDestination()).toEqual({ kind: "automation", task_id: "task-b" });
  expect(appHistory.canBack).toBe(true);
});

test("a route that settles on a default is one arrival, not two", () => {
  // The bug: opening Automations recorded {task_id: null} on arrival, then
  // {task_id: "task-a"} a tick later when the workspace selected its default
  // row. Doing nothing cost two entries, so leaving took two presses — the
  // first one landed you in the room you were already standing in.
  getState().setCurrentSession("s1");
  recordCurrentDestination();

  getState().openAutomations();
  recordCurrentDestination();
  getState().setAutomationCurrentId("task-a");
  recordCurrentDestination();

  goBack();
  expect(getState().automationsOpen).toBe(false);
  expect(getState().currentSessionId).toBe("s1");
});

test("Memory resolving its default page is also one arrival", () => {
  getState().setCurrentSession("s1");
  recordCurrentDestination();

  getState().openMemory();
  recordCurrentDestination();
  getState().setMemoryCurrentPath("index.md");
  recordCurrentDestination();

  goBack();
  expect(getState().memoryOpen).toBe(false);
  expect(getState().currentSessionId).toBe("s1");
});

test("a real move between pages still costs its own step", () => {
  getState().openMemory();
  getState().setMemoryCurrentPath("index.md");
  recordCurrentDestination();
  getState().setMemoryCurrentPath("areas/ops.md");
  recordCurrentDestination();

  // Only the unresolved landing collapses. Page to page is navigation.
  goBack();
  expect(getState().memoryTargetPath).toBe("index.md");
  expect(getState().memoryOpen).toBe(true);
});
