import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ChevronLeft, ChevronRight, Database, PanelLeftClose, PanelLeftOpen, PanelRightClose, PanelRightOpen, Pencil, Plus, X } from "@/components/icons";
import clsx from "clsx";
import { motion, useReducedMotion } from "motion/react";
import { EASE_EMPHASIZED, MOTION, RISE_IN, RISE_SETTLED } from "@/lib/tokens/motion";
import { useStore } from "@/stores";
import { ApiError, type AppConfig } from "@/api/core";
import type { WikiLinkHandlers } from "@/lib/wikilink";
import {
  createMemoryNode,
  getPageHistory,
  getPageLinks,
  applyPageEdit,
  listMemoryArtifactSummaries,
  previewPageEdit,
  readMemoryArtifactDetail,
  rebuildMemoryArtifactSummaries,
} from "@/api/memoryArtifacts";
import type { DiffReviewDecision, DiffReviewOperation } from "@/components/ui/diffReviewTypes";
import { listMemoryItems, setRecordPinned, type MemoryItem, type MemoryKind } from "@/api/memoryItems";
import { IconButton } from "@/components/ui/IconButton";
import { IconSwap } from "@/components/ui/IconSwap";
import { ICON } from "@/lib/icons";
import { Select } from "@/components/ui/Select";
import { TreeSearch } from "@/features/memory/components/MemoryFileTree";
import { NotebookRail } from "@/features/memory/components/NotebookRail";
import { PaneResizeHandle } from "@/features/memory/components/PaneResizeHandle";
import { MemoryNote } from "@/features/memory/components/MemoryNote";
import { RecordDetailPane } from "@/features/memory/components/RecordDetailPane";
import { RecordListPane } from "@/features/memory/components/RecordListPane";
import { MemoryInspector } from "@/features/memory/components/MemoryInspector";
import { MemoryEditor } from "@/features/memory/components/MemoryEditor";
import { MemoryEditReview, type MemoryConflict } from "@/features/memory/components/MemoryEditReview";
import { MemoryQuickSwitcher } from "@/features/memory/components/MemoryQuickSwitcher";
import { WikiLinkPreview } from "@/features/memory/components/WikiLinkPreview";
import { ArtifactCache } from "@/features/memory/lib/artifactCache";
import { NavigationHistory, type NavigationLocation } from "@/features/memory/lib/navigationHistory";
import { isMissingArtifactError, resolveWikiTarget } from "@/features/memory/lib/wikiResolution";
import { clearDraft, clearDraftIfMatches, draftKey, getDraft, setDraft } from "@/features/memory/lib/draftStore";
import { isNotebookResourcePath } from "@/features/memory/lib/notebookIndex";
import { serializeFrontmatter, splitFrontmatter } from "@/features/memory/lib/format";
import { buildWorkspaceTree, stem } from "@/features/memory/lib/workspaceTree";
import type { MemoryFrontmatter } from "@/features/memory/components/MemoryProperties";
import type { MemoryArtifactDetail, MemoryArtifactSummary, MemoryOperation, PageEditHistory, PageEditPreview, PageLinks } from "@/features/memory/lib/notebookTypes";

const RECORD_PAGE_SIZE = 100;
const RAIL_WIDTH_KEY = "arden.desktop.memory.railWidth";
const CTX_WIDTH_KEY = "arden.desktop.memory.ctxWidth";
const LAST_PATH_KEY = "arden.desktop.memory.lastPath";

// Inspector open/closed is conceptually session state, not a Prefs field —
// persisted separately to localStorage so the panel doesn't silently reset
// on reload. Mirrors the SKIP_APPROVALS_KEY pattern in stores/prefs.ts.
const INSPECTOR_OPEN_KEY = "arden.desktop.memory.inspectorOpen";

function loadInspectorOpen(): boolean {
  try {
    return localStorage.getItem(INSPECTOR_OPEN_KEY) !== "false";
  } catch {
    return true;
  }
}

