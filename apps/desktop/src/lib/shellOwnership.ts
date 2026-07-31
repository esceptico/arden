import type { Prefs } from "@/stores/types";

export const COMPACT_SHELL_QUERY = "(max-width: 46.25rem)";

export type ShellWorkspace = "home" | "chat" | "area" | "memory" | "automations";

/** Routes that bring their own left rail. Memory's vault rail and the
 *  automations list occupy the app sidebar's exact slot — same width var,
 *  same inset — so the shell rail yields rather than stacking under them.
 *  This is what lets both be routes instead of full-window dialogs. */
const RAIL_OWNING_WORKSPACES: ReadonlySet<ShellWorkspace> = new Set(["memory", "automations"]);

export function workspaceOwnsRail(workspace: ShellWorkspace): boolean {
  return RAIL_OWNING_WORKSPACES.has(workspace);
}

/** Responsive shell facts only; never persisted with user preferences. */
export interface ShellLayout {
  compact: boolean;
  workspace: ShellWorkspace;
}

export const DEFAULT_SHELL_LAYOUT: ShellLayout = {
  compact: false,
  workspace: "home",
};

/** The actual left-rail visibility contract used by the app shell. */
export function resolveEffectiveSidebarHidden(
  layout: ShellLayout,
  prefs: Pick<Prefs, "sidebarHidden">,
  compactSidebarOpen: boolean,
): boolean {
  if (workspaceOwnsRail(layout.workspace)) return true;
  return layout.compact
    ? !compactSidebarOpen
    : prefs.sidebarHidden;
}

/** Generic sidebar actions never mutate the independent right inspector. */
export function toggledSidebarPrefs(prefs: Prefs): Prefs {
  return { ...prefs, sidebarHidden: !prefs.sidebarHidden };
}
