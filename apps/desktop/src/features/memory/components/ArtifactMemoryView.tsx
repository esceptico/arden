import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { ChevronLeft, ChevronRight, Clock, LeftToRightListBullet, Link01, MoreHorizontal } from "@/components/icons";
import clsx from "clsx";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import {
  CONTENT_ENTER_TRANSITION,
  CONTENT_EXIT_TRANSITION,
  CONTENT_SWAP_VARIANTS,
  MOTION,
} from "@/lib/tokens/motion";
import type { AppConfig } from "@/api/core";
import type { WikiLinkHandlers } from "@/lib/wikilink";
import { listMemoryArtifactSummaries, rebuildMemoryArtifactSummaries } from "@/api/memoryArtifacts";
import { listMemoryItems, type MemoryItem } from "@/api/memoryItems";
import { IconSwap } from "@/components/ui/IconSwap";
import { ContextMenu, type ContextMenuEntry, type ContextMenuPosition } from "@/components/ui/ContextMenu";
import { SidebarToggle } from "@/components/ui/SidebarToggle";
import { PeekSurface } from "@/components/workspace/PeekSurface";
import { NotebookRail, type MemoryRailMode } from "@/features/memory/components/NotebookRail";
import { PaneResizeHandle } from "@/components/workspace/PaneResizeHandle";
import { SidebarResizeHandle } from "@/components/workspace/SidebarResizeHandle";
import { MemoryNote } from "@/features/memory/components/MemoryNote";
import { MemoryInspector } from "@/features/memory/components/MemoryInspector";
import { MemoryEditor } from "@/features/memory/components/MemoryEditor";
import { MemoryEditReview } from "@/features/memory/components/MemoryEditReview";
import { MemoryDiffOverlay } from "@/features/memory/components/MemoryDiffOverlay";
import { MemoryQuickSwitcher } from "@/features/memory/components/MemoryQuickSwitcher";
import { MemoryDocumentTabs } from "@/features/memory/components/MemoryDocumentTabs";
import { WikiLinkPreview } from "@/features/memory/components/WikiLinkPreview";
import {
  WikiRenameApprovalSheet,
  WikiRenameApprovalStatusSheet,
} from "@/features/memory/components/WikiRenameApprovalSheet";
import { useMemoryVaultReconciliation } from "@/features/memory/hooks/useMemoryVaultReconciliation";
import { useMemoryEditing } from "@/features/memory/hooks/useMemoryEditing";
import { useMemoryDocument } from "@/features/memory/hooks/useMemoryDocument";
import { useMemoryInspector } from "@/features/memory/hooks/useMemoryInspector";
import { useMemoryNavigation } from "@/features/memory/hooks/useMemoryNavigation";
import { useWikiRenameApprovals } from "@/features/memory/hooks/useWikiRenameApprovals";
import { resolveWikiTarget } from "@/features/memory/lib/wikiResolution";
import { isNotebookResourcePath } from "@/features/memory/lib/notebookIndex";
import { getBoardMotion } from "@/lib/boardMotion";
import { buildWorkspaceTree, displayTitle } from "@/features/memory/lib/workspaceTree";
import { planMemoryTabClose, planMemoryTabOpen, type MemoryTabCloseAction } from "@/features/memory/lib/tabContext";
import { copyText } from "@/lib/clipboard";
import type { MemoryArtifactDetail, MemoryArtifactSummary, PageEditEvent } from "@/features/memory/lib/notebookTypes";

const RECORD_PAGE_SIZE = 100;
const CTX_WIDTH_KEY = "arden.desktop.memory.ctxWidth";
const COMPACT_RAIL_QUERY = "(max-width: 740px)";
const MEMORY_PAGE_SWAP_VARIANTS = {
  enter: (direction: number) => ({
    ...CONTENT_SWAP_VARIANTS.enter(direction),
    transition: CONTENT_ENTER_TRANSITION,
  }),
  center: {
    ...CONTENT_SWAP_VARIANTS.center,
    transition: CONTENT_ENTER_TRANSITION,
  },
  exit: (direction: number) => ({
    ...CONTENT_SWAP_VARIANTS.exit(direction),
    transition: CONTENT_EXIT_TRANSITION,
  }),
} as const;

interface MemoryTabContextMenuState extends ContextMenuPosition {
  index: number;
  path: string;
}

interface MemoryPageContextMenuState extends ContextMenuPosition {
  page: MemoryArtifactDetail;
}

function loadCompactRail(): boolean {
  return typeof window.matchMedia === "function"
    && window.matchMedia(COMPACT_RAIL_QUERY).matches;
}

interface SummaryRequest {
  epoch: number;
  controller: AbortController;
}

