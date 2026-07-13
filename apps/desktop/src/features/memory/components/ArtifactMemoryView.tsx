import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ChevronLeft, ChevronRight, PanelRight, Pencil, X } from "lucide-react";
import { useReducedMotion } from "motion/react";
import clsx from "clsx";
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
  retryPageEdit,
} from "@/api/memoryArtifacts";
import type { DiffReviewDecision, DiffReviewOperation } from "@/components/ui/diffReviewTypes";
import { listMemoryItems, setRecordPinned, type MemoryItem, type MemoryKind } from "@/api/memoryItems";
import { Select } from "@/components/ui/Select";
import { TreeSearch } from "@/features/memory/components/MemoryFileTree";
import { NotebookRail } from "@/features/memory/components/NotebookRail";
import { FileDetailPane } from "@/features/memory/components/FileDetailPane";
import { RecordDetailPane } from "@/features/memory/components/RecordDetailPane";
import { RecordListPane } from "@/features/memory/components/RecordListPane";
import { MemoryInspector } from "@/features/memory/components/MemoryInspector";
import { MemoryEditor } from "@/features/memory/components/MemoryEditor";
import { MemoryEditReview, type MemoryConflict } from "@/features/memory/components/MemoryEditReview";
import { WikiLinkPreview } from "@/features/memory/components/WikiLinkPreview";
import { ArtifactCache, RevisionCache } from "@/features/memory/lib/artifactCache";
import { NavigationHistory, type NavigationLocation } from "@/features/memory/lib/navigationHistory";
import { isMissingArtifactError, resolveWikiTarget } from "@/features/memory/lib/wikiResolution";
import { clearDraft, clearDraftIfMatches, draftKey, getDraft, setDraft } from "@/features/memory/lib/draftStore";
import {
  buildNotebookRailModel,
  firstNotebookPath,
  isNotebookPage,
  isNotebookResourcePath,
  selectIndexDocuments,
} from "@/features/memory/lib/notebookIndex";
import type { MemoryArtifactDetail, MemoryArtifactSummary, MemoryLink, MemoryOperation, PageEditEvent, PageEditHistory, PageEditPreview, PageLinks } from "@/features/memory/lib/notebookTypes";

const RECORD_PAGE_SIZE = 100;
const SEARCH_DEBOUNCE_MS = 180;

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
  | { kind: "conflict"; generation: number; snapshot: EditSnapshot; conflict: MemoryConflict }
  | { kind: "external"; generation: number; event: PageEditEvent };

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

