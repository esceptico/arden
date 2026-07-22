import type { AppDestination } from "@/api/commands";
import { goToNewSessionHome, switchSession } from "@/actions/sessions";
import { getState } from "@/stores";

export type CommandNavigationResult = { ok: true } | { ok: false; error: string };

export function applyCommandDestination(destination: AppDestination): CommandNavigationResult {
  const state = getState();
  switch (destination.kind) {
    case "home":
      state.closeMemory();
      goToNewSessionHome();
      return { ok: true };
    case "session":
      if (!state.sessions.some((session) => session.session_id === destination.session_id)) {
        return { ok: false, error: "That session is no longer available." };
      }
      state.closeMemory();
      void switchSession(destination.session_id);
      return { ok: true };
    case "settings":
      state.openSettings(null, destination.tab ?? undefined);
      return { ok: true };
    case "automation":
      if (
        destination.task_id
        && !state.automations?.some((automation) => automation.task_id === destination.task_id)
      ) {
        return { ok: false, error: "That automation is no longer available." };
      }
      state.openAutomations(null, destination.task_id ?? null);
      return { ok: true };
    case "memory":
      if (destination.path) {
        return { ok: false, error: "That memory page cannot be opened directly yet." };
      }
      state.openMemory();
      return { ok: true };
    case "area":
      if (!state.areas.recordsById[destination.area_id]) {
        return { ok: false, error: "That area is no longer available." };
      }
      state.closeMemory();
      state.openArea(destination.area_id);
      return { ok: true };
  }
}