function persistInspectorOpen(value: boolean): void {
  try {
    localStorage.setItem(INSPECTOR_OPEN_KEY, value ? "true" : "false");
  } catch {
    /* localStorage unavailable — non-fatal */
  }
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
    : element.dataset.memoryInlinePath != null ? "inline"
      : null;
  const target = kind === "wikilink" ? element.dataset.wikilink : element.dataset.memoryInlinePath;
  if (!kind || target == null) return null;
  const attribute = kind === "wikilink" ? "data-wikilink" : "data-memory-inline-path";
  const matches = Array.from(scroller.querySelectorAll<HTMLElement>(`[${attribute}]`))
    .filter((candidate) => (kind === "wikilink" ? candidate.dataset.wikilink : candidate.dataset.memoryInlinePath) === target);
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
  const attribute = token.kind === "wikilink" ? "data-wikilink" : "data-memory-inline-path";
  const matches = Array.from(scroller.querySelectorAll<HTMLElement>(`[${attribute}]`))
    .filter((candidate) => (token.kind === "wikilink" ? candidate.dataset.wikilink : candidate.dataset.memoryInlinePath) === token.target);
  matches[token.occurrence]?.focus({ preventScroll: true });
}

export function ArtifactMemoryView({ config }: { config: AppConfig }) {
  const reduce = useReducedMotion();
  const closeMemorySurface = useStore((s) => s.closeMemory);
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
  const [recordsOpen, setRecordsOpen] = useState(false);
  const [records, setRecords] = useState<MemoryItem[]>([]);
  const [recordsQuery, setRecordsQuery] = useState("");
  const [recordsError, setRecordsError] = useState<string | null>(null);
  const [recordsLoading, setRecordsLoading] = useState(false);
  const [recordKind, setRecordKind] = useState<MemoryKind | "">("");
  const [selectedRecordId, setSelectedRecordId] = useState<string | null>(null);
  const [pinningId, setPinningId] = useState<string | null>(null);
  const [recordsRefreshKey, setRecordsRefreshKey] = useState(0);
  const [inspectorOpen, setInspectorOpen] = useState(() => loadInspectorOpen());
  const [pageLinks, setPageLinks] = useState<PageLinks | null>(null);
  const [pageHistory, setPageHistory] = useState<PageEditHistory | null>(null);
  const [linksLoading, setLinksLoading] = useState(false);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [linkError, setLinkError] = useState<string | null>(null);
  const [linksRefreshKey, setLinksRefreshKey] = useState(0);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [historyVersion, setHistoryVersion] = useState(0);
  const [pageHistoryPath, setPageHistoryPath] = useState<string | null>(null);
  const [editing, setEditing] = useState<EditingSession | null>(null);
  const [editReview, setEditReview] = useState<ReviewState | null>(null);
  const [editDecisions, setEditDecisions] = useState<Record<string, DiffReviewDecision>>({});
  const [editPending, setEditPending] = useState(false);
  const [reviewPending, setReviewPending] = useState(false);
  const [editError, setEditError] = useState<string | null>(null);
  const [switcherOpen, setSwitcherOpen] = useState(false);
  const [railHidden, setRailHidden] = useState(false);
  const [nbMode, setNbMode] = useState(false);
  const [tabs, setTabs] = useState<string[]>([]);
  const [activeTab, setActiveTab] = useState(0);

  const summaryRequest = useRef<SummaryRequest | null>(null);
  const recordsRequestId = useRef(0);
  const detailCache = useRef(new ArtifactCache());
  const navigationHistory = useRef(new NavigationHistory());
  const pendingRestore = useRef<NavigationLocation | null>(null);
  const linksRequestId = useRef(0);
  const historyRequestId = useRef(0);
  const artifactsRef = useRef<MemoryArtifactSummary[]>([]);
  const selectedMetaRef = useRef<MemoryArtifactSummary | null>(null);
  const recordsTriggerRef = useRef<HTMLButtonElement>(null);
  const recordsHeadingRef = useRef<HTMLHeadingElement>(null);
  const layoutRef = useRef<HTMLDivElement>(null);
  const editingRef = useRef<EditingSession | null>(null);
  const editReviewRef = useRef<ReviewState | null>(null);
  const editRequestGeneration = useRef(0);
  const editPreviewController = useRef<AbortController | null>(null);
  const reviewGeneration = useRef(0);
  const applyGeneration = useRef(0);
  const mutationPendingRef = useRef(false);
  const memoryVaultChangesRef = useRef(useStore.getState().memoryVaultChanges);
  const processedMemoryChangeSeqs = useRef(new Set<number>());
  const memoryChangeDrainRunning = useRef(false);
  const memoryChangeDrainRequested = useRef(false);
  const memoryChangeDrainRef = useRef<(() => Promise<void>) | null>(null);
  const restoreNoteFocus = useRef(false);
  const mountedRef = useRef(true);
  const disposeControllerRef = useRef(new AbortController());

  artifactsRef.current = artifacts;
  editingRef.current = editing;
  editReviewRef.current = editReview;

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
  const createdAtOf = useCallback((artifact: MemoryArtifactSummary) => artifact.createdAt ?? artifact.updatedAt ?? "", []);
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
      if (Number.isFinite(rail)) layout.style.setProperty("--mw-rail-w", `${Math.max(220, Math.min(720, rail))}px`);
      const ctx = parseInt(localStorage.getItem(CTX_WIDTH_KEY) ?? "", 10);
      if (Number.isFinite(ctx)) layout.style.setProperty("--mw-ctx-w", `${Math.max(240, Math.min(480, ctx))}px`);
    } catch { /* non-fatal */ }
  }, []);

  useEffect(() => {
    if (loading) return;
    if (mutationPendingRef.current) return;
    if (selected && navigableArtifacts.some((artifact) => artifact.path === selected)) return;
    let stored: string | null = null;
    try {
      stored = localStorage.getItem(LAST_PATH_KEY);
    } catch { /* non-fatal */ }
    const restored = stored != null && navigableArtifacts.some((artifact) => artifact.path === stored) ? stored : null;
    const fallback = restored
      ?? (navigableArtifacts.some((artifact) => artifact.path === "index.md") ? "index.md" : null)
      ?? navigableArtifacts[0]?.path
      ?? null;
    if (fallback) {
      navigationHistory.current.push({ path: fallback, anchor: null, scrollTop: 0, focusSelector: null });
      setHistoryVersion((version) => version + 1);
    }
    setSelected(fallback);
  }, [loading, navigableArtifacts, reviewPending, selected]);

  const closeRecords = useCallback(() => {
    setRecordsOpen(false);
    queueMicrotask(() => recordsTriggerRef.current?.focus());
  }, []);

  const focusRailNote = useCallback((path: string) => {
    queueMicrotask(() => {
      const note = Array.from(layoutRef.current?.querySelectorAll<HTMLElement>("[data-memory-entry]") ?? [])
        .find((entry) => entry.dataset.memoryEntry === path && entry.matches("button"));
      note?.focus();
    });
  }, []);

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

  const navigateTo = useCallback((path: string, anchor: string | null = null) => {
    if (mutationPendingRef.current) return;
    const destination = navigationHistory.current.current;
    if (destination?.path === path && destination.anchor === anchor) {
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
    setSelected(path);
    if (recordsOpen) {
      setRecordsOpen(false);
      focusRailNote(path);
    }
  }, [currentLocation, focusRailNote, recordsOpen]);

  const selectFile = useCallback((path: string) => navigateTo(path), [navigateTo]);

  const moveHistory = useCallback((movement: "back" | "forward") => {
    if (mutationPendingRef.current) return;
    const current = currentLocation();
    if (current) navigationHistory.current.replaceCurrent(current);
    const location = movement === "back" ? navigationHistory.current.back() : navigationHistory.current.forward();
    if (!location) return;
    pendingRestore.current = location;
    setHistoryVersion((version) => version + 1);
    setContentNotice(null);
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
    if (!recordsOpen) return;
    recordsHeadingRef.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      closeRecords();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [closeRecords, recordsOpen]);

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
          if (mountedRef.current && recordsOpen) setRecordsRefreshKey((key) => key + 1);
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
  }, [load, recordsOpen]);
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
    if (recordsOpen) setRecordsRefreshKey((key) => key + 1);
  }, [load, memoryVaultChanges, memoryVaultVersion, recordsOpen]);

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
    onNavigateInline: (target) => {
      if (!artifactPaths.has(target)) return;
      navigateTo(target, null);
    },
  }), [artifactPaths, currentPageLinks, navigateTo]);

  const loadPreviewDetail = useCallback(async (path: string, signal: AbortSignal) => {
    const response = await readMemoryArtifactDetail(config, path, { signal });
    return response.artifact;
  }, [config]);

  useEffect(() => {
    if (!recordsOpen) return;
    const requestId = ++recordsRequestId.current;
    setRecordsLoading(true);
    setRecordsError(null);
    listMemoryItems(config, {
      limit: RECORD_PAGE_SIZE,
      offset: 0,
      q: recordsQuery.trim() || undefined,
      kind: recordKind || undefined,
      status: "active",
    })
      .then((response) => {
        if (recordsRequestId.current !== requestId) return;
        setRecords(response.items);
        setSelectedRecordId((previous) => previous && response.items.some((item) => item.id === previous)
          ? previous
          : response.items[0]?.id ?? null);
      })
      .catch((reason) => {
        if (recordsRequestId.current === requestId) setRecordsError(reason instanceof Error ? reason.message : String(reason));
      })
      .finally(() => {
        if (recordsRequestId.current === requestId) setRecordsLoading(false);
      });
  }, [config, recordKind, recordsOpen, recordsQuery, recordsRefreshKey]);

  const selectedRecord = records.find((record) => record.id === selectedRecordId) ?? records[0] ?? null;
  const togglePinned = (record: MemoryItem) => {
    const next = !record.pinned;
    const requestId = recordsRequestId.current;
    setPinningId(record.id);
    setRecords((current) => current.map((item) => item.id === record.id ? { ...item, pinned: next } : item));
    setRecordPinned(config, record.id, next)
      .catch((reason) => {
        // A refetch may have replaced the list mid-flight; rolling back a
        // stale boolean onto fresh records would corrupt their pin state.
        if (recordsRequestId.current === requestId) {
          setRecords((current) => current.map((item) => item.id === record.id ? { ...item, pinned: record.pinned } : item));
        }
        // Toast, not recordsError: the list itself loaded fine, and the
        // error slot replaces the whole list with a retry panel.
        useStore.getState().pushToast({
          id: `memory-pin-fail:${record.id}`,
          title: reason instanceof Error ? reason.message : String(reason),
          status: "failed",
          target: { kind: "automation" },
        });
      })
      .finally(() => setPinningId((current) => current === record.id ? null : current));
  };

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

  // Tabs — the draft's document tabs. The active tab mirrors the current
  // selection; navigation through any channel lands in the active tab.
  useEffect(() => {
    const path = selectedMeta?.path ?? selected;
    if (!path) return;
    setTabs((current) => {
      if (current.length === 0) return [path];
      if (current[activeTab] === path) return current;
      const next = [...current];
      next[activeTab] = path;
      return next;
    });
  }, [activeTab, selected, selectedMeta?.path]);

  const switchTab = useCallback((index: number) => {
    if (index === activeTab || mutationPendingRef.current) return;
    const target = tabs[index];
    if (!target) return;
    setActiveTab(index);
    navigateTo(target);
  }, [activeTab, navigateTo, tabs]);

  const closeTab = useCallback((index: number) => {
    if (tabs.length === 1 || mutationPendingRef.current) return;
    const next = tabs.filter((_, i) => i !== index);
    let nextActive = activeTab;
    if (nextActive >= next.length) nextActive = next.length - 1;
    else if (index < nextActive) nextActive -= 1;
    setTabs(next);
    setActiveTab(nextActive);
    if (next[nextActive] !== (selectedMeta?.path ?? selected)) navigateTo(next[nextActive]!);
  }, [activeTab, navigateTo, selected, selectedMeta?.path, tabs]);

  const addTab = useCallback(() => {
    if (mutationPendingRef.current) return;
    setTabs((current) => [...current, "index.md"]);
    setActiveTab(tabs.length);
    navigateTo("index.md");
  }, [navigateTo, tabs.length]);

  const openInNewTab = useCallback((path: string) => {
    if (mutationPendingRef.current) return;
    setTabs((current) => [...current, path]);
    setActiveTab(tabs.length);
    navigateTo(path);
  }, [navigateTo, tabs.length]);

  const toggleNbMode = useCallback(() => {
    setNbMode((mode) => {
      const next = !mode;
      const layout = layoutRef.current;
      if (layout) {
        const current = parseInt(getComputedStyle(layout).getPropertyValue("--mw-rail-w"), 10);
        if (next) layout.style.setProperty("--mw-rail-w", `${Math.max(Number.isFinite(current) ? current : 288, 516)}px`);
        else layout.style.setProperty("--mw-rail-w", "288px");
      }
      setRailHidden(false);
      return next;
    });
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
      if ((!event.metaKey && !event.ctrlKey) || event.key !== "\\") return;
      event.preventDefault();
      setRailHidden((hidden) => !hidden);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  const pendingCreateEdit = useRef<string | null>(null);
  useEffect(() => {
    if (!pendingCreateEdit.current || activeDetail?.path !== pendingCreateEdit.current) return;
    pendingCreateEdit.current = null;
    if (activeDetail.editable && activeDetail.editableContent != null) beginEditing();
  }, [activeDetail, beginEditing]);

  const createNode = useCallback((kind: "note" | "dir", path: string) => {
    if (mutationPendingRef.current) return;
    void createMemoryNode(config, { path, kind: kind === "dir" ? "folder" : "note" })
      .then((result) => {
        if (!mountedRef.current) return;
        if (kind === "note") {
          if (result.artifact) {
            setArtifacts((current) => current.some((artifact) => artifact.path === result.path)
              ? current
              : [...current, result.artifact!]);
          }
          pendingCreateEdit.current = result.path;
          navigateTo(result.path);
        } else {
          setDirectories((current) => current.includes(result.path) ? current : [...current, result.path]);
        }
        void load();
      })
      .catch((reason) => {
        if (mountedRef.current) setEditError(reason instanceof Error ? reason.message : String(reason));
      });
  }, [config, load, navigateTo]);

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

  const completeLocalSave = useCallback((revision: string, snapshot: EditSnapshot, activeReviewGeneration: number) => {
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
  }, [load]);

  const returnFromEditReview = useCallback(() => {
    if (reviewPending) return;
    setEditReview(null);
    setEditDecisions({});
    setEditError(null);
  }, [reviewPending]);

  const rebaseConflict = useCallback(() => {
    if (!editing || editReview?.kind !== "conflict") return;
    const { currentRevision, currentContent } = editReview.conflict;
    const { snapshot } = editReview;
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
  }, [editReview, editing]);

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
      className={clsx("memory-ws", railHidden && "rail-hidden", !inspectorOpen && "ctx-hidden", editing && !editReview && "note-editing")}
    >
      {/* Fixed-viewport panel toggles — the main screen's .sidebar-toggle /
          .right-sidebar-toggle chrome: pinned by the traffic lights while
          the panels slide beneath them. */}
      <IconButton
        size="xs"
        className="sidebar-toggle"
        title={railHidden ? "Show sidebar (⌘\\)" : "Hide sidebar (⌘\\)"}
        aria-label={railHidden ? "Show sidebar" : "Hide sidebar"}
        onClick={() => setRailHidden((hidden) => !hidden)}
      >
        <IconSwap
          state={railHidden ? "b" : "a"}
          iconA={<PanelLeftClose size={16} strokeWidth={2} />}
          iconB={<PanelLeftOpen size={16} strokeWidth={2} />}
        />
      </IconButton>
      <IconButton
        size="xs"
        className="right-sidebar-toggle"
        title={inspectorOpen ? "Hide links and provenance" : "Show links and provenance"}
        aria-label={inspectorOpen ? "Close links and provenance" : "Open links and provenance"}
        aria-pressed={inspectorOpen}
        onClick={() => setInspectorOpen((open) => {
          const next = !open;
          persistInspectorOpen(next);
          return next;
        })}
      >
        <IconSwap
          state={inspectorOpen ? "b" : "a"}
          iconA={<PanelRightOpen size={16} strokeWidth={2} />}
          iconB={<PanelRightClose size={16} strokeWidth={2} />}
        />
      </IconButton>
      <nav data-memory-zone="rail" aria-label="Memory notebook" className="mw-rail">
        <div className="mw-rail-drag" aria-hidden />
        <PaneResizeHandle layoutRef={layoutRef} cssVar="--mw-rail-w" storageKey={RAIL_WIDTH_KEY} min={220} max={nbMode ? 720 : 400} defaultWidth={nbMode ? 516 : 288} edge="end" label="Resize memory rail" />
        {recordsOpen ? (
          <section aria-label="Raw records diagnostic" className="flex h-full min-h-0 flex-col">
            <TreeSearch quiet value={recordsQuery} onChange={setRecordsQuery} placeholder="Search raw records…" />
            {/* One section-head row, same voice as the notes tree: title,
                kind filter, close — no second panel header. */}
            <div className="flex items-center gap-1 pb-1 pl-4 pr-2 pt-2">
              <h2 ref={recordsHeadingRef} tabIndex={-1} className="text-sm font-semibold text-ink outline-none select-none">Records</h2>
              <Select
                value={recordKind}
                onChange={(value) => setRecordKind(value as MemoryKind | "")}
                options={[
                  { value: "", label: "All kinds" },
                  { value: "fact", label: "Facts" },
                  { value: "directive", label: "Rules" },
                  { value: "source", label: "Sources" },
                ]}
                aria-label="Filter raw records by kind"
                className="!h-6 !border-0 ml-auto !px-1.5 !text-xs hover:bg-surface-soft"
              />
              <IconButton size="sm" tone="faint" onClick={closeRecords} aria-label="Close raw records diagnostic">
                <X size={ICON.XS} strokeWidth={2} />
              </IconButton>
            </div>
            <RecordListPane
              query={recordsQuery}
              onQueryChange={setRecordsQuery}
              records={records}
              recordsLoading={recordsLoading}
              recordsError={recordsError}
              selectedRecordId={selectedRecordId}
              pinningId={pinningId}
              reduce={!!reduce}
              onSelectRecord={setSelectedRecordId}
              onTogglePinned={togglePinned}
              onRetry={() => setRecordsRefreshKey((key) => key + 1)}
            />
          </section>
        ) : (
        <NotebookRail
          tree={workspaceTree}
          allNotes={navigableArtifacts}
          selectedPath={selectedMeta?.path ?? null}
          loading={loading}
          error={error}
          rebuilding={rebuilding}
          navigationDisabled={reviewPending}
          nbMode={nbMode}
          createdAt={createdAtOf}
          onToggleNbMode={toggleNbMode}
          onSelect={selectFile}
          onOpenInNewTab={openInNewTab}
          onOpenSwitcher={() => setSwitcherOpen(true)}
          onCreate={createNode}
          onRetry={() => void load()}
          onRebuild={rebuild}
        />
        )}
      </nav>

      <div data-memory-zone="workspace" className="mw-doc">
        <div className="mw-tab-strip">
          <button type="button" className="mw-icon-btn" title="Back (⌘[)" aria-label="Back in memory history" disabled={reviewPending || !navigationHistory.current.canBack} onClick={() => moveHistory("back")}>
            <ChevronLeft size={15} aria-hidden />
          </button>
          <button type="button" className="mw-icon-btn" title="Forward (⌘])" aria-label="Forward in memory history" disabled={reviewPending || !navigationHistory.current.canForward} onClick={() => moveHistory("forward")}>
            <ChevronRight size={15} aria-hidden />
          </button>
          <div className="mw-doc-tabs" role="tablist" aria-label="Open notes">
            {tabs.map((path, index) => (
              <div
                key={`${index}:${path}`}
                role="tab"
                aria-selected={index === activeTab}
                tabIndex={0}
                className={clsx("mw-doc-tab", index === activeTab && "active")}
                onClick={() => switchTab(index)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") switchTab(index);
                }}
              >
                <span className="mw-doc-tab-label">{stem(path)}</span>
                <button
                  type="button"
                  className="mw-doc-tab-x"
                  aria-label="Close tab"
                  onClick={(event) => {
                    event.stopPropagation();
                    closeTab(index);
                  }}
                >
                  ×
                </button>
              </div>
            ))}
            <button type="button" className="mw-icon-btn" style={{ width: 28, height: 28, marginLeft: 3 }} title="New tab" aria-label="New tab" onClick={addTab}>
              <Plus size={14} aria-hidden />
            </button>
          </div>
          <span className="mw-edit-hint" aria-hidden>⌘S review · Esc close</span>
          <button
            type="button"
            className={clsx("mw-icon-btn", editing && !editReview && "on")}
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
            <Pencil size={15} aria-hidden />
          </button>
          <button
            ref={recordsTriggerRef}
            type="button"
            className={clsx("mw-icon-btn", recordsOpen && "on")}
            disabled={reviewPending}
            aria-label="Open raw records diagnostic"
            aria-pressed={recordsOpen}
            title="Raw records"
            onClick={() => {
              if (mutationPendingRef.current) return;
              if (recordsOpen) closeRecords();
              else setRecordsOpen(true);
            }}
          >
            <Database size={15} aria-hidden />
          </button>
          <button type="button" className="mw-icon-btn" aria-label="Close memory" title="Close (Esc)" onClick={closeMemorySurface}>
            <X size={15} aria-hidden />
          </button>
        </div>
        <main aria-label={recordsOpen ? "Raw record" : editReview ? "Memory edit review" : editing ? "Memory editor" : "Memory note"} className="relative min-h-0 flex-1 overflow-hidden">

        {/* Mode swap (note <-> editor <-> review <-> records detail): enter-only
            rise-in on a keyed remount. Exit-based crossfades left exiting
            panels alive holding focus (non-deterministic under happy-dom and
            a real focus hazard) — the entrance alone carries the change. */}
          <motion.div
            key={recordsOpen ? "records" : editReview ? "review" : editing ? "editor" : "note"}
            className="absolute inset-0 min-h-0"
            initial={reduce ? false : RISE_IN}
            animate={{ ...RISE_SETTLED, transitionEnd: { filter: "none" } }}
            transition={{ duration: MOTION.panel, ease: EASE_EMPHASIZED }}
          >
            {recordsOpen ? (
          <RecordDetailPane
            record={selectedRecord}
            direction={1}
            pinningId={pinningId}
            onTogglePinned={togglePinned}
          />
        ) : editReview && reviewPresentation ? (
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
            onDecision={(operationId, decision) => setEditDecisions((current) => ({ ...current, [operationId]: decision }))}
            onApply={() => void applyEditReview()}
            onCancel={returnFromEditReview}
            onRebase={rebaseConflict}
          />
        ) : editing ? (
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
        ) : (
          <motion.div
            key={selectedMeta?.path ?? "empty"}
            className="h-full min-h-0"
            initial={reduce ? false : { opacity: 0, filter: "blur(3px)" }}
            animate={{ opacity: 1, filter: "blur(0px)", transitionEnd: { filter: "none" } }}
            transition={{ duration: 0.19, ease: EASE_EMPHASIZED }}
          >
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
          </motion.div>
        )}
          </motion.div>
        {!recordsOpen && !editing && !editReview && (
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

      <aside data-memory-zone="inspector" aria-label="Links and provenance" className="mw-context">
        <PaneResizeHandle layoutRef={layoutRef} cssVar="--mw-ctx-w" storageKey={CTX_WIDTH_KEY} min={240} max={480} defaultWidth={296} edge="start" label="Resize context panel" />
        {inspectorOpen && visibleDetail ? (
          <MemoryInspector
            page={visibleDetail}
            links={currentPageLinks}
            history={currentPageHistory}
            linksLoading={linksLoading}
            historyLoading={historyLoading}
            linkError={linkError}
            historyError={historyError}
            navigationDisabled={reviewPending}
            titleForPath={(path) => stem(path)}
            onNavigate={navigateTo}
            onRetryLinks={() => setLinksRefreshKey((key) => key + 1)}
            scrollTargetRef={layoutRef}
          />
        ) : inspectorOpen ? (
          <p className="mw-ctx-empty" style={{ paddingTop: 16 }}>Loading…</p>
        ) : null}
      </aside>

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
  );
}
