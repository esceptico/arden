import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Clock, LeftToRightListBullet, Link01 } from "@/components/icons";
import clsx from "clsx";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import {
  CONTENT_ENTER_TRANSITION,
  CONTENT_EXIT_TRANSITION,
  CONTENT_SWAP_VARIANTS,
  MOTION,
} from "@/lib/tokens/motion";
import { useStore } from "@/stores";
import { ApiError, type AppConfig } from "@/api/core";
import type { WikiLinkHandlers } from "@/lib/wikilink";
import {
  getPageHistory,
  getPageLinks,
  applyPageEdit,
  listMemoryArtifactSummaries,
  previewPageEdit,
  readMemoryArtifactDetail,
  rebuildMemoryArtifactSummaries,
} from "@/api/memoryArtifacts";
import type { DiffReviewDecision, DiffReviewOperation } from "@/components/ui/diffReviewTypes";
import { listMemoryItems, type MemoryItem } from "@/api/memoryItems";
import { IconSwap } from "@/components/ui/IconSwap";
import { ContextMenu, type ContextMenuEntry, type ContextMenuPosition } from "@/components/ui/ContextMenu";
import { SidebarToggle } from "@/components/ui/SidebarToggle";
import { PeekSurface } from "@/components/workspace/PeekSurface";
import { NotebookRail, type MemoryRailMode } from "@/features/memory/components/NotebookRail";
import { PaneResizeHandle } from "@/components/workspace/PaneResizeHandle";
import { MemoryNote } from "@/features/memory/components/MemoryNote";
import {
  loadMemoryInspectorPane,
  MemoryInspector,
  persistMemoryInspectorPane,
  type MemoryInspectorPane,
} from "@/features/memory/components/MemoryInspector";
import { MemoryEditor } from "@/features/memory/components/MemoryEditor";
import { MemoryEditReview, type MemoryConflict } from "@/features/memory/components/MemoryEditReview";
import { MemoryDiffOverlay } from "@/features/memory/components/MemoryDiffOverlay";
import { MemoryQuickSwitcher } from "@/features/memory/components/MemoryQuickSwitcher";
import { MemoryDocumentTabs } from "@/features/memory/components/MemoryDocumentTabs";
import { WikiLinkPreview } from "@/features/memory/components/WikiLinkPreview";
import { ArtifactCache } from "@/features/memory/lib/artifactCache";
import { NavigationHistory, type NavigationLocation } from "@/features/memory/lib/navigationHistory";
import { isMissingArtifactError, resolveWikiTarget } from "@/features/memory/lib/wikiResolution";
import { clearDraft, clearDraftIfMatches, draftKey, getDraft, setDraft } from "@/features/memory/lib/draftStore";
import { isNotebookResourcePath } from "@/features/memory/lib/notebookIndex";
import { serializeFrontmatter, splitFrontmatter } from "@/features/memory/lib/format";
import { getBoardMotion } from "@/lib/boardMotion";
import { buildWorkspaceTree, stem } from "@/features/memory/lib/workspaceTree";
import { planMemoryTabClose, planMemoryTabOpen, type MemoryTabCloseAction } from "@/features/memory/lib/tabContext";
import { copyText } from "@/lib/clipboard";
import type { MemoryFrontmatter } from "@/features/memory/components/MemoryProperties";
import type { MemoryArtifactDetail, MemoryArtifactSummary, MemoryOperation, PageEditEvent, PageEditHistory, PageEditPreview, PageLinks } from "@/features/memory/lib/notebookTypes";

const RECORD_PAGE_SIZE = 100;
const RAIL_WIDTH_KEY = "arden.desktop.memory.railWidth";
const CTX_WIDTH_KEY = "arden.desktop.memory.ctxWidth";
const LAST_PATH_KEY = "arden.desktop.memory.lastPath";
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

// Inspector open/closed is conceptually session state, not a Prefs field —
// persisted separately to localStorage so the panel doesn't silently reset
// on reload. Mirrors the SKIP_APPROVALS_KEY pattern in stores/prefs.ts.
const INSPECTOR_OPEN_KEY = "arden.desktop.memory.inspectorOpen";

function loadInspectorOpen(): boolean {
  try {
    return localStorage.getItem(INSPECTOR_OPEN_KEY) === "true";
  } catch {
    return false;
  }
}

function persistInspectorOpen(value: boolean): void {
  try {
    localStorage.setItem(INSPECTOR_OPEN_KEY, value ? "true" : "false");
  } catch {
    /* localStorage unavailable — non-fatal */
  }
}

function loadCompactRail(): boolean {
  return typeof window.matchMedia === "function"
    && window.matchMedia(COMPACT_RAIL_QUERY).matches;
}

interface SummaryRequest {
  epoch: number;
  controller: AbortController;
}

interface MemoryFocusToken {
  kind: "wikilink" | "inline";
  target: string;
  occurrence: number;
}

interface EditingSession {
  path: string;
  title: string;
  baseRevision: string;
  baseContent: string;
  draftContent: string;
}

type ReviewState =
  | { kind: "preview"; generation: number; snapshot: EditSnapshot; preview: PageEditPreview }
  | { kind: "conflict"; generation: number; snapshot: EditSnapshot; conflict: MemoryConflict };

interface EditSnapshot {
  requestGeneration: number;
  path: string;
  baseRevision: string;
  baseContent: string;
  candidateContent: string;
  draftKey: string;
}

function editingMatchesSnapshot(editing: EditingSession | null, snapshot: EditSnapshot): boolean {
  return editing?.path === snapshot.path
    && editing.baseRevision === snapshot.baseRevision
    && editing.baseContent === snapshot.baseContent
    && editing.draftContent === snapshot.candidateContent;
}

