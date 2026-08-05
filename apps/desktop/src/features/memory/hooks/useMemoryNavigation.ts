import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type RefObject,
} from "react";
import { useStore } from "@/stores";
import { NavigationHistory, type NavigationLocation } from "@/features/memory/lib/navigationHistory";
import type { MemoryArtifactDetail, MemoryArtifactSummary } from "@/features/memory/lib/notebookTypes";

const LAST_PATH_KEY = "arden.desktop.memory.lastPath";

interface MemoryFocusToken {
  kind: "wikilink" | "inline";
  target: string;
  occurrence: number;
}

function focusToken(element: HTMLElement, scroller: HTMLElement): string | null {
  const kind = element.dataset.wikilink != null ? "wikilink"
    : element.dataset.memoryPath != null ? "inline"
      : null;
  const target = kind === "wikilink" ? element.dataset.wikilink : element.dataset.memoryPath;
  if (!kind || target == null) return null;
  const attribute = kind === "wikilink" ? "data-wikilink" : "data-memory-path";
  const matches = Array.from(scroller.querySelectorAll<HTMLElement>(`[${attribute}]`))
    .filter((candidate) => (kind === "wikilink" ? candidate.dataset.wikilink : candidate.dataset.memoryPath) === target);
  const occurrence = matches.indexOf(element);
  return occurrence < 0 ? null : JSON.stringify({ kind, target, occurrence } satisfies MemoryFocusToken);
}

function restoreFocusToken(value: string, scroller: HTMLElement) {
  let token: MemoryFocusToken;
  try {
    token = JSON.parse(value) as MemoryFocusToken;
  } catch {
    return;
  }
  if ((token.kind !== "wikilink" && token.kind !== "inline") || typeof token.target !== "string" || !Number.isInteger(token.occurrence) || token.occurrence < 0) return;
  const attribute = token.kind === "wikilink" ? "data-wikilink" : "data-memory-path";
  const matches = Array.from(scroller.querySelectorAll<HTMLElement>(`[${attribute}]`))
    .filter((candidate) => (token.kind === "wikilink" ? candidate.dataset.wikilink : candidate.dataset.memoryPath) === token.target);
  matches[token.occurrence]?.focus({ preventScroll: true });
}

interface UseMemoryNavigationOptions {
  artifacts: MemoryArtifactSummary[];
  activeDetail: MemoryArtifactDetail | null;
  loading: boolean;
  activeTab: number;
  tabs: string[];
  workspaceEmpty: boolean;
  layoutRef: RefObject<HTMLDivElement | null>;
  mutationPendingRef: RefObject<boolean>;
  onWorkspaceOpened: () => void;
}

