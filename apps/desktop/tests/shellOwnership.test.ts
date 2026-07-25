import { expect, test } from "bun:test";
import { getState, setState } from "@/stores";
import { DEFAULT_PREFS, persistPrefs } from "@/stores/prefs";
import {
  DEFAULT_SHELL_LAYOUT,
  resolveEffectiveSidebarHidden,
  toggledSidebarPrefs,
} from "@/lib/shellOwnership";

const dockedChat = {
  ...DEFAULT_SHELL_LAYOUT,
  workspace: "chat" as const,
};

test("the docked Chat inspector never owns or closes the left sidebar", () => {
  const openDockedInspector = {
    ...DEFAULT_PREFS,
    sidebarHidden: false,
    rightPanelDocked: true,
    rightPanelCollapsed: false,
  };

  expect(resolveEffectiveSidebarHidden(dockedChat, openDockedInspector, false)).toBe(false);
  expect(resolveEffectiveSidebarHidden({ ...dockedChat, compact: true }, openDockedInspector, true)).toBe(false);

  expect(toggledSidebarPrefs(openDockedInspector)).toMatchObject({
    sidebarHidden: true,
    rightPanelCollapsed: false,
  });
});

test("generic sidebar actions leave the right inspector open", () => {
  const previousPrefs = getState().prefs;
  const previousLayout = getState().shellLayout;
  try {
    setState({
      prefs: {
        ...DEFAULT_PREFS,
        sidebarHidden: false,
        rightPanelDocked: true,
        rightPanelCollapsed: false,
      },
      shellLayout: dockedChat,
    });

    getState().toggleSidebar();

    expect(getState().prefs).toMatchObject({
      sidebarHidden: true,
      rightPanelCollapsed: false,
    });
  } finally {
    setState({ prefs: previousPrefs, shellLayout: previousLayout });
    persistPrefs(previousPrefs);
  }
});