function snapshotEditing(editing: EditingSession, requestGeneration: number): EditSnapshot {
  return {
    requestGeneration,
    path: editing.path,
    baseRevision: editing.baseRevision,
    baseContent: editing.baseContent,
    candidateContent: editing.draftContent,
    draftKey: draftKey(editing.path, editing.baseRevision),
  };
}

function conflictFromDrift(
  draft: EditingSession,
  detail: MemoryArtifactDetail,
  requestGeneration: number,
  generation: number,
): ReviewState {
  return {
    kind: "conflict",
    generation,
    snapshot: snapshotEditing(draft, requestGeneration),
    conflict: {
      currentRevision: detail.revision,
      currentContent: detail.editableContent ?? detail.content,
    },
  };
}

function diffOperation(operation: MemoryOperation): DiffReviewOperation {
  switch (operation.kind) {
    case "ADD":
      return { kind: "ADD", id: operation.id, text: operation.text, memoryKind: operation.memoryKind, scope: operation.scope };
    case "SUPERSEDE":
    case "MERGE":
      return { kind: operation.kind, id: operation.id, text: operation.text, memoryKind: operation.memoryKind, scope: operation.scope, targetIds: operation.targetIds };
    case "RETRACT":
      return { kind: "RETRACT", id: operation.id, targetIds: operation.targetIds };
    case "NOOP":
      return { kind: "NOOP", id: operation.id, reason: operation.reason };
    case "ASK":
      return { kind: "ASK", id: operation.id, question: operation.question, targetIds: operation.targetIds };
  }
}

function revisionConflict(reason: unknown): MemoryConflict | null {
  if (!(reason instanceof ApiError) || reason.status !== 409 || !reason.data || typeof reason.data !== "object") return null;
  const detail = (reason.data as { detail?: unknown }).detail;
  if (!detail || typeof detail !== "object") return null;
  const value = detail as { error?: unknown; current_revision?: unknown; current_content?: unknown };
  if (value.error !== "page_revision_conflict" || typeof value.current_revision !== "string" || typeof value.current_content !== "string") return null;
  return { currentRevision: value.current_revision, currentContent: value.current_content };
}

const INTERACTIVE_SELECTOR = [
  "a[href]",
  "button",
  "input",
  "textarea",
  "select",
  "summary",
  "[contenteditable]:not([contenteditable='false'])",
  "[role]",
  "[tabindex]:not([tabindex='-1'])",
].join(",");

