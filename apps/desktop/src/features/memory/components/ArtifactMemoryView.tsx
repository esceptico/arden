import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ChevronLeft, ChevronRight, PanelRight, X } from "lucide-react";
import { useReducedMotion } from "motion/react";
import clsx from "clsx";
import { useStore } from "@/stores";
import type { AppConfig } from "@/api/core";
import type { WikiLinkHandlers } from "@/lib/wikilink";
import {
  getPageHistory,
  getPageLinks,
  listMemoryArtifactSummaries,
  readMemoryArtifactDetail,
  rebuildMemoryArtifactSummaries,
} from "@/api/memoryArtifacts";
import { listMemoryItems, setRecordPinned, type MemoryItem, type MemoryKind } from "@/api/memoryItems";
import { Select } from "@/components/ui/Select";
import { TreeSearch } from "@/features/memory/components/MemoryFileTree";
import { NotebookRail } from "@/features/memory/components/NotebookRail";
import { FileDetailPane } from "@/features/memory/components/FileDetailPane";
import { RecordDetailPane } from "@/features/memory/components/RecordDetailPane";
import { RecordListPane } from "@/features/memory/components/RecordListPane";
import { MemoryInspector } from "@/features/memory/components/MemoryInspector";
import { WikiLinkPreview } from "@/features/memory/components/WikiLinkPreview";
import { ArtifactCache, RevisionCache } from "@/features/memory/lib/artifactCache";
import { NavigationHistory, type NavigationLocation } from "@/features/memory/lib/navigationHistory";
import { isMissingArtifactError, resolveWikiTarget } from "@/features/memory/lib/wikiResolution";
import {
  buildNotebookRailModel,
  firstNotebookPath,
  isNotebookPage,
  isNotebookResourcePath,
  selectIndexDocuments,
} from "@/features/memory/lib/notebookIndex";
import type { MemoryArtifactDetail, MemoryArtifactSummary, PageEditHistory, PageLinks } from "@/features/memory/lib/notebookTypes";

const RECORD_PAGE_SIZE = 100;
const SEARCH_DEBOUNCE_MS = 180;

interface SummaryRequest {
  epoch: number;
  controller: AbortController;
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
  const [trustError, setTrustError] = useState<string | null>(null);
  const [historyVersion, setHistoryVersion] = useState(0);
  const [pageHistoryPath, setPageHistoryPath] = useState<string | null>(null);

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

  artifactsRef.current = artifacts;
  queryRef.current = query.trim();

  const beginSummaryRequest = useCallback((): SummaryRequest => {
    summaryRequest.current?.controller.abort();
    const next = {
      epoch: (summaryRequest.current?.epoch ?? 0) + 1,
      controller: new AbortController(),
    };
    summaryRequest.current = next;
    setLoading(false);
    setSearchLoading(false);
    setRebuilding(false);
    return next;
  }, []);

  const isCurrentSummaryRequest = useCallback((request: SummaryRequest) =>
    summaryRequest.current?.epoch === request.epoch, []);

  useEffect(() => () => summaryRequest.current?.controller.abort(), []);