function enqueueExternalReview(current: PageEditEvent[], event: PageEditEvent): PageEditEvent[] {
  if (current.some((candidate) => candidate.id === event.id)) return current;
  return [...current, event].sort((left, right) => left.sequence - right.sequence);
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

function mergeLinks(current: MemoryLink[], incoming: MemoryLink[]) {
  const seen = new Set(current.map((link) => `${link.sourcePath}\u0000${link.line}\u0000${link.column}\u0000${link.target}`));
  return [...current, ...incoming.filter((link) => {
    const key = `${link.sourcePath}\u0000${link.line}\u0000${link.column}\u0000${link.target}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  })];
}

export function ArtifactMemoryView({ config }: { config: AppConfig }) {
  const reduce = useReducedMotion();
  const [artifacts, setArtifacts] = useState<MemoryArtifactSummary[]>([]);
  const [indexDocuments, setIndexDocuments] = useState<Map<string, string>>(() => new Map());
  const [indexErrors, setIndexErrors] = useState<Map<string, string>>(() => new Map());
  const [indexLoading, setIndexLoading] = useState(true);
  const [selected, setSelected] = useState<string | null>(null);
  const [activeDetail, setActiveDetail] = useState<MemoryArtifactDetail | null>(null);
  const [contentLoading, setContentLoading] = useState(false);
  const [contentError, setContentError] = useState<string | null>(null);
  const [contentNotice, setContentNotice] = useState<string | null>(null);
  const [contentRefreshKey, setContentRefreshKey] = useState(0);
  const [query, setQuery] = useState("");
  const [searchResults, setSearchResults] = useState<MemoryArtifactSummary[] | null>(null);
  const [searchLoading, setSearchLoading] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);
  const [direction, setDirection] = useState(1);
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
  const [inspectorOpen, setInspectorOpen] = useState(false);
  const [pageLinks, setPageLinks] = useState<PageLinks | null>(null);
  const [pageHistory, setPageHistory] = useState<PageEditHistory | null>(null);
  const [linksLoading, setLinksLoading] = useState(false);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [linksLoadingMore, setLinksLoadingMore] = useState(false);
  const [historyLoadingMore, setHistoryLoadingMore] = useState(false);
  const [linkError, setLinkError] = useState<string | null>(null);
  const [linksRefreshKey, setLinksRefreshKey] = useState(0);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [historyVersion, setHistoryVersion] = useState(0);
  const [historyRefreshKey, setHistoryRefreshKey] = useState(0);
  const [pageHistoryPath, setPageHistoryPath] = useState<string | null>(null);
  const [editing, setEditing] = useState<EditingSession | null>(null);
  const [editReview, setEditReview] = useState<ReviewState | null>(null);
  const [externalReviewQueue, setExternalReviewQueue] = useState<PageEditEvent[]>([]);
  const [pausedExternalReviewId, setPausedExternalReviewId] = useState<string | null>(null);
  const [editDecisions, setEditDecisions] = useState<Record<string, DiffReviewDecision>>({});
  const [editPending, setEditPending] = useState(false);
  const [reviewPending, setReviewPending] = useState(false);
  const [editError, setEditError] = useState<string | null>(null);

  const summaryRequest = useRef<SummaryRequest | null>(null);
  const indexGeneration = useRef(0);
  const recordsRequestId = useRef(0);
  const detailCache = useRef(new ArtifactCache());
  const navigationHistory = useRef(new NavigationHistory());
  const pendingRestore = useRef<NavigationLocation | null>(null);
  const linksRequestId = useRef(0);
  const historyRequestId = useRef(0);
  const indexDetailCache = useRef(new RevisionCache<string>(24));
  const artifactsRef = useRef<MemoryArtifactSummary[]>([]);
  const queryRef = useRef("");
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
  const externalReviewQueueRef = useRef<PageEditEvent[]>([]);
  const reviewSelectionPathRef = useRef<string | null>(null);
  const memoryVaultChangesRef = useRef(useStore.getState().memoryVaultChanges);
  const processedMemoryChangeSeqs = useRef(new Set<number>());
  const memoryChangeDrainRunning = useRef(false);
  const memoryChangeDrainRequested = useRef(false);
  const memoryChangeDrainRef = useRef<(() => Promise<void>) | null>(null);
  const restoreNoteFocus = useRef(false);
  const mountedRef = useRef(true);
  const disposeControllerRef = useRef(new AbortController());

  artifactsRef.current = artifacts;
  queryRef.current = query.trim();
  editingRef.current = editing;
  editReviewRef.current = editReview;
  externalReviewQueueRef.current = externalReviewQueue;

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
      indexGeneration.current += 1;
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
    setSearchLoading(false);
    setRebuilding(false);
    return next;
  }, []);

  const isCurrentSummaryRequest = useCallback((request: SummaryRequest) =>
    mountedRef.current && summaryRequest.current?.epoch === request.epoch, []);

  useEffect(() => () => summaryRequest.current?.controller.abort(), []);

  const refreshIndexDocuments = useCallback(async (summaries: MemoryArtifactSummary[]) => {
    if (!mountedRef.current) return;
    const generation = ++indexGeneration.current;
    setIndexLoading(true);
    const selectedDocuments = selectIndexDocuments(summaries);
    const results = await Promise.all(selectedDocuments.map(async (summary) => {
      const revision = summary.revision ?? "unknown";
      const cached = indexDetailCache.current.get(summary.path, revision);
      if (cached != null) return { path: summary.path, content: cached, error: null };
      try {
        const response = await readMemoryArtifactDetail(config, summary.path, { signal: disposeControllerRef.current.signal });
        indexDetailCache.current.set(summary.path, response.artifact.revision, response.artifact.content);
        return { path: summary.path, content: response.artifact.content, error: null };
      } catch (reason) {
        return {
          path: summary.path,
          content: null,
          error: reason instanceof Error ? reason.message : String(reason),
        };
      }
    }));
    if (!mountedRef.current || generation !== indexGeneration.current) return;
    setIndexDocuments((current) => {
      const next = new Map<string, string>();
      for (const result of results) {
        if (result.content != null) next.set(result.path, result.content);
        else {
          const lastGood = current.get(result.path);
          if (lastGood != null) next.set(result.path, lastGood);
        }
      }
      return next;
    });
    setIndexErrors(new Map(results.flatMap((result) => result.error == null ? [] : [[result.path, result.error]])));
    setIndexLoading(false);
  }, [config]);

  const acceptSummaries = useCallback((next: MemoryArtifactSummary[]) => {
    if (!mountedRef.current) return;
    artifactsRef.current = next;
    setArtifacts(next);
    void refreshIndexDocuments(next);
  }, [refreshIndexDocuments]);

  const load = useCallback(async (): Promise<boolean> => {
    if (!mountedRef.current) return false;
    const request = beginSummaryRequest();
    setLoading(true);
    setError(null);
    try {
      const response = await listMemoryArtifactSummaries(config, {}, { signal: request.controller.signal });
      if (!isCurrentSummaryRequest(request)) return false;
      acceptSummaries(response.artifacts);
      return true;
    } catch (reason) {
      if (!isCurrentSummaryRequest(request)) return false;
      setIndexLoading(false);
      setError(reason instanceof Error ? reason.message : String(reason));
      return false;
    } finally {
      if (isCurrentSummaryRequest(request)) setLoading(false);
    }
  }, [acceptSummaries, beginSummaryRequest, config, isCurrentSummaryRequest]);

  const search = useCallback(async (value: string): Promise<boolean> => {
    if (!mountedRef.current) return false;
    const queryValue = value.trim();
    if (!queryValue) return false;
    const request = beginSummaryRequest();
    setSearchLoading(true);
    setSearchError(null);
    setSearchResults(null);
    try {
      const response = await listMemoryArtifactSummaries(config, { q: queryValue }, { signal: request.controller.signal });
      if (!isCurrentSummaryRequest(request) || queryRef.current !== queryValue) return false;
      setSearchResults(response.artifacts.filter(isNotebookPage));
      return true;
    } catch (reason) {
      if (!isCurrentSummaryRequest(request)) return false;
      setSearchError(reason instanceof Error ? reason.message : String(reason));
      return false;
    } finally {
      if (isCurrentSummaryRequest(request)) setSearchLoading(false);
    }
  }, [beginSummaryRequest, config, isCurrentSummaryRequest]);

  useEffect(() => {
    void load();
  }, [load]);

  const previousQuery = useRef("");
  useEffect(() => {
    const value = query.trim();
    const previous = previousQuery.current;
    previousQuery.current = value;
    if (!value) {
      setSearchResults(null);
      setSearchError(null);
      setSearchLoading(false);
      if (previous) {
        beginSummaryRequest();
        if (artifactsRef.current.length === 0) void load();
      }
      return;
    }
    const request = beginSummaryRequest();
    setSearchResults(null);
    setSearchError(null);
    setSearchLoading(true);
    const timer = window.setTimeout(() => {
      if (!isCurrentSummaryRequest(request)) return;
      void listMemoryArtifactSummaries(config, { q: value }, { signal: request.controller.signal })
        .then((response) => {
          if (!isCurrentSummaryRequest(request) || queryRef.current !== value) return;
          setSearchResults(response.artifacts.filter(isNotebookPage));
        })
        .catch((reason) => {
          if (isCurrentSummaryRequest(request)) setSearchError(reason instanceof Error ? reason.message : String(reason));
        })
        .finally(() => {
          if (isCurrentSummaryRequest(request)) setSearchLoading(false);
        });
    }, SEARCH_DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
  }, [beginSummaryRequest, config, isCurrentSummaryRequest, load, query]);

  const railModel = useMemo(() => buildNotebookRailModel(artifacts, indexDocuments), [artifacts, indexDocuments]);
  const indexBlocked = indexErrors.has("index.md") && !indexDocuments.has("index.md");
  const navigableArtifacts = useMemo(() => artifacts.filter((artifact) => isNotebookResourcePath(artifact.path)), [artifacts]);
  const selectedMeta = navigableArtifacts.find((artifact) => artifact.path === selected)
    ?? searchResults?.find((artifact) => artifact.path === selected)
    ?? null;
  selectedMetaRef.current = selectedMeta;

  useEffect(() => {
    if (indexLoading) return;
    if (mutationPendingRef.current) return;
    const fallback = firstNotebookPath(railModel.entries) ?? railModel.files[0]?.path ?? null;
    if (selected && (
      navigableArtifacts.some((artifact) => artifact.path === selected)
      || searchResults?.some((artifact) => artifact.path === selected)
    )) return;
    if (fallback) {
      navigationHistory.current.push({ path: fallback, anchor: null, scrollTop: 0, focusSelector: null });
      setHistoryVersion((version) => version + 1);
    }
    setSelected(fallback);
  }, [indexLoading, navigableArtifacts, railModel.entries, railModel.files, reviewPending, searchResults, selected]);

  const primaryOrder = useMemo(() => {
    const paths: string[] = [];
    const visit = (entries: typeof railModel.entries) => {
      for (const entry of entries) {
        if (entry.kind === "note") paths.push(entry.path);
        else visit(entry.children);
      }
    };
    visit(railModel.entries);
    return [...paths, ...railModel.files.map((artifact) => artifact.path)];
  }, [railModel]);

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
    const scroller = layoutRef.current?.querySelector<HTMLElement>("[data-memory-note-scroll]");
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
    const from = primaryOrder.indexOf(selectedMeta?.path ?? selected ?? "");
    const to = primaryOrder.indexOf(path);
    if (from !== -1 && to !== -1 && from !== to) setDirection(to > from ? 1 : -1);
    setContentNotice(null);
    setSelected(path);
    if (recordsOpen) {
      setRecordsOpen(false);
      focusRailNote(path);
    }
  }, [currentLocation, focusRailNote, primaryOrder, recordsOpen, selected, selectedMeta?.path]);

  const selectFile = useCallback((path: string) => navigateTo(path), [navigateTo]);

  const moveHistory = useCallback((movement: "back" | "forward") => {
    if (mutationPendingRef.current) return;
    const current = currentLocation();
    if (current) navigationHistory.current.replaceCurrent(current);
    const location = movement === "back" ? navigationHistory.current.back() : navigationHistory.current.forward();
    if (!location) return;
    pendingRestore.current = location;
    setHistoryVersion((version) => version + 1);
    const from = primaryOrder.indexOf(selectedMeta?.path ?? selected ?? "");
    const to = primaryOrder.indexOf(location.path);
    if (from !== -1 && to !== -1 && from !== to) setDirection(to > from ? 1 : -1);
    setContentNotice(null);
    setQuery("");
    setSelected(location.path);
  }, [currentLocation, primaryOrder, selected, selectedMeta?.path]);

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
    const snapshot = snapshotEditing(editing, editRequestGeneration.current);
    setEditReview((current) => current?.kind === "conflict" && current.conflict.currentRevision === activeDetail.revision
      ? current
      : {
          kind: "conflict",
          generation: ++reviewGeneration.current,
          snapshot,
          conflict: { currentRevision: activeDetail.revision, currentContent },
        });
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

  const rememberExternalReview = useCallback((event: PageEditEvent) => {
    const next = enqueueExternalReview(externalReviewQueueRef.current, event);
    externalReviewQueueRef.current = next;
    setExternalReviewQueue(next);
  }, []);

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
          const accepted = await load();
          if (mountedRef.current && accepted && queryRef.current) await search(queryRef.current);
          if (mountedRef.current && recordsOpen) setRecordsRefreshKey((key) => key + 1);
        }
        if (next.reviewRequired && next.revision) {
          try {
            for (const path of next.paths) {
              const history = await getPageHistory(
                config,
                { path, limit: 100 },
                { signal: disposeControllerRef.current.signal },
              );
              if (!mountedRef.current) return;
              const event = history.events.find((candidate) =>
                candidate.path === path
                && candidate.origin === "external"
                && candidate.reconciliation === "needs_review"
                && candidate.resultRevision === next.revision,
              );
              if (event) rememberExternalReview(event);
            }
          } catch (reason) {
            if (mountedRef.current && selectedMetaRef.current && next.paths.includes(selectedMetaRef.current.path)) {
              setEditError(reason instanceof Error ? reason.message : String(reason));
            }
            return;
          }
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
  }, [config, load, recordsOpen, rememberExternalReview, search]);
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
    void load().then((accepted) => {
      if (accepted && queryRef.current) void search(queryRef.current);
    });
    if (recordsOpen) setRecordsRefreshKey((key) => key + 1);
  }, [load, memoryVaultChanges, memoryVaultVersion, recordsOpen, search]);

  const selectedHasWikilinks = activeDetail != null
    && selectedMeta != null
    && activeDetail.path === selectedMeta.path
    && activeDetail.content.includes("[[");
  const shouldLoadLinks = selectedHasWikilinks || inspectorOpen;
  useEffect(() => {
    const controller = new AbortController();
    const requestId = ++linksRequestId.current;
    setPageLinks(null);
    setLinksLoadingMore(false);
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
    setHistoryLoadingMore(false);
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
  }, [config, contentRefreshKey, historyRefreshKey, inspectorOpen, selectedMeta?.path]);

  const currentPageLinks = pageLinks?.path === selectedMeta?.path ? pageLinks : null;
  const currentPageHistory = pageHistoryPath === selectedMeta?.path ? pageHistory : null;
  const artifactPaths = useMemo(() => new Set(navigableArtifacts.map((artifact) => artifact.path)), [navigableArtifacts]);

  const wikiHandlers = useMemo<WikiLinkHandlers>(() => ({
    exists: (target) => resolveWikiTarget(currentPageLinks, target) !== null,
    onNavigate: (target) => {
      const resolved = resolveWikiTarget(currentPageLinks, target);
      if (!resolved) return;
      setQuery("");
      navigateTo(resolved.path, resolved.anchor);
    },
    existsInline: (target) => artifactPaths.has(target),
    onNavigateInline: (target) => {
      if (!artifactPaths.has(target)) return;
      setQuery("");
      navigateTo(target, null);
    },
  }), [artifactPaths, currentPageLinks, navigateTo]);

  const loadPreviewDetail = useCallback(async (path: string, signal: AbortSignal) => {
    const response = await readMemoryArtifactDetail(config, path, { signal });
    return response.artifact;
  }, [config]);

  const loadMoreLinks = useCallback(() => {
    const current = currentPageLinks;
    const path = selectedMeta?.path;
    if (!current || !path || linksLoadingMore || current.stale) return;
    const offset = current.offset + current.limit;
    const requestId = linksRequestId.current;
    setLinksLoadingMore(true);
    setLinkError(null);
    const signal = disposeControllerRef.current.signal;
    void getPageLinks(config, { path, limit: current.limit, offset }, { signal }).then(async (page) => {
      if (linksRequestId.current !== requestId || selectedMetaRef.current?.path !== path) return;
      if (page.revision !== current.revision) {
        const fresh = await getPageLinks(config, { path, limit: current.limit, offset: 0 }, { signal });
        if (linksRequestId.current === requestId && selectedMetaRef.current?.path === path) setPageLinks(fresh);
        return;
      }
      setPageLinks((previous) => previous?.path === path && previous.revision === page.revision
        ? {
            ...page,
            outgoing: mergeLinks(previous.outgoing, page.outgoing),
            backlinks: mergeLinks(previous.backlinks, page.backlinks),
          }
        : previous);
    }).catch((reason) => {
      if (linksRequestId.current === requestId) setLinkError(reason instanceof Error ? reason.message : String(reason));
    }).finally(() => {
      if (linksRequestId.current === requestId) setLinksLoadingMore(false);
    });
  }, [config, currentPageLinks, linksLoadingMore, selectedMeta?.path]);

  const loadMoreHistory = useCallback(() => {
    const current = currentPageHistory;
    const path = selectedMeta?.path;
    if (!current || !path || historyLoadingMore || current.nextBeforeSequence == null) return;
    const requestId = historyRequestId.current;
    setHistoryLoadingMore(true);
    setHistoryError(null);
    void getPageHistory(
      config,
      { path, limit: current.limit, beforeSequence: current.nextBeforeSequence },
      { signal: disposeControllerRef.current.signal },
    ).then((page) => {
      if (historyRequestId.current !== requestId || selectedMetaRef.current?.path !== path) return;
      setPageHistory((previous) => {
        if (pageHistoryPath !== path || !previous) return page;
        const ids = new Set(previous.events.map((event) => event.id));
        return { ...page, events: [...previous.events, ...page.events.filter((event) => !ids.has(event.id))] };
      });
    }).catch((reason) => {
      if (historyRequestId.current === requestId) setHistoryError(reason instanceof Error ? reason.message : String(reason));
    }).finally(() => {
      if (historyRequestId.current === requestId) setHistoryLoadingMore(false);
    });
  }, [config, currentPageHistory, historyLoadingMore, pageHistoryPath, selectedMeta?.path]);

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
    setPinningId(record.id);
    setRecords((current) => current.map((item) => item.id === record.id ? { ...item, pinned: next } : item));
    setRecordPinned(config, record.id, next)
      .catch((reason) => {
        setRecords((current) => current.map((item) => item.id === record.id ? { ...item, pinned: record.pinned } : item));
        setRecordsError(reason instanceof Error ? reason.message : String(reason));
      })
      .finally(() => setPinningId((current) => current === record.id ? null : current));
  };

  const rebuild = () => {
    if (!mountedRef.current) return;
    const request = beginSummaryRequest();
    setRebuilding(true);
    setContentNotice(null);
    setError(null);
    rebuildMemoryArtifactSummaries(config, { signal: request.controller.signal })
      .then((response) => {
        if (!isCurrentSummaryRequest(request)) return false;
        acceptSummaries(response.artifacts);
        return true;
      })
      .then((accepted) => {
        if (accepted && queryRef.current) void search(queryRef.current);
      })
      .catch((reason) => {
        if (isCurrentSummaryRequest(request)) setError(reason instanceof Error ? reason.message : String(reason));
      })
      .finally(() => {
        if (isCurrentSummaryRequest(request)) setRebuilding(false);
      });
  };

  const rightPanelOpen = recordsOpen || inspectorOpen;
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

  const forgetExactRecord = useCallback(async (recordId: string, eventId: string, questionId: string) => {
    await retryPageEdit(config, {
      eventId,
      decisions: { [questionId]: { choice: "forget_memory", targetIds: [recordId] } },
    });
    detailCache.current.invalidatePath(selectedMetaRef.current?.path ?? "");
    setContentRefreshKey((key) => key + 1);
    setHistoryRefreshKey((key) => key + 1);
    setRecordsRefreshKey((key) => key + 1);
    await load();
  }, [config, load]);

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

  const promoteExternalReview = useCallback((
    queue: PageEditEvent[] = externalReviewQueueRef.current,
    path: string | null | undefined = selectedMetaRef.current?.path,
  ) => {
    const next = queue.find((event) => event.path === path);
    if (!next || next.id === pausedExternalReviewId) return false;
    setEditDecisions({});
    setEditError(null);
    setEditReview({ kind: "external", generation: ++reviewGeneration.current, event: next });
    return true;
  }, [pausedExternalReviewId]);

  useEffect(() => {
    if (editReview || reviewPending || externalReviewQueue.length === 0) return;
    const draft = editingRef.current;
    const path = selectedMeta?.path;
    const next = externalReviewQueue.find((event) => event.path === path);
    if (!next || next.id === pausedExternalReviewId) return;
    if (draft && draft.draftContent !== draft.baseContent && draft.baseRevision !== next.resultRevision) return;
    promoteExternalReview(externalReviewQueue, path);
  }, [editReview, externalReviewQueue, pausedExternalReviewId, promoteExternalReview, reviewPending, selectedMeta?.path]);

  const returnFromEditReview = useCallback(() => {
    if (reviewPending) return;
    const currentReview = editReviewRef.current;
    if (currentReview?.kind === "external") {
      setPausedExternalReviewId(currentReview.event.id);
      setEditDecisions({});
      setEditError(null);
      const draft = editingRef.current;
      if (
        draft
        && activeDetail?.path === draft.path
        && activeDetail.revision !== draft.baseRevision
        && draft.draftContent !== draft.baseContent
      ) {
        setEditReview({
          kind: "conflict",
          generation: ++reviewGeneration.current,
          snapshot: snapshotEditing(draft, editRequestGeneration.current),
          conflict: {
            currentRevision: activeDetail.revision,
            currentContent: activeDetail.editableContent ?? activeDetail.content,
          },
        });
      } else {
        setEditReview(null);
      }
      return;
    }
    if (promoteExternalReview()) return;
    setEditReview(null);
    setEditDecisions({});
    setEditError(null);
  }, [activeDetail, promoteExternalReview, reviewPending]);

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
    if (!promoteExternalReview()) {
      setEditReview(null);
    }
    setEditDecisions({});
    setEditError(null);
  }, [editReview, editing, promoteExternalReview]);

  const finishExternalReview = useCallback((eventId: string) => {
    const remaining = externalReviewQueueRef.current.filter((event) => event.id !== eventId);
    externalReviewQueueRef.current = remaining;
    setExternalReviewQueue(remaining);
    setPausedExternalReviewId((current) => current === eventId ? null : current);
    if (promoteExternalReview(remaining, selectedMetaRef.current?.path)) return;
    const draft = editingRef.current;
    if (
      draft
      && activeDetail?.path === draft.path
      && activeDetail.revision !== draft.baseRevision
      && draft.draftContent !== draft.baseContent
    ) {
      setEditReview({
        kind: "conflict",
        generation: ++reviewGeneration.current,
        snapshot: snapshotEditing(draft, editRequestGeneration.current),
        conflict: {
          currentRevision: activeDetail.revision,
          currentContent: activeDetail.editableContent ?? activeDetail.content,
        },
      });
    } else {
      setEditReview(null);
    }
    setEditDecisions({});
    setEditError(null);
  }, [activeDetail, promoteExternalReview]);

  const applyEditReview = useCallback(async () => {
    const review = editReviewRef.current;
    if (!review || review.kind === "conflict" || mutationPendingRef.current) return;
    const transactionGeneration = ++applyGeneration.current;
    const activeReviewGeneration = review.generation;
    const submittedDecisions = review.kind === "external"
      ? decisionsForServer(review.event.reviewOperations, review.event.questions)
      : decisionsForServer(review.preview.operations, review.preview.questions);
    mutationPendingRef.current = true;
    setReviewPending(true);
    setEditError(null);
    try {
      if (review.kind === "external") {
        await retryPageEdit(config, {
          eventId: review.event.id,
          decisions: submittedDecisions,
        });
        if (applyGeneration.current !== transactionGeneration || !mountedRef.current) return;
        finishExternalReview(review.event.id);
        setHistoryRefreshKey((key) => key + 1);
        setRecordsRefreshKey((key) => key + 1);
        setContentNotice("Resolved external memory effects.");
        return;
      }
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
  }, [completeLocalSave, config, decisionsForServer, finishExternalReview]);

  useEffect(() => {
    if (mutationPendingRef.current) return;
    const path = selectedMeta?.path ?? null;
    const pathChanged = reviewSelectionPathRef.current !== path;
    reviewSelectionPathRef.current = path;
    if (pathChanged) setPausedExternalReviewId(null);
    const currentReview = editReviewRef.current;
    if (currentReview?.kind === "external" && currentReview.event.path !== path) {
      setEditReview(null);
      setEditDecisions({});
      setEditError(null);
    }
    if (editing && path !== editing.path) {
      setEditing(null);
      if (currentReview?.kind !== "external") setEditReview(null);
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
      if (!editorTextarea && [target, focused].some(isInteractiveShortcutTarget)) return;
      if (key === "e") {
        if (!editing && (!visibleDetail?.editable || visibleDetail.editableContent == null)) return;
        event.preventDefault();
        if (editing) {
          editRequestGeneration.current += 1;
          setEditPending(false);
          setEditReview(null);
          setEditing(null);
        } else beginEditing();
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
    restoreNoteFocus.current = false;
    queueMicrotask(() => {
      const article = Array.from(layoutRef.current?.querySelectorAll<HTMLElement>("[data-memory-note-path]") ?? [])
        .find((candidate) => candidate.dataset.memoryNotePath === visibleDetail.path);
      article?.focus({ preventScroll: true });
    });
  }, [editReview, editing, visibleDetail]);

  const reviewPresentation = useMemo(() => {
    if (!editReview) return null;
    if (editReview.kind === "external") {
      if (editReview.event.path !== selectedMeta?.path) return null;
      const analysis = editReview.event.analysis;
      return {
        path: editReview.event.path,
        baseContent: analysis?.before.join("\n") ?? "",
        draftContent: analysis?.after.join("\n") ?? "",
        operations: editReview.event.reviewOperations,
        analysisPending: false,
      };
    }
    if (!editing) return null;
    return {
      path: editReview.snapshot.path,
      baseContent: editReview.snapshot.baseContent,
      draftContent: editReview.snapshot.candidateContent,
      operations: editReview.kind === "preview" ? editReview.preview.operations : [],
      analysisPending: editReview.kind === "preview" && editReview.preview.analysisPending,
    };
  }, [editReview, editing, selectedMeta?.path]);

  return (
    <div
      ref={layoutRef}
      data-memory-layout="notebook"
      className={clsx(
        "relative grid h-full min-h-0 max-[640px]:grid-cols-[180px_minmax(0,1fr)_0px]",
        rightPanelOpen
          ? "grid-cols-[280px_minmax(0,1fr)_320px] max-[900px]:grid-cols-[200px_minmax(0,1fr)_0px]"
          : "grid-cols-[280px_minmax(0,1fr)_0px] max-[900px]:grid-cols-[200px_minmax(0,1fr)_0px]",
      )}
    >
      <nav data-memory-zone="rail" aria-label="Memory notebook" className="flex min-h-0 flex-col border-r border-line-soft">
        <NotebookRail
          model={railModel}
          searchResults={searchResults}
          selectedPath={selectedMeta?.path ?? null}
          query={query}
          loading={loading || indexLoading}
          searchLoading={searchLoading}
          error={error}
          searchError={searchError}
          indexErrors={[...indexErrors.entries()].map(([path, message]) => `${path}: ${message}`)}
          indexBlocked={indexBlocked}
          rebuilding={rebuilding}
          recordsOpen={recordsOpen}
          navigationDisabled={reviewPending}
          recordsTriggerRef={recordsTriggerRef}
          onQueryChange={setQuery}
          onSelect={selectFile}
          onRetry={() => void load()}
          onRetryIndex={() => void refreshIndexDocuments(artifactsRef.current)}
          onRebuild={rebuild}
          onToggleRecords={() => {
            if (mutationPendingRef.current) return;
            if (recordsOpen) closeRecords();
            else {
              setInspectorOpen(false);
              setRecordsOpen(true);
            }
          }}
        />
      </nav>

      <main data-memory-zone="workspace" aria-label={recordsOpen ? "Raw record" : editReview ? "Memory edit review" : editing ? "Memory editor" : "Memory note"} className="relative min-h-0 overflow-hidden">
        {!recordsOpen && !editing && !editReview && (
          <div className="absolute right-3 top-3 z-10 flex items-center gap-1 rounded-[9px] border border-line-soft bg-bg-main/90 p-1 shadow-sm backdrop-blur">
            <button
              type="button"
              aria-label="Edit memory note"
              title="Edit (Cmd/Ctrl+E)"
              disabled={!visibleDetail?.editable || visibleDetail.editableContent == null}
              onClick={beginEditing}
              className="grid size-7 place-items-center rounded-[6px] text-muted hover:bg-surface-soft hover:text-ink disabled:opacity-35"
            >
              <Pencil className="size-3.5" />
            </button>
            <button
              type="button"
              aria-label="Back in memory history"
              title="Back (Cmd/Ctrl+[)"
              disabled={reviewPending || !navigationHistory.current.canBack}
              onClick={() => moveHistory("back")}
              className="grid size-7 place-items-center rounded-[6px] text-muted hover:bg-surface-soft hover:text-ink disabled:opacity-35"
            >
              <ChevronLeft className="size-4" />
            </button>
            <button
              type="button"
              aria-label="Forward in memory history"
              title="Forward (Cmd/Ctrl+])"
              disabled={reviewPending || !navigationHistory.current.canForward}
              onClick={() => moveHistory("forward")}
              className="grid size-7 place-items-center rounded-[6px] text-muted hover:bg-surface-soft hover:text-ink disabled:opacity-35"
            >
              <ChevronRight className="size-4" />
            </button>
            <button
              type="button"
              aria-label={inspectorOpen ? "Close memory trust inspector" : "Open memory trust inspector"}
              aria-pressed={inspectorOpen}
              onClick={() => setInspectorOpen((open) => !open)}
              className="grid size-7 place-items-center rounded-[6px] text-muted hover:bg-surface-soft hover:text-ink aria-pressed:bg-surface-soft aria-pressed:text-ink"
            >
              <PanelRight className="size-4" />
            </button>
          </div>
        )}
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
            reviewId={editReview.kind === "external" ? editReview.event.id : editReview.kind === "preview" ? editReview.preview.id : `conflict:${editReview.conflict.currentRevision}`}
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
            title={editing.title}
            baseRevision={editing.baseRevision}
            baseContent={editing.baseContent}
            value={editing.draftContent}
            saving={editPending}
            error={editError}
            onChange={(draftContent) => {
              editRequestGeneration.current += 1;
              editPreviewController.current?.abort();
              setEditPending(false);
              if (draftContent === editing.baseContent) clearDraft(editing.path, editing.baseRevision);
              else setDraft(editing.path, editing.baseRevision, draftContent);
              setEditing((current) => current ? { ...current, draftContent } : current);
            }}
            onSave={() => void requestEditPreview()}
            onClose={() => {
              editRequestGeneration.current += 1;
              editPreviewController.current?.abort();
              setEditPending(false);
              setEditing(null);
            }}
          />
        ) : (
          <FileDetailPane
            summary={selectedMeta}
            detail={visibleDetail}
            loading={loading || indexLoading}
            direction={direction}
            contentNotice={contentNotice}
            contentError={contentError}
            contentLoading={contentLoading}
            wikiHandlers={wikiHandlers}
            onRetry={() => {
              if (selectedMeta) {
                detailCache.current.invalidatePath(selectedMeta.path);
              }
              setContentRefreshKey((key) => key + 1);
            }}
          />
        )}
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

      <aside
        data-memory-zone="inspector"
        aria-label="Memory inspector"
        aria-hidden={!rightPanelOpen}
        className={clsx(
          "min-h-0 overflow-hidden",
          rightPanelOpen && "border-l border-line-soft max-[900px]:absolute max-[900px]:inset-y-0 max-[900px]:right-0 max-[900px]:z-20 max-[900px]:w-[min(320px,calc(100%-24px))] max-[900px]:bg-bg-main max-[900px]:shadow-lg",
        )}
      >
        {recordsOpen && (
          <section aria-label="Raw records diagnostic" className="flex h-full min-h-0 flex-col">
            <div className="flex items-center justify-between px-3 pb-1 pt-3">
              <div>
                <h2 ref={recordsHeadingRef} tabIndex={-1} className="text-sm font-semibold text-ink outline-none">Raw records</h2>
                <p className="text-2xs text-muted">Diagnostic view</p>
              </div>
              <button
                type="button"
                onClick={closeRecords}
                aria-label="Close raw records diagnostic"
                className="grid size-7 place-items-center rounded-[8px] text-muted hover:bg-surface-soft hover:text-ink"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
            <TreeSearch value={recordsQuery} onChange={setRecordsQuery} placeholder="Search raw records…" />
            <div className="flex justify-end px-3 pt-2">
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
              />
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
        )}
        {!recordsOpen && inspectorOpen && visibleDetail && (
          <MemoryInspector
            page={visibleDetail}
            links={currentPageLinks}
            history={currentPageHistory}
            linksLoading={linksLoading}
            historyLoading={historyLoading}
            linksLoadingMore={linksLoadingMore}
            historyLoadingMore={historyLoadingMore}
            linkError={linkError}
            historyError={historyError}
            navigationDisabled={reviewPending}
            onNavigate={navigateTo}
            onRetryLinks={() => setLinksRefreshKey((key) => key + 1)}
            onLoadMoreLinks={loadMoreLinks}
            onLoadMoreHistory={loadMoreHistory}
            onCorrect={() => beginEditing()}
            onForget={forgetExactRecord}
          />
        )}
      </aside>
    </div>
  );
}