export function ArtifactMemoryView({ config }: { config: AppConfig }) {
  const reduce = useReducedMotion();
  const [artifacts, setArtifacts] = useState<MemoryArtifactSummary[]>([]);
  const [directories, setDirectories] = useState<string[]>([]);
  const [activeDetail, setActiveDetail] = useState<MemoryArtifactDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [rebuilding, setRebuilding] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [records, setRecords] = useState<MemoryItem[]>([]);
  const [recordsError, setRecordsError] = useState<string | null>(null);
  const [recordsLoading, setRecordsLoading] = useState(false);
  const [recordsRefreshKey, setRecordsRefreshKey] = useState(0);
  const [diffEvent, setDiffEvent] = useState<PageEditEvent | null>(null);
  const [diffClosing, setDiffClosing] = useState(false);
  const [switcherOpen, setSwitcherOpen] = useState(false);
  const [railHidden, setRailHidden] = useState(loadCompactRail);
  const [instrumentsCollapsed, setInstrumentsCollapsed] = useState(false);
  const [railMode, setRailMode] = useState<MemoryRailMode>("files");
  const [tabs, setTabs] = useState<string[]>([]);
  const [activeTab, setActiveTab] = useState(0);
  const [workspaceEmpty, setWorkspaceEmpty] = useState(false);
  const [tabContextMenu, setTabContextMenu] = useState<MemoryTabContextMenuState | null>(null);
  const [pageContextMenu, setPageContextMenu] = useState<MemoryPageContextMenuState | null>(null);

  const summaryRequest = useRef<SummaryRequest | null>(null);
  const recordsRequestId = useRef(0);
  const artifactsRef = useRef<MemoryArtifactSummary[]>([]);
  const layoutRef = useRef<HTMLDivElement>(null);
  const instrumentsCollapsedRef = useRef(instrumentsCollapsed);
  const instrumentsCollapsedByFit = useRef(false);
  const instrumentsFitOverride = useRef(false);
  const mutationPendingRef = useRef(false);
  const diffReturnFocus = useRef<HTMLElement | null>(null);
  const mountedRef = useRef(true);
  const disposeControllerRef = useRef(new AbortController());

  artifactsRef.current = artifacts;
  instrumentsCollapsedRef.current = instrumentsCollapsed;

  useEffect(() => {
    if (diffEvent || !diffReturnFocus.current) return;
    const returnFocus = diffReturnFocus.current;
    const timer = window.setTimeout(() => {
      diffReturnFocus.current = null;
      if (!returnFocus.isConnected || returnFocus.closest("[inert]")) return;
      returnFocus.focus({ preventScroll: true });
    }, 0);
    return () => window.clearTimeout(timer);
  }, [diffEvent]);

  useLayoutEffect(() => {
    const root = layoutRef.current;
    if (!root) return;

    const syncInstrumentFit = () => {
      const tabStrip = root.querySelector<HTMLElement>(".mw-tab-strip");
      const instruments = root.querySelector<HTMLElement>(".mw-instruments");
      const instrumentItems = root.querySelector<HTMLElement>(".mw-instrument-items");
      const instrumentCollapse = root.querySelector<HTMLElement>(".mw-instrument-collapse");
      if (!tabStrip || !instruments || !instrumentItems || !instrumentCollapse) return;

      const rootFontSize = Number.parseFloat(getComputedStyle(document.documentElement).fontSize) || 16;
      const rootStyle = getComputedStyle(root);
      const cssLength = (name: string) => {
        const value = rootStyle.getPropertyValue(name).trim();
        return value.endsWith("rem")
          ? Number.parseFloat(value) * rootFontSize
          : Number.parseFloat(value);
      };
      const measuredInstrumentWidth = Math.ceil(
        instrumentItems.scrollWidth
        + instrumentCollapse.offsetWidth
        + cssLength("--tab-bar-gap")
        // .mw-instruments' own left+right padding — was --instrument-inner-gap
        // (a flat 8px, imprecise stand-in, same issue as the doc-tab strip's
        // old --tab-strip-chrome). Content is right-anchored, so undersizing
        // this clips the leftmost button; oversizing shows as left-side slack.
        + cssLength("--tab-bar-padding") * 2,
      );
      if (Number.isFinite(measuredInstrumentWidth)) {
        root.style.setProperty("--memory-instrument-expanded-width", `${measuredInstrumentWidth}px`);
      }
      const expandedRoomValue = getComputedStyle(root)
        .getPropertyValue("--instrument-expanded-room")
        .trim();
      const expandedRoom = expandedRoomValue.endsWith("rem")
        ? Number.parseFloat(expandedRoomValue) * rootFontSize
        : Number.parseFloat(expandedRoomValue);
      if (!Number.isFinite(expandedRoom)) return;

      const shouldFitCollapse =
        instruments.getBoundingClientRect().right - tabStrip.getBoundingClientRect().left < expandedRoom;
      if (!shouldFitCollapse) instrumentsFitOverride.current = false;

      if (
        shouldFitCollapse &&
        !instrumentsFitOverride.current &&
        !instrumentsCollapsedRef.current
      ) {
        instrumentsCollapsedByFit.current = true;
        instrumentsCollapsedRef.current = true;
        setInstrumentsCollapsed(true);
      } else if (
        !shouldFitCollapse &&
        instrumentsCollapsedRef.current &&
        instrumentsCollapsedByFit.current
      ) {
        instrumentsCollapsedByFit.current = false;
        instrumentsCollapsedRef.current = false;
        setInstrumentsCollapsed(false);
      }
    };

    syncInstrumentFit();
    const resizeObserver =
      typeof ResizeObserver === "function"
        ? new ResizeObserver(syncInstrumentFit)
        : null;
    resizeObserver?.observe(root);
    root.querySelectorAll<HTMLElement>(".mw-instrument-items, .mw-instrument-collapse")
      .forEach((element) => resizeObserver?.observe(element));
    window.addEventListener("resize", syncInstrumentFit);
    return () => {
      resizeObserver?.disconnect();
      window.removeEventListener("resize", syncInstrumentFit);
    };
  }, [instrumentsCollapsed, tabs.length]);

  useEffect(() => {
    mountedRef.current = true;
    if (disposeControllerRef.current.signal.aborted) disposeControllerRef.current = new AbortController();
    return () => {
      mountedRef.current = false;
      disposeControllerRef.current.abort();
      summaryRequest.current?.controller.abort();
      recordsRequestId.current += 1;
    };
  }, []);

  const beginSummaryRequest = useCallback((): SummaryRequest => {
    summaryRequest.current?.controller.abort();
    const next = {
      epoch: (summaryRequest.current?.epoch ?? 0) + 1,
      controller: new AbortController(),
    };
    summaryRequest.current = next;
    if (!mountedRef.current) {
      next.controller.abort();
      return next;
    }
    setLoading(false);
    setRebuilding(false);
    return next;
  }, []);

  const isCurrentSummaryRequest = useCallback((request: SummaryRequest) =>
    mountedRef.current && summaryRequest.current?.epoch === request.epoch, []);

  useEffect(() => () => summaryRequest.current?.controller.abort(), []);

  const acceptSummaries = useCallback((next: MemoryArtifactSummary[], nextDirectories?: string[]) => {
    if (!mountedRef.current) return;
    artifactsRef.current = next;
    setArtifacts(next);
    if (nextDirectories) setDirectories(nextDirectories);
  }, []);

  const load = useCallback(async (onAccepted?: () => void): Promise<boolean> => {
    if (!mountedRef.current) return false;
    const request = beginSummaryRequest();
    setLoading(true);
    setError(null);
    try {
      const response = await listMemoryArtifactSummaries(config, {}, { signal: request.controller.signal });
      if (!isCurrentSummaryRequest(request)) return false;
      acceptSummaries(response.artifacts, response.directories);
      onAccepted?.();
      return true;
    } catch (reason) {
      if (!isCurrentSummaryRequest(request)) return false;
      setError(reason instanceof Error ? reason.message : String(reason));
      return false;
    } finally {
      if (isCurrentSummaryRequest(request)) setLoading(false);
    }
  }, [acceptSummaries, beginSummaryRequest, config, isCurrentSummaryRequest]);

  useEffect(() => {
    void load();
  }, [load]);

  const navigableArtifacts = useMemo(() => artifacts.filter((artifact) => isNotebookResourcePath(artifact.path)), [artifacts]);
  const titles = useMemo(
    () => new Map(artifacts.map((artifact) => [artifact.path, displayTitle(artifact)])),
    [artifacts],
  );
  const titleForPath = useCallback((path: string) => titles.get(path), [titles]);
  const workspaceTree = useMemo(() => buildWorkspaceTree(navigableArtifacts, directories), [directories, navigableArtifacts]);
  const openWorkspace = useCallback(() => setWorkspaceEmpty(false), []);
  const {
    selectedPath: selected,
    selectedArtifact: selectedMeta,
    contentDirection,
    navigationVersion,
    recentPaths,
    navigateTo,
    selectFile,
    clearSelection,
    isSelected,
  } = useMemoryNavigation({
    artifacts: navigableArtifacts,
    activeDetail,
    loading,
    activeTab,
    tabs,
    workspaceEmpty,
    layoutRef,
    mutationPendingRef,
    onWorkspaceOpened: openWorkspace,
  });

  const updateArtifactRevision = useCallback((path: string, revision: string) => {
    setArtifacts((current) => current.map((artifact) => artifact.path === path ? { ...artifact, revision } : artifact));
  }, []);

  const removeMissingArtifact = useCallback((path: string) => {
    setArtifacts((current) => current.filter((artifact) => artifact.path !== path));
    if (!mutationPendingRef.current) clearSelection(path);
  }, [clearSelection]);

  const replaceRestoredArtifact = useCallback((detail: MemoryArtifactDetail) => {
    setArtifacts((current) => current.map((artifact) => {
      if (artifact.path !== detail.path) return artifact;
      const {
        content: _content,
        editableContent: _editableContent,
        timeline: _timeline,
        frontmatter: _frontmatter,
        ...summary
      } = detail;
      return summary;
    }));
  }, []);

  const closeDiffAfterRestore = useCallback(() => setDiffClosing(true), []);
  const handleArchived = useCallback((path: string) => {
    setTabs((current) => current.filter((tabPath) => tabPath !== path));
    clearSelection(path);
  }, [clearSelection]);

  const {
    visibleDetail,
    contentLoading,
    contentError,
    contentNotice,
    contentRefreshKey,
    timelineLoading,
    timelineError,
    restorePending,
    restoreError,
    pageMutationPending,
    cache: detailCache,
    invalidate: invalidateDetail,
    clearCache: clearDetailCache,
    refresh: refreshDetail,
    retry: retryDetail,
    retryTimeline,
    clearNotice: clearContentNotice,
    handleSaved: handleMemorySaved,
    handleRebased: handleMemoryRebased,
    restore: restoreDocument,
    archive: archiveDocument,
    clearRestoreError,
    loadPreviewDetail,
  } = useMemoryDocument({
    config,
    selectedArtifact: selectedMeta,
    navigationVersion,
    activeDetail,
    setActiveDetail,
    mutationPendingRef,
    diffEvent,
    onRevision: updateArtifactRevision,
    onMissing: removeMissingArtifact,
    onRestoreArtifact: replaceRestoredArtifact,
    onArchived: handleArchived,
    refreshSummaries: load,
    beginSummaryRequest,
    isCurrentSummaryRequest,
    acceptSummaries,
    closeDiff: closeDiffAfterRestore,
    isSelected,
  });

  const openDiff = useCallback((event: PageEditEvent, trigger: HTMLElement) => {
    if (mutationPendingRef.current) return;
    if (!diffEvent) diffReturnFocus.current = trigger;
    setDiffClosing(false);
    clearRestoreError();
    setDiffEvent(event);
  }, [clearRestoreError, diffEvent]);

  const {
    editing,
    review: editReview,
    reviewClosing,
    decisions: editDecisions,
    editPending,
    reviewPending,
    error: editError,
    reviewPresentation,
    beginEditing,
    closeEditor,
    changeDraft,
    saveFrontmatter,
    finishReviewExit,
    returnFromReview: returnFromEditReview,
    rebaseConflict,
    applyReview: applyEditReview,
    setDecision: setEditDecision,
  } = useMemoryEditing({
    config,
    detail: activeDetail,
    visibleDetail,
    selectedPath: selectedMeta?.path ?? null,
    layoutRef,
    mutationPendingRef,
    onSaved: handleMemorySaved,
    onRebased: handleMemoryRebased,
  });

  const {
    open: inspectorOpen,
    pane: inspectorPane,
    detail: inspectorDetail,
    detailIsCurrent: inspectorDetailIsCurrent,
    links: currentPageLinks,
    history: currentPageHistory,
    linksLoading,
    historyLoading,
    linkError,
    historyError,
    setVisible: setInspectorVisibility,
    openPane: openInspectorPane,
    retryLinks,
    beginExternalRefresh: beginInspectorRefresh,
    acceptExternalRefresh: acceptInspectorRefresh,
  } = useMemoryInspector({
    config,
    selectedPath: selectedMeta?.path ?? null,
    activeDetail,
    visibleDetail,
    contentRefreshKey,
  });

  const onRenameResolved = useCallback(() => {
    clearDetailCache();
    void load();
  }, [clearDetailCache, load]);
  const {
    draft: renameDraft,
    openDraft: openRenameDraft,
    cancelDraft: cancelRenameDraft,
    activeApproval: activeRenameApproval,
    pending: renamePending,
    error: renameError,
    reconciliationRequired: renameReconciliationRequired,
    verification: renameVerification,
    request: requestRename,
    reconcile: reconcileRename,
    resolve: resolveRename,
  } = useWikiRenameApprovals(config, onRenameResolved);
  const renameBlocked = renameDraft != null || activeRenameApproval != null;
  const availabilityError = renameVerification === "error" ? renameError : null;
  const retryAvailabilityChecks = () => {
    if (renameVerification === "error") void reconcileRename();
  };

  useLayoutEffect(() => {
    const items = layoutRef.current?.querySelector<HTMLElement>(".mw-instrument-items");
    const motion = getBoardMotion();
    if (!items || !motion) return;
    const active = inspectorOpen
      ? items.querySelector<HTMLElement>(".oinst.on")
      : null;
    motion.tabs.sync(items, active, { animate: !reduce });
  }, [inspectorOpen, inspectorPane, reduce]);

  // Restore persisted pane widths once — drags write the vars imperatively.
  useEffect(() => {
    const layout = layoutRef.current;
    if (!layout) return;
    try {
      const ctx = parseInt(localStorage.getItem(CTX_WIDTH_KEY) ?? "", 10);
      if (Number.isFinite(ctx)) layout.style.setProperty("--mw-ctx-w", `${Math.max(240, Math.min(480, ctx))}px`);
    } catch { /* non-fatal */ }
  }, []);

  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const query = window.matchMedia(COMPACT_RAIL_QUERY);
    const sync = (event: MediaQueryListEvent) => setRailHidden(event.matches);
    query.addEventListener("change", sync);
    return () => query.removeEventListener("change", sync);
  }, []);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const key = event.key.toLowerCase();
      if ((!event.metaKey && !event.ctrlKey) || (key !== "o" && key !== "p")) return;
      if (editing != null || reviewPending) return;
      event.preventDefault();
      setSwitcherOpen(true);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [editing, reviewPending]);

  const refreshVisibleFacts = useCallback(() => {
    setRecordsRefreshKey((key) => key + 1);
  }, []);
  useMemoryVaultReconciliation({
    selectedPath: selectedMeta?.path ?? null,
    factsActive: railMode === "facts",
    invalidateDetail,
    refreshSelectedDetail: refreshDetail,
    refreshSummaries: load,
    refreshFacts: refreshVisibleFacts,
  });

  const artifactPaths = useMemo(() => new Set(navigableArtifacts.map((artifact) => artifact.path)), [navigableArtifacts]);

  const wikiHandlers = useMemo<WikiLinkHandlers>(() => ({
    exists: (target) => resolveWikiTarget(currentPageLinks, target) !== null,
    onNavigate: (target) => {
      const resolved = resolveWikiTarget(currentPageLinks, target);
      if (!resolved) return;
      navigateTo(resolved.path, resolved.anchor);
    },
    existsInline: (target) => artifactPaths.has(target),
    onNavigateInline: (target, anchor = null) => {
      if (!artifactPaths.has(target)) return;
      navigateTo(target, anchor);
    },
  }), [artifactPaths, currentPageLinks, navigateTo]);

  useEffect(() => {
    if (railMode !== "facts") return;
    const requestId = ++recordsRequestId.current;
    setRecordsLoading(true);
    setRecordsError(null);
    listMemoryItems(config, {
      limit: RECORD_PAGE_SIZE,
      offset: 0,
      status: "active",
    })
      .then((response) => {
        if (recordsRequestId.current !== requestId) return;
        setRecords(response.items);
      })
      .catch((reason) => {
        if (recordsRequestId.current === requestId) setRecordsError(reason instanceof Error ? reason.message : String(reason));
      })
      .finally(() => {
        if (recordsRequestId.current === requestId) setRecordsLoading(false);
      });
  }, [config, railMode, recordsRefreshKey]);

  const rebuild = () => {
    if (!mountedRef.current) return;
    const request = beginSummaryRequest();
    setRebuilding(true);
    clearContentNotice();
    setError(null);
    rebuildMemoryArtifactSummaries(config, { signal: request.controller.signal })
      .then((response) => {
        if (!isCurrentSummaryRequest(request)) return;
        acceptSummaries(response.artifacts);
      })
      .catch((reason) => {
        if (isCurrentSummaryRequest(request)) setError(reason instanceof Error ? reason.message : String(reason));
      })
      .finally(() => {
        if (isCurrentSummaryRequest(request)) setRebuilding(false);
      });
  };

  const pageContextEntries = useMemo<ContextMenuEntry[]>(() => {
    const page = pageContextMenu?.page;
    if (!page || !page.pageId) return [];
    return [
      {
        id: "rename",
        label: "Rename page",
        onSelect: () => openRenameDraft({
          pageId: page.pageId!,
          oldPath: page.path,
          oldTitle: page.title,
        }),
      },
      {
        id: "archive",
        label: "Archive page",
        tone: "danger",
        onSelect: () => void archiveDocument(page),
      },
    ];
  }, [archiveDocument, openRenameDraft, pageContextMenu]);

  // Every page-opening route shares the mock's append-or-activate contract.
  // A selected path is therefore always represented exactly once in the strip.
  useLayoutEffect(() => {
    const path = selectedMeta?.path ?? selected;
    if (!path) return;
    const plan = planMemoryTabOpen(tabs, activeTab, path);
    if (!plan.changed) return;
    if (plan.tabs.length !== tabs.length) setTabs(plan.tabs);
    if (plan.activeTab !== activeTab) setActiveTab(plan.activeTab);
  }, [activeTab, selected, selectedMeta?.path, tabs]);

  const switchTab = useCallback((index: number) => {
    if (index === activeTab || mutationPendingRef.current) return;
    const target = tabs[index];
    if (!target) return;
    const direction = index < activeTab ? -1 : 1;
    setActiveTab(index);
    navigateTo(target, null, direction);
  }, [activeTab, navigateTo, tabs]);

  const closeTabs = useCallback((targetIndex: number, action: MemoryTabCloseAction) => {
    if (mutationPendingRef.current) return;
    const plan = planMemoryTabClose(tabs, activeTab, targetIndex, action);
    if (!plan.changed) return;

    if (plan.empty) {
      setTabs([]);
      setActiveTab(0);
      setWorkspaceEmpty(true);
      clearSelection();
      setInspectorVisibility(false);
      return;
    }

    const nextPath = plan.tabs[plan.activeTab]!;
    const currentPath = selectedMeta?.path ?? selected;
    setTabs(plan.tabs);
    setActiveTab(plan.activeTab);
    if (nextPath !== currentPath) {
      const direction = action === "close-tab"
        ? plan.activeTab < activeTab ? -1 : 1
        : targetIndex < activeTab ? -1 : 1;
      navigateTo(nextPath, null, direction);
    }
  }, [activeTab, clearSelection, navigateTo, selected, selectedMeta?.path, setInspectorVisibility, tabs]);

  const closeTab = useCallback((index: number) => {
    closeTabs(index, "close-tab");
  }, [closeTabs]);

  const openTabContextMenu = useCallback((
    index: number,
    path: string,
    trigger: HTMLElement,
    source: ContextMenuPosition["source"],
    x: number,
    y: number,
  ) => {
    if (mutationPendingRef.current) return;
    setTabContextMenu({ index, path, trigger, source, x, y });
  }, []);

  const tabContextEntries = useMemo<ContextMenuEntry[]>(() => {
    if (!tabContextMenu) return [];
    const { index, path } = tabContextMenu;
    return [
      { id: "open", label: "Open", onSelect: () => switchTab(index) },
      { id: "copy-path", label: "Copy path", onSelect: () => void copyText(path) },
      { id: "divider", type: "separator" },
      { id: "close-tab", label: "Close tab", shortcut: "⌘W", onSelect: () => closeTabs(index, "close-tab") },
      { id: "close-others", label: "Close other tabs", onSelect: () => closeTabs(index, "close-others") },
      { id: "close-right", label: "Close tabs to the right", onSelect: () => closeTabs(index, "close-right") },
      { id: "close-all", label: "Close all tabs", onSelect: () => closeTabs(index, "close-all") },
    ];
  }, [closeTabs, switchTab, tabContextMenu]);

  const selectRailMode = useCallback((mode: MemoryRailMode) => {
    if (mutationPendingRef.current) return;
    setRailMode(mode);
    setRailHidden(false);
  }, []);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if ((!event.metaKey && !event.ctrlKey) || event.key.toLowerCase() !== "b") return;
      event.preventDefault();
      setRailHidden((hidden) => !hidden);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  return (<>
    <div
      ref={layoutRef}
      data-memory-layout="notebook"
      className={clsx(
        "memory-ws",
        railHidden && "rail-hidden",
        instrumentsCollapsed && "instruments-collapsed",
        editing && !editReview && "note-editing",
        editReview && "review-open",
        diffEvent && "diff-open",
        reviewClosing && "review-closing",
        diffClosing && "diff-closing",
      )}
    >
      <div className="memory-focus-cache">
      <div className="memory-focus-plane" inert={editReview || diffEvent ? true : undefined}>
      {/* The rail toggle stays pinned by the traffic lights while the
          floating rail moves beneath it. */}
      <SidebarToggle
        hidden={railHidden}
        onToggle={() => setRailHidden((hidden) => !hidden)}
      />
      <nav
        data-memory-zone="rail"
        data-page-enter-item="chrome"
        aria-label="Memory notebook"
        className="mw-rail"
      >
        <div className="mw-rail-drag" aria-hidden />
        {/* The notebook rail IS the app sidebar in this workspace — same
            shared width (prefs.sidebarWidth), so a drag here moves every
            other rail with it instead of forking a memory-local width. */}
        <SidebarResizeHandle />
        <NotebookRail
          mode={railMode}
          tree={workspaceTree}
          allNotes={navigableArtifacts}
          records={records}
          selectedPath={selectedMeta?.path ?? null}
          loading={loading}
          error={error}
          rebuilding={rebuilding}
          recordsLoading={recordsLoading}
          recordsError={recordsError}
          navigationDisabled={reviewPending}
          onModeChange={selectRailMode}
          onSelect={selectFile}
          onRetry={() => void load()}
          onRetryRecords={() => setRecordsRefreshKey((key) => key + 1)}
          onRebuild={rebuild}
        />
      </nav>

      <div data-memory-zone="workspace" className="mw-doc">
        {availabilityError && !renameBlocked && (
          <div data-memory-availability-error role="alert" className="memory-edit-review__error">
            <span>{availabilityError}</span>{" "}
            <button type="button" onClick={retryAvailabilityChecks}>Retry checks</button>
          </div>
        )}
        {tabs.length > 0 && (
          <MemoryDocumentTabs
            paths={tabs}
            titleForPath={titleForPath}
            activeIndex={activeTab}
            disabled={reviewPending}
            onSelect={switchTab}
            onClose={closeTab}
            onOpenContextMenu={openTabContextMenu}
          />
        )}

        <ContextMenu
          state={tabContextMenu}
          onClose={() => setTabContextMenu(null)}
          entries={tabContextEntries}
        />
        <ContextMenu
          state={pageContextMenu}
          onClose={() => setPageContextMenu(null)}
          entries={pageContextEntries}
          ariaLabel="Page actions"
        />

        {!workspaceEmpty && (
        <div className={clsx("ornament ready mw-instruments", instrumentsCollapsed && "collapsed")} data-page-enter-item="chrome" aria-label="Page instruments">
          <span className="ornament-surface mw-instrument-surface" aria-hidden />
          <div className="ornament-items mw-instrument-items" inert={instrumentsCollapsed || undefined}>
            <button
              type="button"
              className={clsx("oinst mw-instrument", inspectorOpen && inspectorPane === "links" && "on")}
              disabled={reviewPending}
              title="Links"
              aria-label={inspectorOpen && inspectorPane === "links" ? "Close links" : "Open links"}
              aria-pressed={inspectorOpen && inspectorPane === "links"}
              onClick={() => {
                if (inspectorOpen && inspectorPane === "links") setInspectorVisibility(false);
                else openInspectorPane("links");
              }}
            >
              <Link01 className="mw-instrument-icon" size={15} aria-hidden />
              <span>links</span>
              <span className="oc mw-instrument-count">{currentPageLinks?.totalOutgoing ?? 0}</span>
            </button>
            <button
              type="button"
              className={clsx("oinst mw-instrument", inspectorOpen && inspectorPane === "records" && "on")}
              disabled={reviewPending}
              aria-label={inspectorOpen && inspectorPane === "records" ? "Close records" : "Open records"}
              aria-pressed={inspectorOpen && inspectorPane === "records"}
              title="Page records"
              onClick={() => {
                if (inspectorOpen && inspectorPane === "records") setInspectorVisibility(false);
                else openInspectorPane("records");
              }}
            >
              <LeftToRightListBullet className="mw-instrument-icon" size={15} aria-hidden />
              <span>records</span>
              <span className="oc mw-instrument-count">{visibleDetail?.recordCount ?? 0}</span>
            </button>
            <button
              type="button"
              className={clsx("oinst mw-instrument", inspectorOpen && inspectorPane === "activity" && "on")}
              disabled={reviewPending}
              aria-label={inspectorOpen && inspectorPane === "activity" ? "Close activity" : "Open activity"}
              aria-pressed={inspectorOpen && inspectorPane === "activity"}
              onClick={() => {
                if (inspectorOpen && inspectorPane === "activity") setInspectorVisibility(false);
                else openInspectorPane("activity");
              }}
            >
              <Clock className="mw-instrument-icon" size={15} aria-hidden />
              <span>activity</span>
            </button>
            <span className="odiv mw-instrument-divider" aria-hidden />
            <button
              type="button"
              className={clsx("mw-instrument mw-instrument-edit", editing && !editReview && "on")}
              title={!editing && visibleDetail && (!visibleDetail.editable || visibleDetail.editableContent == null)
                ? visibleDetail.readonlyReason ?? "Read-only page"
                : "Edit source (⌘E)"}
              aria-label="Edit memory note"
              aria-pressed={editing != null && !editReview}
              disabled={pageMutationPending || (!editing && (!visibleDetail?.editable || visibleDetail.editableContent == null))}
              onClick={() => {
                if (editing) closeEditor();
                else beginEditing();
              }}
            >
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <use href="#dp-edit" />
              </svg>
            </button>
            {!editing && visibleDetail?.editable && visibleDetail.pageId && (
              <button
                type="button"
                className="mw-instrument"
                title="Page actions"
                aria-label="Page actions"
                aria-haspopup="menu"
                disabled={pageMutationPending || reviewPending}
                onClick={(event) => {
                  const rect = event.currentTarget.getBoundingClientRect();
                  setPageContextMenu({
                    page: visibleDetail,
                    trigger: event.currentTarget,
                    source: "pointer",
                    x: rect.left,
                    y: rect.bottom,
                  });
                }}
              >
                <MoreHorizontal size={16} aria-hidden />
              </button>
            )}
          </div>
          <button
            type="button"
            className="mw-instrument mw-instrument-collapse"
            title={instrumentsCollapsed ? "Expand instruments" : "Compact instruments"}
            aria-label={instrumentsCollapsed ? "Expand instruments" : "Compact instruments"}
            aria-expanded={!instrumentsCollapsed}
            onClick={() => {
              if (instrumentsCollapsed && instrumentsCollapsedByFit.current) {
                instrumentsFitOverride.current = true;
              } else if (!instrumentsCollapsed) {
                instrumentsFitOverride.current = false;
              }
              instrumentsCollapsedByFit.current = false;
              instrumentsCollapsedRef.current = !instrumentsCollapsed;
              setInstrumentsCollapsed(!instrumentsCollapsed);
            }}
          >
            <IconSwap
              state={instrumentsCollapsed ? "b" : "a"}
              iconA={<ChevronRight size={16} aria-hidden />}
              iconB={<ChevronLeft size={16} aria-hidden />}
            />
          </button>
        </div>
        )}
        <main
          id="memory-note-panel"
          aria-label={editing ? "Memory editor" : "Memory note"}
          aria-labelledby={tabs.length > 0 ? `memory-note-tab-${activeTab}` : undefined}
          className="mw-main relative min-h-0 flex-1 overflow-hidden"
        >
          {editing ? (
            <div className="absolute inset-0 min-h-0">
              <MemoryEditor
                path={editing.path}
                title={titleForPath(editing.path) ?? editing.path}
                baseContent={editing.baseContent}
                value={editing.draftContent}
                saving={editPending}
                error={editError}
                onClose={closeEditor}
                onChange={changeDraft}
              />
            </div>
          ) : (
            <AnimatePresence initial={false} mode="wait" custom={contentDirection}>
              <motion.div
                key={selectedMeta?.path ?? "empty"}
                custom={contentDirection}
                variants={MEMORY_PAGE_SWAP_VARIANTS}
                className="absolute inset-0 min-h-0"
                initial={reduce ? false : "enter"}
                animate="center"
                exit={reduce
                  ? { opacity: 0, transition: { duration: MOTION.reduced } }
                  : "exit"}
              >
              {workspaceEmpty ? (
                <div className="mw-empty-workspace">
                  <h1>No page open</h1>
                  <p>Choose a page from Files to open it here.</p>
                </div>
              ) : (
                <MemoryNote
                  summary={selectedMeta}
                  detail={visibleDetail}
                  listLoading={loading}
                  contentLoading={contentLoading}
                  contentNotice={contentNotice}
                  contentError={contentError}
                  wikiHandlers={wikiHandlers}
                  onFrontmatterChange={visibleDetail?.editable && visibleDetail.editableContent != null ? saveFrontmatter : undefined}
                  onRetry={() => {
                    if (selectedMeta) retryDetail(selectedMeta.path);
                  }}
                />
              )}
              </motion.div>
            </AnimatePresence>
          )}
        {!workspaceEmpty && !editing && !editReview && (
          <WikiLinkPreview
            containerRef={layoutRef}
            links={currentPageLinks}
            summaries={navigableArtifacts}
            cache={detailCache}
            loadDetail={loadPreviewDetail}
          />
        )}
        </main>
      </div>

      <div data-memory-zone="inspector">
        <PeekSurface
          open={inspectorOpen}
          onClose={() => setInspectorVisibility(false)}
          ariaLabel="Page peek"
          layer="memory-context"
          className="mw-context surface-peek"
        >
        <div className="mw-context-plane">
          <PaneResizeHandle layoutRef={layoutRef} cssVar="--mw-ctx-w" storageKey={CTX_WIDTH_KEY} min={240} max={480} defaultWidth={296} edge="start" label="Resize context panel" />
          {inspectorDetail ? (
            <MemoryInspector
              page={inspectorDetail}
              links={currentPageLinks}
              history={currentPageHistory}
              linksLoading={linksLoading || !inspectorDetailIsCurrent}
              historyLoading={historyLoading || !inspectorDetailIsCurrent}
              linkError={inspectorDetailIsCurrent ? linkError : null}
              historyError={inspectorDetailIsCurrent ? historyError : null}
              timelineLoading={timelineLoading || !inspectorDetailIsCurrent}
              timelineError={inspectorDetailIsCurrent ? timelineError : null}
              onRetryTimeline={retryTimeline}
              navigationDisabled={reviewPending || !inspectorDetailIsCurrent}
              titleForPath={titleForPath}
              onNavigate={navigateTo}
              onRetryLinks={retryLinks}
              onOpenDiff={(event, trigger) => {
                void openDiff(event, trigger);
              }}
              activePane={inspectorPane}
              onClose={() => setInspectorVisibility(false)}
            />
          ) : (
            <p className="mw-ctx-empty mw-ctx-loading">Loading…</p>
          )}
        </div>
        </PeekSurface>
      </div>

      <MemoryQuickSwitcher
        open={switcherOpen}
        artifacts={navigableArtifacts}
        recentPaths={recentPaths}
        onClose={() => setSwitcherOpen(false)}
        onSelect={(path) => {
          setSwitcherOpen(false);
          navigateTo(path, null);
        }}
      />
      </div>
      </div>

      {diffEvent && (
        <MemoryDiffOverlay
          event={diffEvent}
          path={diffEvent.path}
          open={!diffClosing}
          currentPageVersion={visibleDetail?.path === diffEvent.path ? visibleDetail.revision : null}
          restorePending={restorePending}
          restoreError={restoreError}
          onRestore={visibleDetail?.path === diffEvent.path && visibleDetail.repositoryHead != null
            ? () => {
              void restoreDocument({
                begin: beginInspectorRefresh,
                accept: acceptInspectorRefresh,
              });
            }
            : undefined}
          onOpenChange={(open) => setDiffClosing(!open)}
          onExitComplete={() => {
            setDiffEvent(null);
            setDiffClosing(false);
            clearRestoreError();
          }}
        />
      )}

      {editReview && reviewPresentation && (
        <MemoryEditReview
          kind={editReview.kind}
          reviewId={editReview.kind === "preview" ? editReview.preview.id : `conflict:${editReview.conflict.currentRevision}`}
          path={reviewPresentation.path}
          baseContent={reviewPresentation.baseContent}
          draftContent={reviewPresentation.draftContent}
          operations={reviewPresentation.operations}
          decisions={editDecisions}
          analysisPending={reviewPresentation.analysisPending}
          conflict={editReview.kind === "conflict" ? editReview.conflict : undefined}
          pending={reviewPending}
          error={editError}
          closing={reviewClosing}
          onExitComplete={finishReviewExit}
          onDecision={setEditDecision}
          onApply={() => void applyEditReview()}
          onCancel={returnFromEditReview}
          onRebase={rebaseConflict}
        />
      )}
    </div>
    {renameBlocked && (
      <WikiRenameApprovalSheet
        draft={renameDraft ?? undefined}
        approval={activeRenameApproval ?? undefined}
        pending={renamePending}
        reconciliationRequired={renameReconciliationRequired}
        error={renameError}
        onCancel={cancelRenameDraft}
        onRequest={(input) => void requestRename(input)}
        onReconcile={() => void reconcileRename()}
        onAccept={(approvalId) => void resolveRename(approvalId, "accept")}
        onReject={(approvalId) => void resolveRename(approvalId, "reject")}
      />
    )}
    {renameBlocked && !renameDraft && !activeRenameApproval && (
      <WikiRenameApprovalStatusSheet
        error={renameVerification === "error" ? renameError : null}
        onRetry={() => void reconcileRename()}
      />
    )}
  </>
  );
}
