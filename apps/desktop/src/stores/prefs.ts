import {
  isThinkingAnimation,
  isThinkingIntensity,
  type Prefs,
} from "@/stores/types";
import { DEFAULT_ACCENT } from "@/lib/palettes";

export const SIDEBAR_MIN_WIDTH = 200;
export const SIDEBAR_MAX_WIDTH = 380;
export const SIDEBAR_SNAP_POINTS = [220, 244, 288, 320] as const;
export const SIDEBAR_SNAP_THRESHOLD_PX = 12;

export const RIGHT_PANEL_DEFAULT_WIDTH = 320;
export const RIGHT_PANEL_MIN_WIDTH = 280;
export const RIGHT_PANEL_MAX_WIDTH = 520;
export const RIGHT_PANEL_SNAP_POINTS = [320, 360, 420, 480] as const;
export const RIGHT_PANEL_SNAP_THRESHOLD_PX = 12;

export const DEFAULT_QUICK_CAPTURE_SHORTCUT = "CommandOrControl+Shift+Space";

const PREFS_KEY = "arden.desktop.prefs";
export const PREFS_VERSION = 13;

export const DEFAULT_PREFS: Prefs = {
  theme: "system",
  accent: DEFAULT_ACCENT,
  cornerProfile: "round",
  thinkingAnimation: "comet",
  thinkingIntensity: "normal",
  sidebarGroupBy: "area",
  sidebarUnreadOnly: false,
  sidebarChannelsOnly: false,
  pinnedSessionIds: [],
  ambientSeen: {},
  dismissedWorkflows: [],
  sidebarHidden: false,
  rightPanelCollapsed: false,
  rightPanelDocked: false,
  areaHubCollapsed: true,
  sidebarWidth: 288,
  rightPanelWidth: RIGHT_PANEL_DEFAULT_WIDTH,
  quickCaptureShortcut: DEFAULT_QUICK_CAPTURE_SHORTCUT,
};

type LegacyPrefs = Partial<Prefs> & Record<string, unknown> & {
  prefsVersion?: unknown;
  palette?: unknown;
  material?: unknown;
  showReasoningInChat?: unknown;
};

function isPrefsRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/**
 * Local preferences outlive releases, so validate the two restored IDs at
 * the persistence boundary. Their TypeScript unions protect callers, while
 * these guards protect old or manually edited localStorage values.
 */
function normalizePrefs(value: unknown, migrateLegacyLayout: boolean): Prefs {
  if (!isPrefsRecord(value)) return DEFAULT_PREFS;

  const parsed = { ...value } as LegacyPrefs;
  const prefsVersion = typeof parsed.prefsVersion === "number" ? parsed.prefsVersion : 0;
  Reflect.deleteProperty(parsed, "palette");
  Reflect.deleteProperty(parsed, "material");
  Reflect.deleteProperty(parsed, "glass");
  Reflect.deleteProperty(parsed, "showReasoningInChat");

  if (!isThinkingAnimation(parsed.thinkingAnimation)) {
    Reflect.deleteProperty(parsed, "thinkingAnimation");
  }
  if (!isThinkingIntensity(parsed.thinkingIntensity)) {
    Reflect.deleteProperty(parsed, "thinkingIntensity");
  }

  if (migrateLegacyLayout && prefsVersion < PREFS_VERSION && parsed.sidebarWidth === 272) {
    parsed.sidebarWidth = DEFAULT_PREFS.sidebarWidth;
  }
  if (migrateLegacyLayout && prefsVersion < 13) {
    if (parsed.rightPanelCollapsed === true) {
      parsed.rightPanelCollapsed = DEFAULT_PREFS.rightPanelCollapsed;
    }
    if (parsed.areaHubCollapsed === false) {
      parsed.areaHubCollapsed = DEFAULT_PREFS.areaHubCollapsed;
    }
  }

  Reflect.deleteProperty(parsed, "prefsVersion");
  return { ...DEFAULT_PREFS, ...parsed };
}

/** Runtime counterpart to `Prefs`' generic `setPref` signature. */
export function isValidPrefValue(key: keyof Prefs, value: unknown): boolean {
  if (key === "thinkingAnimation") return isThinkingAnimation(value);
  if (key === "thinkingIntensity") return isThinkingIntensity(value);
  return true;
}

export function loadPrefs(): Prefs {
  try {
    const raw = localStorage.getItem(PREFS_KEY);
    if (!raw) return DEFAULT_PREFS;
    return normalizePrefs(JSON.parse(raw), true);
  } catch {
    return DEFAULT_PREFS;
  }
}

export function persistPrefs(prefs: Prefs): void {
  try {
    localStorage.setItem(
      PREFS_KEY,
      JSON.stringify({ ...normalizePrefs(prefs, false), prefsVersion: PREFS_VERSION }),
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
const SKIP_APPROVALS_KEY = "arden.desktop.skipApprovals";

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