function isInteractiveShortcutTarget(element: Element | null): boolean {
  return element instanceof HTMLElement && element.closest(INTERACTIVE_SELECTOR) != null;
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

export function ArtifactMemoryView({ config }: { config: AppConfig }) {
  const reduce = useReducedMotion();
  const [artifacts, setArtifacts] = useState<MemoryArtifactSummary[]>([]);
  const [directories, setDirectories] = useState<string[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [activeDetail, setActiveDetail] = useState<MemoryArtifactDetail | null>(null);
  const [contentLoading, setContentLoading] = useState(false);
  const [contentError, setContentError] = useState<string | null>(null);
  const [contentNotice, setContentNotice] = useState<string | null>(null);
  const [contentRefreshKey, setContentRefreshKey] = useState(0);
  const [loading, setLoading] = useState(true);
  const [rebuilding, setRebuilding] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [records, setRecords] = useState<MemoryItem[]>([]);
  const [recordsError, setRecordsError] = useState<string | null>(null);
  const [recordsLoading, setRecordsLoading] = useState(false);
  const [recordsRefreshKey, setRecordsRefreshKey] = useState(0);
  const [inspectorOpen, setInspectorOpen] = useState(() => loadInspectorOpen());
  const [inspectorPane, setInspectorPane] = useState<MemoryInspectorPane>(loadMemoryInspectorPane);
  const [pageLinks, setPageLinks] = useState<PageLinks | null>(null);
  const [pageHistory, setPageHistory] = useState<PageEditHistory | null>(null);
  const [linksLoading, setLinksLoading] = useState(false);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [linkError, setLinkError] = useState<string | null>(null);
  const [linksRefreshKey, setLinksRefreshKey] = useState(0);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [historyVersion, setHistoryVersion] = useState(0);
  const [pageHistoryPath, setPageHistoryPath] = useState<string | null>(null);
  const [diffEvent, setDiffEvent] = useState<PageEditEvent | null>(null);
  const [diffClosing, setDiffClosing] = useState(false);
  const [editing, setEditing] = useState<EditingSession | null>(null);
  const [editReview, setEditReview] = useState<ReviewState | null>(null);
  const [reviewClosing, setReviewClosing] = useState(false);
  const [editDecisions, setEditDecisions] = useState<Record<string, DiffReviewDecision>>({});
  const [editPending, setEditPending] = useState(false);
  const [reviewPending, setReviewPending] = useState(false);
  const [editError, setEditError] = useState<string | null>(null);
  const [switcherOpen, setSwitcherOpen] = useState(false);
  const [railHidden, setRailHidden] = useState(loadCompactRail);
  const [instrumentsCollapsed, setInstrumentsCollapsed] = useState(false);
  const [railMode, setRailMode] = useState<MemoryRailMode>("files");
  const [tabs, setTabs] = useState<string[]>([]);
  const [activeTab, setActiveTab] = useState(0);
  const [workspaceEmpty, setWorkspaceEmpty] = useState(false);
  const [tabContextMenu, setTabContextMenu] = useState<MemoryTabContextMenuState | null>(null);
  const [contentDirection, setContentDirection] = useState(1);

  const summaryRequest = useRef<SummaryRequest | null>(null);
  const recordsRequestId = useRef(0);
  const detailCache = useRef(new ArtifactCache());
  const navigationHistory = useRef(new NavigationHistory());
  const pendingRestore = useRef<NavigationLocation | null>(null);
  const linksRequestId = useRef(0);
  const historyRequestId = useRef(0);
  const retainedInspectorDetail = useRef<MemoryArtifactDetail | null>(null);
  const artifactsRef = useRef<MemoryArtifactSummary[]>([]);
  const selectedMetaRef = useRef<MemoryArtifactSummary | null>(null);
  const layoutRef = useRef<HTMLDivElement>(null);
  const instrumentsCollapsedRef = useRef(instrumentsCollapsed);
  const instrumentsCollapsedByFit = useRef(false);
  const instrumentsFitOverride = useRef(false);
  const editingRef = useRef<EditingSession | null>(null);
  const editReviewRef = useRef<ReviewState | null>(null);
  const editRequestGeneration = useRef(0);
  const editPreviewController = useRef<AbortController | null>(null);
  const reviewExitAction = useRef<(() => void) | null>(null);
  const reviewGeneration = useRef(0);
  const applyGeneration = useRef(0);
  const mutationPendingRef = useRef(false);
  const memoryVaultChangesRef = useRef(useStore.getState().memoryVaultChanges);
  const processedMemoryChangeSeqs = useRef(new Set<number>());
  const memoryChangeDrainRunning = useRef(false);
  const memoryChangeDrainRequested = useRef(false);
  const memoryChangeDrainRef = useRef<(() => Promise<void>) | null>(null);
  const diffReturnFocus = useRef<HTMLElement | null>(null);
  const restoreNoteFocus = useRef(false);
  const mountedRef = useRef(true);
  const disposeControllerRef = useRef(new AbortController());

  artifactsRef.current = artifacts;
  editingRef.current = editing;
  editReviewRef.current = editReview;
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

  // Sliding selection indicator for links/records/activity — same engine and
  // motion as the sidebar Files/Notebook/Facts switch (MemoryDocumentTabs'
  // sync via bindTabs; this reuses the lower-level sync() directly instead
  // of bind(), since these three buttons keep their own onClick toggle-to-
  // close behavior — bindTabs' select() has no "click active tab to
  // deselect" concept. A falsy target hides the indicator (panel closed).
  useLayoutEffect(() => {
    const items = layoutRef.current?.querySelector<HTMLElement>(".mw-instrument-items");
    const motion = getBoardMotion();
    if (!items || !motion) return;
    const active = inspectorOpen
      ? items.querySelector<HTMLElement>(".oinst.on")
      : null;
    motion.tabs.sync(items, active, { animate: !reduce });
  }, [inspectorOpen, inspectorPane, reduce]);

  useEffect(() => {
    mountedRef.current = true;
    if (disposeControllerRef.current.signal.aborted) disposeControllerRef.current = new AbortController();
    return () => {
      mountedRef.current = false;
      disposeControllerRef.current.abort();
      summaryRequest.current?.controller.abort();
      linksRequestId.current += 1;
      historyRequestId.current += 1;
      recordsRequestId.current += 1;
      editRequestGeneration.current += 1;
      editPreviewController.current?.abort();
    };
  }, []);

  const openDiff = useCallback((event: PageEditEvent, trigger: HTMLElement) => {
    if (!diffEvent) diffReturnFocus.current = trigger;
    setDiffClosing(false);
    setDiffEvent(event);
  }, [diffEvent]);

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

  const load = useCallback(async (): Promise<boolean> => {
    if (!mountedRef.current) return false;
    const request = beginSummaryRequest();
    setLoading(true);
    setError(null);
    try {
      const response = await listMemoryArtifactSummaries(config, {}, { signal: request.controller.signal });
      if (!isCurrentSummaryRequest(request)) return false;
      acceptSummaries(response.artifacts, response.directories);
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
  const workspaceTree = useMemo(() => buildWorkspaceTree(navigableArtifacts, directories), [directories, navigableArtifacts]);
  const selectedMeta = navigableArtifacts.find((artifact) => artifact.path === selected) ?? null;
  selectedMetaRef.current = selectedMeta;

  useEffect(() => {
    if (!selected) return;
    try {
      localStorage.setItem(LAST_PATH_KEY, selected);
    } catch { /* non-fatal */ }
  }, [selected]);

  // Restore persisted pane widths once — drags write the vars imperatively.
  useEffect(() => {
    const layout = layoutRef.current;
    if (!layout) return;
    try {
      const rail = parseInt(localStorage.getItem(RAIL_WIDTH_KEY) ?? "", 10);
      if (Number.isFinite(rail)) layout.style.setProperty("--mw-rail-w", `${Math.max(220, Math.min(400, rail))}px`);
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

  useLayoutEffect(() => {
    if (loading) return;
    if (mutationPendingRef.current) return;
    if (workspaceEmpty && tabs.length === 0) {
      if (selected !== null) setSelected(null);
      return;
    }
    if (selected && navigableArtifacts.some((artifact) => artifact.path === selected)) return;
    let stored: string | null = null;
    try {
      stored = localStorage.getItem(LAST_PATH_KEY);
    } catch { /* non-fatal */ }
    const restored = stored != null && navigableArtifacts.some((artifact) => artifact.path === stored) ? stored : null;
    // The active tab is visible state. Keep it and the document selection
    // together when a list refresh temporarily clears the selected path.
    const activeTabPath = tabs[activeTab];
    const tabFallback = activeTabPath != null && navigableArtifacts.some((artifact) => artifact.path === activeTabPath)
      ? activeTabPath
      : null;
    const fallback = tabFallback
      ?? restored
      ?? (navigableArtifacts.some((artifact) => artifact.path === "index.md") ? "index.md" : null)
      ?? navigableArtifacts[0]?.path
      ?? null;
    if (fallback) {
      navigationHistory.current.push({ path: fallback, anchor: null, scrollTop: 0, focusSelector: null });
      setHistoryVersion((version) => version + 1);
    }
    setSelected(fallback);
  }, [activeTab, loading, navigableArtifacts, reviewPending, selected, tabs, workspaceEmpty]);

  const setInspectorVisibility = useCallback((open: boolean) => {
    persistInspectorOpen(open);
    setInspectorOpen(open);
  }, []);

  const openInspectorPane = useCallback((pane: MemoryInspectorPane) => {
    persistMemoryInspectorPane(pane);
    setInspectorPane(pane);
    setInspectorVisibility(true);
  }, [setInspectorVisibility]);

  const currentLocation = useCallback((): NavigationLocation | null => {
    const path = selectedMeta?.path ?? selected;
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
  }, [selected, selectedMeta?.path]);

  const navigateTo = useCallback((
    path: string,
    anchor: string | null = null,
    direction = 1,
  ) => {
    if (mutationPendingRef.current) return;
    if (workspaceEmpty) setWorkspaceEmpty(false);
    const destination = navigationHistory.current.current;
    const currentPath = selectedMetaRef.current?.path ?? selected;
    if (destination?.path === path && destination.anchor === anchor && currentPath === path) {
      if (anchor) {
        pendingRestore.current = destination;
        setHistoryVersion((version) => version + 1);
      }
      return;
    }
    const current = currentLocation();
    if (current) navigationHistory.current.replaceCurrent(current);
    const next = { path, anchor, scrollTop: 0, focusSelector: null };
    navigationHistory.current.push(next);
    pendingRestore.current = next;
    setHistoryVersion((version) => version + 1);
    setContentNotice(null);
    setContentDirection(direction < 0 ? -1 : 1);
    setSelected(path);
  }, [currentLocation, selected, workspaceEmpty]);

  const selectFile = useCallback((path: string, direction: number) => {
    navigateTo(path, null, direction);
  }, [navigateTo]);

  const moveHistory = useCallback((movement: "back" | "forward") => {
    if (mutationPendingRef.current) return;
    const current = currentLocation();
    if (current) navigationHistory.current.replaceCurrent(current);
    const location = movement === "back" ? navigationHistory.current.back() : navigationHistory.current.forward();
    if (!location) return;
    pendingRestore.current = location;
    setHistoryVersion((version) => version + 1);
    setContentNotice(null);
    setContentDirection(movement === "back" ? -1 : 1);
    setSelected(location.path);
  }, [currentLocation]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if ((!event.metaKey && !event.ctrlKey) || (event.key !== "[" && event.key !== "]")) return;
      const targets = [event.target, document.activeElement];
      if (targets.some((target) => target instanceof HTMLElement && (
        target.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)
      ))) return;
      event.preventDefault();
      moveHistory(event.key === "[" ? "back" : "forward");
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [moveHistory]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const key = event.key.toLowerCase();
      if ((!event.metaKey && !event.ctrlKey) || (key !== "o" && key !== "p")) return;
      if (editingRef.current != null || reviewPending) return;
      event.preventDefault();
      setSwitcherOpen(true);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [reviewPending]);

  const recentPaths = useMemo(() => {
    const currentPath = selectedMeta?.path ?? selected;
    const seen = new Set<string>();
    const paths: string[] = [];
    for (const location of navigationHistory.current.locations()) {
      if (location.path === currentPath || seen.has(location.path)) continue;
      seen.add(location.path);
      paths.push(location.path);
    }
    return paths;
  }, [historyVersion, selected, selectedMeta?.path]);

  useEffect(() => {
    const controller = new AbortController();
    if (!selectedMeta) {
      setActiveDetail(null);
      setContentLoading(false);
      return;
    }
    const cached = detailCache.current.get(selectedMeta.path, selectedMeta.revision);
    if (cached) {
      setActiveDetail(cached);
      setContentError(null);
      setContentLoading(false);
      return;
    }
    setActiveDetail((current) => current?.path === selectedMeta.path ? current : null);
    setContentLoading(true);
    setContentError(null);
    readMemoryArtifactDetail(config, selectedMeta.path, { signal: controller.signal })
      .then((response) => {
        if (controller.signal.aborted || !mountedRef.current) return;
        const detail = response.artifact;
        detailCache.current.set(detail);
        setActiveDetail(detail);
        if (detail.revision !== selectedMeta.revision) {
          setArtifacts((current) => current.map((artifact) => artifact.path === detail.path
            ? { ...artifact, revision: detail.revision }
            : artifact));
        }
      })
      .catch((reason) => {
        if (controller.signal.aborted || !mountedRef.current) return;
        if (isMissingArtifactError(reason)) {
          const missingPath = selectedMeta.path;
          setArtifacts((current) => current.filter((artifact) => artifact.path !== missingPath));
          if (!mutationPendingRef.current) setSelected((current) => current === missingPath ? null : current);
          setActiveDetail(null);
          setContentNotice("That memory note changed or disappeared; refreshed the index.");
          void load();
          return;
        }
        setContentError(reason instanceof Error ? reason.message : String(reason));
      })
      .finally(() => {
        if (!controller.signal.aborted && mountedRef.current) setContentLoading(false);
      });
    return () => controller.abort();
  }, [config, contentRefreshKey, load, selectedMeta?.path, selectedMeta?.revision]);

  useEffect(() => {
    if (!editing || !activeDetail || activeDetail.path !== editing.path || activeDetail.revision === editing.baseRevision) return;
    if (reviewPending) return;
    const currentContent = activeDetail.editableContent ?? activeDetail.content;
    if (editing.draftContent === editing.baseContent) {
      clearDraft(editing.path, editing.baseRevision);
      setEditing({
        path: activeDetail.path,
        title: activeDetail.title,
        baseRevision: activeDetail.revision,
        baseContent: currentContent,
        draftContent: currentContent,
      });
      return;
    }
    setEditReview((current) => current?.kind === "conflict" && current.conflict.currentRevision === activeDetail.revision
      ? current
      : conflictFromDrift(editing, activeDetail, editRequestGeneration.current, ++reviewGeneration.current));
  }, [activeDetail, editing, reviewPending]);

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
  }, [activeDetail, historyVersion]);

  const memoryVaultVersion = useStore((state) => state.memoryVaultVersion);
  const memoryVaultChanges = useStore((state) => state.memoryVaultChanges);
  const observedVaultVersion = useRef(memoryVaultVersion);
  const observedVaultChanges = useRef(memoryVaultChanges);

  const drainMemoryChanges = useCallback(async () => {
    if (memoryChangeDrainRunning.current) {
      memoryChangeDrainRequested.current = true;
      return;
    }
    memoryChangeDrainRunning.current = true;
    try {
      while (true) {
        const next = memoryVaultChangesRef.current.find((change) => !processedMemoryChangeSeqs.current.has(change.seq));
        if (!next) break;
        const current = selectedMetaRef.current;
        if (current && next.paths.includes(current.path)) {
          detailCache.current.invalidatePath(current.path);
          if (mountedRef.current) setContentRefreshKey((key) => key + 1);
          await load();
          if (mountedRef.current && railMode === "facts") setRecordsRefreshKey((key) => key + 1);
        }
        processedMemoryChangeSeqs.current.add(next.seq);
      }
    } finally {
      memoryChangeDrainRunning.current = false;
      if (memoryChangeDrainRequested.current) {
        memoryChangeDrainRequested.current = false;
        queueMicrotask(() => void memoryChangeDrainRef.current?.());
      }
    }
  }, [load, railMode]);
  memoryChangeDrainRef.current = drainMemoryChanges;

  useEffect(() => {
    memoryVaultChangesRef.current = memoryVaultChanges;
    const retained = new Set(memoryVaultChanges.map((change) => change.seq));
    processedMemoryChangeSeqs.current = new Set(
      [...processedMemoryChangeSeqs.current].filter((sequence) => retained.has(sequence)),
    );
    void drainMemoryChanges();
  }, [drainMemoryChanges, memoryVaultChanges]);

  useEffect(() => {
    void drainMemoryChanges();
  }, [drainMemoryChanges, selectedMeta?.path]);

  useEffect(() => {
    if (memoryVaultVersion === observedVaultVersion.current) return;
    observedVaultVersion.current = memoryVaultVersion;
    const queueChanged = memoryVaultChanges !== observedVaultChanges.current;
    observedVaultChanges.current = memoryVaultChanges;
    if (queueChanged) return;
    const current = selectedMetaRef.current;
    if (current) {
      detailCache.current.invalidatePath(current.path);
      setContentRefreshKey((key) => key + 1);
    }
    void load();
    if (railMode === "facts") setRecordsRefreshKey((key) => key + 1);
  }, [load, memoryVaultChanges, memoryVaultVersion, railMode]);

  const selectedHasWikilinks = activeDetail != null
    && selectedMeta != null
    && activeDetail.path === selectedMeta.path
    && activeDetail.content.includes("[[");
  const shouldLoadLinks = selectedHasWikilinks || inspectorOpen;
  useEffect(() => {
    const controller = new AbortController();
    const requestId = ++linksRequestId.current;
    setPageLinks(null);
    setLinkError(null);
    if (!selectedMeta || !shouldLoadLinks) {
      setLinksLoading(false);
      return;
    }
    setLinksLoading(true);
    getPageLinks(config, { path: selectedMeta.path, limit: 100, offset: 0 }, { signal: controller.signal }).then((links) => {
      if (!controller.signal.aborted && linksRequestId.current === requestId) setPageLinks(links);
    }).catch((reason) => {
      if (controller.signal.aborted || linksRequestId.current !== requestId) return;
      setLinkError(reason instanceof Error ? reason.message : String(reason));
    }).finally(() => {
      if (!controller.signal.aborted && linksRequestId.current === requestId) setLinksLoading(false);
    });
    return () => controller.abort();
  }, [config, contentRefreshKey, linksRefreshKey, selectedMeta?.path, shouldLoadLinks]);

  useEffect(() => {
    const controller = new AbortController();
    const requestId = ++historyRequestId.current;
    setPageHistory(null);
    setPageHistoryPath(null);
    setHistoryError(null);
    if (!selectedMeta || !inspectorOpen) {
      setHistoryLoading(false);
      return;
    }
    setHistoryLoading(true);
    getPageHistory(config, { path: selectedMeta.path, limit: 100 }, { signal: controller.signal }).then((history) => {
      if (controller.signal.aborted || historyRequestId.current !== requestId) return;
      setPageHistory(history);
      setPageHistoryPath(selectedMeta.path);
    }).catch((reason) => {
      if (controller.signal.aborted || historyRequestId.current !== requestId) return;
      setHistoryError(reason instanceof Error ? reason.message : String(reason));
    }).finally(() => {
      if (!controller.signal.aborted && historyRequestId.current === requestId) setHistoryLoading(false);
    });
    return () => controller.abort();
  }, [config, contentRefreshKey, inspectorOpen, selectedMeta?.path]);

  const currentPageLinks = pageLinks?.path === selectedMeta?.path ? pageLinks : null;
  const currentPageHistory = pageHistoryPath === selectedMeta?.path ? pageHistory : null;
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

  const loadPreviewDetail = useCallback(async (path: string, signal: AbortSignal) => {
    const response = await readMemoryArtifactDetail(config, path, { signal });
    return response.artifact;
  }, [config]);

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

  const closeEditor = () => {
    editRequestGeneration.current += 1;
    editPreviewController.current?.abort();
    setEditPending(false);
    setEditing(null);
  };

  const rebuild = () => {
    if (!mountedRef.current) return;
    const request = beginSummaryRequest();
    setRebuilding(true);
    setContentNotice(null);
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

  const visibleDetail = activeDetail?.path === selectedMeta?.path && activeDetail?.revision === selectedMeta?.revision
    ? activeDetail
    : null;
  if (visibleDetail) retainedInspectorDetail.current = visibleDetail;
  const inspectorDetail = visibleDetail
    ?? (selectedMeta ? retainedInspectorDetail.current : null);
  const inspectorDetailIsCurrent = inspectorDetail?.path === selectedMeta?.path;

  const beginEditing = useCallback(() => {
    const detail = activeDetail;
    if (!detail || detail.path !== selectedMetaRef.current?.path || !detail.editable || detail.editableContent == null) return;
    const baseContent = detail.editableContent;
    editRequestGeneration.current += 1;
    setEditing({
      path: detail.path,
      title: detail.title,
      baseRevision: detail.revision,
      baseContent,
      draftContent: getDraft(detail.path, detail.revision) ?? baseContent,
    });
    setEditReview(null);
    setEditDecisions({});
    setEditError(null);
  }, [activeDetail]);

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
      setSelected(null);
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
  }, [activeTab, navigateTo, selected, selectedMeta?.path, setInspectorVisibility, tabs]);

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

  // Esc closes the editor (draft behavior) — capture phase so it wins over
  // MemorySurface's window-level Esc-to-close while an editing session is live.
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || !editingRef.current || editReviewRef.current) return;
      event.preventDefault();
      event.stopImmediatePropagation();
      editRequestGeneration.current += 1;
      editPreviewController.current?.abort();
      setEditPending(false);
      setEditing(null);
    };
    window.addEventListener("keydown", onKeyDown, true);
    return () => window.removeEventListener("keydown", onKeyDown, true);
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

  // Properties edits recompose the page source (new frontmatter + unchanged
  // body) and run it through the standard review flow — no editor session.
  const saveFrontmatter = useCallback((next: MemoryFrontmatter) => {
    const detail = activeDetail;
    if (!detail || detail.path !== selectedMetaRef.current?.path || detail.editableContent == null || mutationPendingRef.current) return;
    const { body } = splitFrontmatter(detail.editableContent);
    const candidate = serializeFrontmatter(next) + body;
    if (candidate === detail.editableContent) return;
    editPreviewController.current?.abort();
    const controller = new AbortController();
    editPreviewController.current = controller;
    const requestGeneration = ++editRequestGeneration.current;
    const snapshot: EditSnapshot = {
      requestGeneration,
      path: detail.path,
      baseRevision: detail.revision,
      baseContent: detail.editableContent,
      candidateContent: candidate,
      draftKey: draftKey(detail.path, detail.revision),
    };
    setEditPending(true);
    setEditError(null);
    previewPageEdit(config, {
      path: snapshot.path,
      baseRevision: snapshot.baseRevision,
      content: snapshot.candidateContent,
      actor: "user:desktop",
    }, { signal: controller.signal })
      .then((preview) => {
        if (controller.signal.aborted || editRequestGeneration.current !== requestGeneration) return;
        setEditDecisions({});
        setEditReview({ kind: "preview", generation: ++reviewGeneration.current, snapshot, preview });
      })
      .catch((reason) => {
        if (controller.signal.aborted || !mountedRef.current || editRequestGeneration.current !== requestGeneration) return;
        const conflict = revisionConflict(reason);
        if (conflict) setEditReview({ kind: "conflict", generation: ++reviewGeneration.current, snapshot, conflict });
        else setEditError(reason instanceof Error ? reason.message : String(reason));
      })
      .finally(() => {
        if (!controller.signal.aborted && editRequestGeneration.current === requestGeneration) setEditPending(false);
      });
  }, [activeDetail, config]);

  const requestEditPreview = useCallback(async () => {
    if (!editing || editing.draftContent === editing.baseContent || editPending) return;
    editPreviewController.current?.abort();
    const controller = new AbortController();
    editPreviewController.current = controller;
    const requestGeneration = ++editRequestGeneration.current;
    const snapshot = snapshotEditing(editing, requestGeneration);
    setEditPending(true);
    setEditError(null);
    try {
      const preview = await previewPageEdit(config, {
        path: snapshot.path,
        baseRevision: snapshot.baseRevision,
        content: snapshot.candidateContent,
        actor: "user:desktop",
      }, { signal: controller.signal });
      if (controller.signal.aborted || (
        editRequestGeneration.current !== requestGeneration
        || !editingMatchesSnapshot(editingRef.current, snapshot)
      )) return;
      setEditDecisions({});
      setEditReview({ kind: "preview", generation: ++reviewGeneration.current, snapshot, preview });
    } catch (reason) {
      if (controller.signal.aborted || !mountedRef.current || (
        editRequestGeneration.current !== requestGeneration
        || !editingMatchesSnapshot(editingRef.current, snapshot)
      )) return;
      const conflict = revisionConflict(reason);
      if (conflict) setEditReview({ kind: "conflict", generation: ++reviewGeneration.current, snapshot, conflict });
      else setEditError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      if (!controller.signal.aborted && editRequestGeneration.current === requestGeneration) setEditPending(false);
    }
  }, [config, editPending, editing]);

  const decisionsForServer = useCallback((operations: readonly MemoryOperation[], questions: readonly { id: string; operationIndex: number }[]) => {
    const result: Record<string, DiffReviewDecision> = {};
    for (const question of questions) {
      const operation = operations[question.operationIndex];
      const decision = operation ? editDecisions[operation.id] : undefined;
      if (decision) result[question.id] = decision;
    }
    return result;
  }, [editDecisions]);

  /** Keep the blocking sheet mounted through its 180ms exit so the room,
   * scrim, and sheet release as one visual transaction. */
  const requestReviewExit = useCallback((afterExit: () => void) => {
    if (!editReviewRef.current || reviewExitAction.current) return;
    reviewExitAction.current = afterExit;
    setReviewClosing(true);
  }, []);

  const finishReviewExit = useCallback(() => {
    const afterExit = reviewExitAction.current;
    reviewExitAction.current = null;
    setReviewClosing(false);
    afterExit?.();
  }, []);

  const completeLocalSave = useCallback((revision: string, snapshot: EditSnapshot, activeReviewGeneration: number) => {
    const finish = () => {
      clearDraftIfMatches(snapshot.path, snapshot.baseRevision, snapshot.candidateContent);
      if (!mountedRef.current || editReviewRef.current?.generation !== activeReviewGeneration) return;
      detailCache.current.invalidatePath(snapshot.path);
      setArtifacts((current) => current.map((artifact) => artifact.path === snapshot.path ? { ...artifact, revision } : artifact));
      if (editingMatchesSnapshot(editingRef.current, snapshot)) setEditing(null);
      setEditReview((current) => current?.generation === activeReviewGeneration ? null : current);
      setEditDecisions({});
      setEditError(null);
      setContentNotice("Saved memory note.");
      restoreNoteFocus.current = true;
      setContentRefreshKey((key) => key + 1);
      void load();
    };
    // There is no sheet to animate after unmount; preserve the transactional
    // draft-clear guarantee without scheduling a callback that cannot fire.
    if (!mountedRef.current) {
      finish();
      return;
    }
    requestReviewExit(finish);
  }, [load, requestReviewExit]);

  const returnFromEditReview = useCallback(() => {
    if (reviewPending) return;
    requestReviewExit(() => {
      setEditReview(null);
      setEditDecisions({});
      setEditError(null);
    });
  }, [requestReviewExit, reviewPending]);

  const rebaseConflict = useCallback(() => {
    if (!editing || editReview?.kind !== "conflict") return;
    const { currentRevision, currentContent } = editReview.conflict;
    const { snapshot } = editReview;
    requestReviewExit(() => {
      setDraft(snapshot.path, currentRevision, snapshot.candidateContent);
      setEditing({
        ...editing,
        path: snapshot.path,
        baseRevision: currentRevision,
        baseContent: currentContent,
        draftContent: snapshot.candidateContent,
      });
      detailCache.current.invalidatePath(editing.path);
      setActiveDetail(null);
      setArtifacts((current) => current.map((artifact) => artifact.path === editing.path
        ? { ...artifact, revision: currentRevision }
        : artifact));
      setContentRefreshKey((key) => key + 1);
      setEditReview(null);
      setEditDecisions({});
      setEditError(null);
    });
  }, [editReview, editing, requestReviewExit]);

  const applyEditReview = useCallback(async () => {
    const review = editReviewRef.current;
    if (!review || review.kind === "conflict" || mutationPendingRef.current) return;
    const transactionGeneration = ++applyGeneration.current;
    const activeReviewGeneration = review.generation;
    const submittedDecisions = decisionsForServer(review.preview.operations, review.preview.questions);
    mutationPendingRef.current = true;
    setReviewPending(true);
    setEditError(null);
    try {
      const result = await applyPageEdit(config, {
        previewId: review.preview.id,
        decisions: submittedDecisions,
        savePending: review.preview.analysisPending,
      });
      if (applyGeneration.current !== transactionGeneration) return;
      completeLocalSave(result.revision, review.snapshot, activeReviewGeneration);
    } catch (reason) {
      if (
        !mountedRef.current
        ||
        applyGeneration.current !== transactionGeneration
        || editReviewRef.current?.generation !== activeReviewGeneration
      ) return;
      const conflict = revisionConflict(reason);
      if (conflict && review.kind === "preview") {
        setEditReview({
          kind: "conflict",
          generation: ++reviewGeneration.current,
          snapshot: review.snapshot,
          conflict,
        });
      }
      else setEditError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      if (applyGeneration.current === transactionGeneration) {
        mutationPendingRef.current = false;
        if (mountedRef.current) setReviewPending(false);
      }
    }
  }, [completeLocalSave, config, decisionsForServer]);

  useEffect(() => {
    if (mutationPendingRef.current) return;
    const path = selectedMeta?.path ?? null;
    if (editing && path !== editing.path) {
      setEditing(null);
      setEditReview(null);
      setEditDecisions({});
      setEditError(null);
    }
  }, [editing, selectedMeta?.path]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (mutationPendingRef.current) return;
      if ((!event.metaKey && !event.ctrlKey) || event.altKey || event.shiftKey) return;
      const key = event.key.toLowerCase();
      if (key !== "e" && key !== "s") return;
      const target = event.target instanceof Element ? event.target : null;
      const focused = document.activeElement;
      const editorTextarea = [target, focused].some((element) =>
        element instanceof HTMLTextAreaElement && element.closest("[data-memory-editor]") != null,
      );
      const editButton = [target, focused].some((element) =>
        element instanceof HTMLElement && element.closest('button[aria-label="Edit memory note"]') != null,
      );
      if (!editorTextarea && !editButton && [target, focused].some(isInteractiveShortcutTarget)) return;
      if (key === "e") {
        if (!editing && (!visibleDetail?.editable || visibleDetail.editableContent == null)) return;
        event.preventDefault();
        if (editing) closeEditor();
        else beginEditing();
        return;
      }
      if (!editing || editReview) return;
      event.preventDefault();
      void requestEditPreview();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [beginEditing, editReview, editing, requestEditPreview, reviewPending, visibleDetail]);

  useEffect(() => {
    if (!restoreNoteFocus.current || editing || editReview || !visibleDetail) return;
    // Keep the flag set until focus actually lands: the mode crossfade tears
    // the previous panel down ~200ms in, blurring to body, and visibleDetail
    // refreshes re-run this effect (cancelling naive timers) in between.
    const tryFocus = () => {
      // During the note crossfade two articles share the path (popLayout keeps
      // the exiting one mounted, first in DOM) — focus the entering one.
      const article = Array.from(layoutRef.current?.querySelectorAll<HTMLElement>("[data-memory-note-path]") ?? [])
        .filter((candidate) => candidate.dataset.memoryNotePath === visibleDetail.path)
        .at(-1);
      if (!article) return;
      if (document.activeElement !== document.body && document.activeElement !== article && restoreNoteFocus.current === false) return;
      article.focus({ preventScroll: true });
      if (document.activeElement === article) restoreNoteFocus.current = false;
    };
    queueMicrotask(tryFocus);
    const retry = window.setTimeout(tryFocus, 260);
    return () => window.clearTimeout(retry);
  }, [editReview, editing, visibleDetail]);

  const reviewPresentation = useMemo(() => {
    if (!editReview) return null;
    return {
      path: editReview.snapshot.path,
      baseContent: editReview.snapshot.baseContent,
      draftContent: editReview.snapshot.candidateContent,
      operations: editReview.kind === "preview" ? editReview.preview.operations : [],
      analysisPending: editReview.kind === "preview" && editReview.preview.analysisPending,
    };
  }, [editReview]);

  return (
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
        <PaneResizeHandle layoutRef={layoutRef} cssVar="--mw-rail-w" storageKey={RAIL_WIDTH_KEY} min={220} max={400} defaultWidth={288} edge="end" label="Resize memory rail" />
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
        {tabs.length > 0 && (
          <MemoryDocumentTabs
            paths={tabs}
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
              disabled={!editing && (!visibleDetail?.editable || visibleDetail.editableContent == null)}
              onClick={() => {
                if (editing) closeEditor();
                else beginEditing();
              }}
            >
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <use href="#dp-edit" />
              </svg>
            </button>
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
              iconA={<svg viewBox="0 0 24 24" aria-hidden="true"><use href="#dp-arrow-right" /></svg>}
              iconB={<svg viewBox="0 0 24 24" aria-hidden="true"><use href="#dp-more" /></svg>}
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
                baseContent={editing.baseContent}
                value={editing.draftContent}
                saving={editPending}
                error={editError}
                onClose={closeEditor}
                onChange={(draftContent) => {
                  editRequestGeneration.current += 1;
                  editPreviewController.current?.abort();
                  setEditPending(false);
                  if (draftContent === editing.baseContent) clearDraft(editing.path, editing.baseRevision);
                  else setDraft(editing.path, editing.baseRevision, draftContent);
                  setEditing((current) => current ? { ...current, draftContent } : current);
                }}
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
                    if (selectedMeta) {
                      detailCache.current.invalidatePath(selectedMeta.path);
                    }
                    setContentRefreshKey((key) => key + 1);
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
            cache={detailCache.current}
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
              navigationDisabled={reviewPending || !inspectorDetailIsCurrent}
              titleForPath={(path) => stem(path)}
              onNavigate={navigateTo}
              onRetryLinks={() => setLinksRefreshKey((key) => key + 1)}
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
          onOpenChange={(open) => setDiffClosing(!open)}
          onExitComplete={() => {
            setDiffEvent(null);
            setDiffClosing(false);
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
          operations={reviewPresentation.operations.map(diffOperation)}
          decisions={editDecisions}
          analysisPending={reviewPresentation.analysisPending}
          conflict={editReview.kind === "conflict" ? editReview.conflict : undefined}
          pending={reviewPending}
          error={editError}
          closing={reviewClosing}
          onExitComplete={finishReviewExit}
          onDecision={(operationId, decision) => setEditDecisions((current) => ({ ...current, [operationId]: decision }))}
          onApply={() => void applyEditReview()}
          onCancel={returnFromEditReview}
          onRebase={rebaseConflict}
        />
      )}
    </div>
  );
}
