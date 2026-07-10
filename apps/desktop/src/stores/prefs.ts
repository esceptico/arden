import type { Prefs } from "@/stores/types";
import { DEFAULT_ACCENT } from "@/lib/palettes";

export const SIDEBAR_MIN_WIDTH = 200;
export const SIDEBAR_MAX_WIDTH = 380;
export const SIDEBAR_SNAP_POINTS = [220, 244, 280, 320] as const;
export const SIDEBAR_SNAP_THRESHOLD_PX = 12;

export const RIGHT_PANEL_DEFAULT_WIDTH = 320;
export const RIGHT_PANEL_MIN_WIDTH = 280;
export const RIGHT_PANEL_MAX_WIDTH = 520;
export const RIGHT_PANEL_SNAP_POINTS = [320, 360, 420, 480] as const;
export const RIGHT_PANEL_SNAP_THRESHOLD_PX = 12;

export const DEFAULT_QUICK_CAPTURE_SHORTCUT = "CommandOrControl+Shift+Space";

const PREFS_KEY = "ntrp.desktop.prefs";
const PREFS_VERSION = 9;

export const DEFAULT_PREFS: Prefs = {
  thinkingAnimation: "comet",
  thinkingIntensity: "normal",
  theme: "system",
  accent: DEFAULT_ACCENT,
  sidebarGroupBy: "area",
  sidebarUnreadOnly: false,
  sidebarChannelsOnly: false,
  pinnedSessionIds: [],
  dismissedWorkflows: [],
  sidebarHidden: false,
  rightPanelCollapsed: true,
  sidebarWidth: 272,
  rightPanelWidth: RIGHT_PANEL_DEFAULT_WIDTH,
  quickCaptureShortcut: DEFAULT_QUICK_CAPTURE_SHORTCUT,
};

type LegacyPrefs = Partial<Prefs> & {
  prefsVersion?: number;
  palette?: unknown;
  material?: unknown;
  showReasoningInChat?: unknown;
};

export function loadPrefs(): Prefs {
  try {
    const raw = localStorage.getItem(PREFS_KEY);
    if (!raw) return DEFAULT_PREFS;
    const parsed = JSON.parse(raw) as LegacyPrefs;
    Reflect.deleteProperty(parsed, "palette");
    Reflect.deleteProperty(parsed, "material");
    Reflect.deleteProperty(parsed, "glass");
    Reflect.deleteProperty(parsed, "showReasoningInChat");
    return { ...DEFAULT_PREFS, ...parsed };
  } catch {
    return DEFAULT_PREFS;
  }
}

export function persistPrefs(prefs: Prefs): void {
  try {
    localStorage.setItem(
      PREFS_KEY,
      JSON.stringify({ ...prefs, prefsVersion: PREFS_VERSION }),
    );
  } catch {
    /* localStorage unavailable — non-fatal */
  }
}

// Auto mode (skip approvals) is conceptually session state, not a Prefs
// field — but we persist it to localStorage so closing the app and
// reopening doesn't silently flip the user back into approval-required
// mode without warning. Stored separately from `prefs` so the migration
// surface stays narrow.
const SKIP_APPROVALS_KEY = "ntrp.desktop.skipApprovals";

export function loadSkipApprovals(): boolean {
  try {
    return localStorage.getItem(SKIP_APPROVALS_KEY) === "true";
  } catch {
    return false;
  }
}

export function persistSkipApprovals(value: boolean): void {
  try {
    localStorage.setItem(SKIP_APPROVALS_KEY, value ? "true" : "false");
  } catch {
    /* localStorage unavailable — non-fatal */
  }
}
