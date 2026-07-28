export type MemoryTabCloseAction = "close-tab" | "close-others" | "close-right" | "close-all";

export interface MemoryTabClosePlan {
  changed: boolean;
  tabs: string[];
  activeTab: number;
  empty: boolean;
}

export interface MemoryTabOpenPlan {
  changed: boolean;
  tabs: string[];
  activeTab: number;
}

/** Open a page once, or activate its existing tab. The mock treats every
 * page-opening route (rail, wikilink, inspector, switcher) this way. */
export function planMemoryTabOpen(
  tabs: readonly string[],
  activeTab: number,
  path: string,
): MemoryTabOpenPlan {
  const existing = tabs.indexOf(path);
  if (existing >= 0) {
    return {
      changed: existing !== activeTab,
      tabs: [...tabs],
      activeTab: existing,
    };
  }
  return {
    changed: true,
    tabs: [...tabs, path],
    activeTab: tabs.length,
  };
}

/**
 * Match the mock's tab housekeeping exactly. Closing others/right activates
 * the context tab only when something was actually closed; closing the final
 * tab is valid and leaves the notebook in its explicit empty workspace.
 */
export function planMemoryTabClose(
  tabs: readonly string[],
  activeTab: number,
  targetIndex: number,
  action: MemoryTabCloseAction,
): MemoryTabClosePlan {
  if (targetIndex < 0 || targetIndex >= tabs.length) {
    return { changed: false, tabs: [...tabs], activeTab, empty: tabs.length === 0 };
  }

  if (action === "close-all") {
    return { changed: tabs.length > 0, tabs: [], activeTab: 0, empty: true };
  }

  if (action === "close-others") {
    if (tabs.length === 1) return { changed: false, tabs: [...tabs], activeTab, empty: false };
    return { changed: true, tabs: [tabs[targetIndex]!], activeTab: 0, empty: false };
  }

  if (action === "close-right") {
    if (targetIndex >= tabs.length - 1) return { changed: false, tabs: [...tabs], activeTab, empty: false };
    return { changed: true, tabs: tabs.slice(0, targetIndex + 1), activeTab: targetIndex, empty: false };
  }

  const next = tabs.filter((_, index) => index !== targetIndex);
  if (next.length === 0) return { changed: true, tabs: [], activeTab: 0, empty: true };
  let nextActive = activeTab;
  if (nextActive >= next.length) nextActive = next.length - 1;
  else if (targetIndex < nextActive) nextActive -= 1;
  return { changed: true, tabs: next, activeTab: nextActive, empty: false };
}