  const refreshIndexDocuments = useCallback(async (summaries: MemoryArtifactSummary[]) => {
    const generation = ++indexGeneration.current;
    setIndexLoading(true);
    const selectedDocuments = selectIndexDocuments(summaries);
    const results = await Promise.all(selectedDocuments.map(async (summary) => {
      const revision = summary.revision ?? "unknown";
      const cached = indexDetailCache.current.get(summary.path, revision);
      if (cached != null) return { path: summary.path, content: cached, error: null };
      try {
        const response = await readMemoryArtifactDetail(config, summary.path);
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
    if (generation !== indexGeneration.current) return;
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
    artifactsRef.current = next;
    setArtifacts(next);
    void refreshIndexDocuments(next);
  }, [refreshIndexDocuments]);

  const load = useCallback(async (): Promise<boolean> => {
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
  }, [indexLoading, navigableArtifacts, railModel.entries, railModel.files, searchResults, selected]);

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
    const focusSelector = active instanceof HTMLElement && scroller?.contains(active) && active.dataset.wikilink
      ? `wikilink:${active.dataset.wikilink}`
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
    let cancelled = false;
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
    readMemoryArtifactDetail(config, selectedMeta.path)
      .then((response) => {
        if (cancelled) return;
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
        if (cancelled) return;
        if (isMissingArtifactError(reason)) {
          const missingPath = selectedMeta.path;
          setArtifacts((current) => current.filter((artifact) => artifact.path !== missingPath));
          setSelected((current) => current === missingPath ? null : current);
          setActiveDetail(null);
          setContentNotice("That memory note changed or disappeared; refreshed the index.");
          void load();
          return;
        }
        setContentError(reason instanceof Error ? reason.message : String(reason));
      })
      .finally(() => {
        if (!cancelled) setContentLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [config, contentRefreshKey, load, selectedMeta?.path, selectedMeta?.revision]);

  useEffect(() => {
    const detail = activeDetail;
    const location = pendingRestore.current;
    if (!detail || !location || location.path !== detail.path) return;
    pendingRestore.current = null;
    const frame = window.requestAnimationFrame(() => {
      const scroller = layoutRef.current?.querySelector<HTMLElement>("[data-memory-note-scroll]");
      if (!scroller) return;
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
      if (location.focusSelector?.startsWith("wikilink:")) {
        const target = location.focusSelector.slice("wikilink:".length);
        Array.from(scroller.querySelectorAll<HTMLElement>("[data-wikilink]"))
          .find((element) => element.dataset.wikilink === target)?.focus({ preventScroll: true });
      }
    });
    return () => window.cancelAnimationFrame(frame);
  }, [activeDetail, historyVersion]);

  const memoryVaultVersion = useStore((state) => state.memoryVaultVersion);
  const observedVaultVersion = useRef(memoryVaultVersion);
  useEffect(() => {
    if (memoryVaultVersion === observedVaultVersion.current) return;
    observedVaultVersion.current = memoryVaultVersion;
    const current = selectedMetaRef.current;
    if (current) {
      detailCache.current.invalidatePath(current.path);
      setContentRefreshKey((key) => key + 1);
    }
    void load().then((accepted) => {
      if (accepted && queryRef.current) void search(queryRef.current);
    });
    if (recordsOpen) setRecordsRefreshKey((key) => key + 1);
  }, [load, memoryVaultVersion, recordsOpen, search]);

  const selectedHasWikilinks = activeDetail != null
    && selectedMeta != null
    && activeDetail.path === selectedMeta.path
    && activeDetail.content.includes("[[");
  useEffect(() => {
    const requestId = ++linksRequestId.current;
    setPageLinks(null);
    if (!selectedMeta || (!selectedHasWikilinks && !inspectorOpen)) {
      setLinksLoading(false);
      return;
    }
    setLinksLoading(true);
    setTrustError(null);
    getPageLinks(config, { path: selectedMeta.path, limit: 100, offset: 0 }).then((links) => {
      if (linksRequestId.current === requestId) setPageLinks(links);
    }).catch((reason) => {
      if (linksRequestId.current !== requestId) return;
      setTrustError(reason instanceof Error ? reason.message : String(reason));
    }).finally(() => {
      if (linksRequestId.current === requestId) setLinksLoading(false);
    });
  }, [config, contentRefreshKey, inspectorOpen, selectedHasWikilinks, selectedMeta?.path]);

  useEffect(() => {
    const requestId = ++historyRequestId.current;
    setPageHistory(null);
    setPageHistoryPath(null);
    if (!selectedMeta || !inspectorOpen) {
      setHistoryLoading(false);
      return;
    }
    setHistoryLoading(true);
    setTrustError(null);
    getPageHistory(config, { path: selectedMeta.path, limit: 100 }).then((history) => {
      if (historyRequestId.current !== requestId) return;
      setPageHistory(history);
      setPageHistoryPath(selectedMeta.path);
    }).catch((reason) => {
      if (historyRequestId.current !== requestId) return;
      setTrustError(reason instanceof Error ? reason.message : String(reason));
    }).finally(() => {
      if (historyRequestId.current === requestId) setHistoryLoading(false);
    });
  }, [config, contentRefreshKey, inspectorOpen, selectedMeta?.path]);

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

  return (
    <div
      ref={layoutRef}
      data-memory-layout="notebook"
      className={clsx(
        "grid h-full min-h-0",
        rightPanelOpen
          ? "grid-cols-[280px_minmax(0,1fr)_320px]"
          : "grid-cols-[280px_minmax(0,1fr)_0px]",
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
          recordsTriggerRef={recordsTriggerRef}
          onQueryChange={setQuery}
          onSelect={selectFile}
          onRetry={() => void load()}
          onRetryIndex={() => void refreshIndexDocuments(artifactsRef.current)}
          onRebuild={rebuild}
          onToggleRecords={() => {
            if (recordsOpen) closeRecords();
            else {
              setInspectorOpen(false);
              setRecordsOpen(true);
            }
          }}
        />
      </nav>

      <main data-memory-zone="workspace" aria-label={recordsOpen ? "Raw record" : "Memory note"} className="relative min-h-0 overflow-hidden">
        {!recordsOpen && (
          <div className="absolute right-3 top-3 z-10 flex items-center gap-1 rounded-[9px] border border-line-soft bg-bg-main/90 p-1 shadow-sm backdrop-blur">
            <button
              type="button"
              aria-label="Back in memory history"
              title="Back (⌘[)"
              disabled={!navigationHistory.current.canBack}
              onClick={() => moveHistory("back")}
              className="grid size-7 place-items-center rounded-[6px] text-muted hover:bg-surface-soft hover:text-ink disabled:opacity-35"
            >
              <ChevronLeft className="size-4" />
            </button>
            <button
              type="button"
              aria-label="Forward in memory history"
              title="Forward (⌘])"
              disabled={!navigationHistory.current.canForward}
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
        {!recordsOpen && (
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
        className={clsx("min-h-0 overflow-hidden", rightPanelOpen && "border-l border-line-soft")}
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
            loading={linksLoading || historyLoading}
            error={trustError}
            onNavigate={navigateTo}
          />
        )}
      </aside>
    </div>
  );
}
