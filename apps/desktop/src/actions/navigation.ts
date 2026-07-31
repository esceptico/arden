import type { AppDestination } from "@/api/navigation";
import { goToNewSessionHome, switchSession } from "@/actions/sessions";
import { NavigationHistory } from "@/lib/navigationHistory";
import { withNavigationGuard } from "@/lib/navigationGuard";
import { getState } from "@/stores";

export type AppNavigationResult = { ok: true } | { ok: false; error: string };

function sameDestination(left: AppDestination, right: AppDestination): boolean {
  if (left.kind !== right.kind) return false;
  if (left.kind === "session" && right.kind === "session") return left.session_id === right.session_id;
  if (left.kind === "area" && right.kind === "area") return left.area_id === right.area_id;
  if (left.kind === "automation" && right.kind === "automation") return left.task_id === right.task_id;
  if (left.kind === "memory" && right.kind === "memory") return (left.path ?? null) === (right.path ?? null);
  return true;
}

export const appHistory = new NavigationHistory<AppDestination>(sameDestination);

/** Whether back/forward are available, as a bitmask so useSyncExternalStore
 *  gets a referentially stable snapshot without allocating an object per
 *  read. NavigationHistory is a plain class; this is the reactive edge. */
const CAN_BACK = 1;
const CAN_FORWARD = 2;
const historyListeners = new Set<() => void>();
let historySnapshot = 0;

function publishHistory(): void {
  const next = (appHistory.canBack ? CAN_BACK : 0) | (appHistory.canForward ? CAN_FORWARD : 0);
  if (next === historySnapshot) return;
  historySnapshot = next;
  for (const listener of historyListeners) listener();
}

export function subscribeToHistory(listener: () => void): () => void {
  historyListeners.add(listener);
  return () => {
    historyListeners.delete(listener);
  };
}

export function getHistorySnapshot(): number {
  return historySnapshot;
}

export function readHistoryAvailability(snapshot: number) {
  return {
    canBack: (snapshot & CAN_BACK) !== 0,
    canForward: (snapshot & CAN_FORWARD) !== 0,
  };
}

/** Where the shell currently is, derived from store state rather than tracked
 *  separately — the route has one source of truth.
 *
 *  Settings is absent by construction: it is a Takeover over the stage, not a
 *  route, so opening it does not change where you are. Back after closing
 *  Settings returns to what you were doing before it, never into it. */
export function currentDestination(): AppDestination {
  const state = getState();
  if (state.memoryOpen) return { kind: "memory", path: state.memoryCurrentPath };
  if (state.automationsOpen) return { kind: "automation", task_id: state.automationCurrentId };
  if (state.areas.openAreaKey) return { kind: "area", area_id: state.areas.openAreaKey };
  if (state.currentSessionId) return { kind: "session", session_id: state.currentSessionId };
  return { kind: "home" };
}

/** Land on Home from anywhere: dismiss the Settings takeover and leave the
 *  memory/automations routes, then clear the session/room. Shared by the
 *  ⇧⌘H chord and the agent's open_in_app home destination. */
export function goHome(): void {
  const state = getState();
  state.closeMemory();
  state.closeAutomations();
  state.closeSettings();
  goToNewSessionHome();
}

export function applyAppDestination(destination: AppDestination): AppNavigationResult {
  const state = getState();
  switch (destination.kind) {
    case "home":
      goHome();
      return { ok: true };
    case "session":
      if (!state.sessions.some((session) => session.session_id === destination.session_id)) {
        return { ok: false, error: "That session is no longer available." };
      }
      state.closeMemory();
      state.closeAutomations();
      void switchSession(destination.session_id);
      return { ok: true };
    case "settings":
      state.openSettings(destination.tab ?? undefined);
      return { ok: true };
    case "automation":
      if (
        destination.task_id
        && !state.automations?.some((automation) => automation.task_id === destination.task_id)
      ) {
        return { ok: false, error: "That automation is no longer available." };
      }
      state.openAutomations(destination.task_id ?? null);
      return { ok: true };
    case "memory":
      // A path makes this a page address, not just the surface: the same
      // vocabulary an agent uses in open_in_app, and the same one history
      // walks. openMemory hands it to the vault, which selects that page.
      state.openMemory(destination.path ?? undefined);
      return { ok: true };
    case "area":
      if (!state.areas.recordsById[destination.area_id]) {
        return { ok: false, error: "That area is no longer available." };
      }
      state.closeMemory();
      state.closeAutomations();
      state.openArea(destination.area_id);
      return { ok: true };
  }
}

/** Record the route the shell just landed on.
 *
 *  Called by the shell whenever the route changes, rather than at each
 *  navigation call site — sidebar clicks, ⌘N, area cards, agent destinations
 *  and toasts all reach the store by different paths, and a trail assembled
 *  from call sites silently misses whichever one is added next.
 *
 *  Replays need no suppression flag: after back() the cursor already points at
 *  the destination being entered, so the landing is recognised as the current
 *  entry and nothing is pushed. */
export function recordCurrentDestination(): void {
  const now = currentDestination();
  const current = appHistory.current;
  if (current && sameDestination(current, now)) return;
  appHistory.push(now);
  publishHistory();
}

function step(movement: "back" | "forward"): void {
  const target = movement === "back" ? appHistory.back() : appHistory.forward();
  if (!target) return;
  // The destination may have died while it sat in history — session deleted,
  // area archived. Keep walking the same direction rather than stranding the
  // user on a dead entry.
  if (!applyAppDestination(target).ok) step(movement);
  publishHistory();
}

export function goBack(): void {
  if (!appHistory.canBack) return;
  withNavigationGuard(() => step("back"));
}

export function goForward(): void {
  if (!appHistory.canForward) return;
  withNavigationGuard(() => step("forward"));
}