export function useMemoryNavigation({
  artifacts,
  activeDetail,
  loading,
  activeTab,
  tabs,
  workspaceEmpty,
  layoutRef,
  mutationPendingRef,
  onWorkspaceOpened,
}: UseMemoryNavigationOptions) {
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const [historyVersion, setHistoryVersion] = useState(0);
  const [contentDirection, setContentDirection] = useState(1);
  const [navigationVersion, setNavigationVersion] = useState(0);
  const memoryTargetPath = useStore((state) => state.memoryTargetPath);
  const memoryTargetAnchor = useStore((state) => state.memoryTargetAnchor);
  const clearMemoryTarget = useStore((state) => state.clearMemoryTarget);
  const setMemoryCurrentPath = useStore((state) => state.setMemoryCurrentPath);
  const navigationHistory = useRef(new NavigationHistory());
  const pendingRestore = useRef<NavigationLocation | null>(null);
  const restoreByPath = useRef(new Map<string, NavigationLocation>());
  const currentPathRef = useRef<string | null>(null);

  const selectedArtifact = artifacts.find((artifact) => artifact.path === selectedPath) ?? null;
  const currentPath = selectedArtifact?.path ?? selectedPath;
  currentPathRef.current = currentPath;

  useEffect(() => {
    if (!selectedPath) return;
    try {
      localStorage.setItem(LAST_PATH_KEY, selectedPath);
    } catch {
      // Persistence is unavailable.
    }
  }, [selectedPath]);

  useLayoutEffect(() => {
    if (loading || mutationPendingRef.current) return;
    if (workspaceEmpty && tabs.length === 0) {
      if (selectedPath !== null) setSelectedPath(null);
      return;
    }
    if (selectedPath && artifacts.some((artifact) => artifact.path === selectedPath)) return;
    let stored: string | null = null;
    try {
      stored = localStorage.getItem(LAST_PATH_KEY);
    } catch {
      // Persistence is unavailable.
    }
    const restored = stored != null && artifacts.some((artifact) => artifact.path === stored) ? stored : null;
    const activeTabPath = tabs[activeTab];
    const tabFallback = activeTabPath != null && artifacts.some((artifact) => artifact.path === activeTabPath)
      ? activeTabPath
      : null;
    const fallback = tabFallback
      ?? restored
      ?? (artifacts.some((artifact) => artifact.path === "README.md") ? "README.md" : null)
      ?? artifacts[0]?.path
      ?? null;
    if (fallback) {
      navigationHistory.current.push({ path: fallback, anchor: null, scrollTop: 0, focusSelector: null });
      setHistoryVersion((version) => version + 1);
    }
    setSelectedPath(fallback);
  }, [activeTab, artifacts, loading, mutationPendingRef, selectedPath, tabs, workspaceEmpty]);

  const currentLocation = useCallback((): NavigationLocation | null => {
    const path = currentPathRef.current;
    if (!path) return null;
    const article = Array.from(layoutRef.current?.querySelectorAll<HTMLElement>("[data-memory-note-path]") ?? [])
      .find((candidate) => candidate.dataset.memoryNotePath === path);
    const scroller = article?.querySelector<HTMLElement>("[data-memory-note-scroll]");
    const active = document.activeElement;
    const focusSelector = active instanceof HTMLElement && scroller?.contains(active)
      ? focusToken(active, scroller)
      : null;
    const current = navigationHistory.current.current;
    return {
      path,
      anchor: current?.path === path ? current.anchor : null,
      scrollTop: scroller?.scrollTop ?? 0,
      focusSelector,
    };
  }, [layoutRef]);

  const navigateTo = useCallback((
    path: string,
    anchor: string | null = null,
    direction = 1,
  ) => {
    if (mutationPendingRef.current) return;
    if (workspaceEmpty) onWorkspaceOpened();
    const destination = navigationHistory.current.current;
    if (destination?.path === path && destination.anchor === anchor && currentPathRef.current === path) {
      if (anchor) {
        pendingRestore.current = destination;
        setHistoryVersion((version) => version + 1);
      }
      return;
    }
    const current = currentLocation();
    if (current) {
      navigationHistory.current.replaceCurrent(current);
      restoreByPath.current.set(current.path, current);
    }
    const remembered = anchor ? null : restoreByPath.current.get(path);
    const next = {
      path,
      anchor,
      scrollTop: remembered?.scrollTop ?? 0,
      focusSelector: remembered?.focusSelector ?? null,
    };
    navigationHistory.current.push(next);
    pendingRestore.current = next;
    setHistoryVersion((version) => version + 1);
    setNavigationVersion((version) => version + 1);
    setContentDirection(direction < 0 ? -1 : 1);
    setSelectedPath(path);
  }, [currentLocation, mutationPendingRef, onWorkspaceOpened, workspaceEmpty]);

  useLayoutEffect(() => {
    if (loading || !memoryTargetPath) return;
    if (!artifacts.some((artifact) => artifact.path === memoryTargetPath)) return;
    clearMemoryTarget();
    if (selectedPath === memoryTargetPath && !memoryTargetAnchor) return;
    navigateTo(memoryTargetPath, memoryTargetAnchor);
  }, [artifacts, clearMemoryTarget, loading, memoryTargetAnchor, memoryTargetPath, navigateTo, selectedPath]);

  useEffect(() => {
    setMemoryCurrentPath(currentPath);
  }, [currentPath, setMemoryCurrentPath]);

  useEffect(() => {
    const detail = activeDetail;
    const location = pendingRestore.current;
    if (!detail || !location || location.path !== detail.path) return;
    let timer = 0;
    let attempts = 0;
    const restore = () => {
      const article = Array.from(layoutRef.current?.querySelectorAll<HTMLElement>("[data-memory-note-path]") ?? [])
        .find((candidate) => candidate.dataset.memoryNotePath === location.path);
      const scroller = article?.querySelector<HTMLElement>("[data-memory-note-scroll]");
      if (!scroller) {
        attempts += 1;
        if (attempts < 60) timer = window.setTimeout(restore, 16);
        return;
      }
      pendingRestore.current = null;
      if (location.anchor) {
        const normalized = location.anchor.trim().toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
        const heading = Array.from(scroller.querySelectorAll<HTMLElement>("h1,h2,h3,h4,h5,h6"))
          .find((candidate) => candidate.id === location.anchor || candidate.textContent?.trim().toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") === normalized);
        if (heading) {
          heading.tabIndex = -1;
          heading.scrollIntoView({ block: "start" });
          heading.focus({ preventScroll: true });
          return;
        }
      }
      scroller.scrollTop = location.scrollTop;
      if (location.focusSelector) restoreFocusToken(location.focusSelector, scroller);
    };
    timer = window.setTimeout(restore, 0);
    return () => window.clearTimeout(timer);
  }, [activeDetail, historyVersion, layoutRef]);

  const recentPaths = useMemo(() => {
    const seen = new Set<string>();
    const paths: string[] = [];
    for (const location of navigationHistory.current.locations()) {
      if (location.path === currentPath || seen.has(location.path)) continue;
      seen.add(location.path);
      paths.push(location.path);
    }
    return paths;
  }, [currentPath, historyVersion]);

  const clearSelection = useCallback((path?: string) => {
    setSelectedPath((current) => path == null || current === path ? null : current);
  }, []);

  const isSelected = useCallback((path: string) => currentPathRef.current === path, []);
  const selectFile = useCallback((path: string, direction: number) => {
    navigateTo(path, null, direction);
  }, [navigateTo]);

  return {
    selectedPath,
    selectedArtifact,
    contentDirection,
    navigationVersion,
    recentPaths,
    navigateTo,
    selectFile,
    clearSelection,
    isSelected,
  };
}
