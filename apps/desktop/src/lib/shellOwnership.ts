import type { Prefs } from "@/stores/types";

export const COMPACT_SHELL_QUERY = "(max-width: 46.25rem)";

export type ShellWorkspace = "home" | "chat" | "area";

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
  return layout.compact
    ? !compactSidebarOpen
    : prefs.sidebarHidden;
}

/** Generic sidebar actions never mutate the independent right inspector. */
export function toggledSidebarPrefs(prefs: Prefs): Prefs {
  return { ...prefs, sidebarHidden: !prefs.sidebarHidden };
}
