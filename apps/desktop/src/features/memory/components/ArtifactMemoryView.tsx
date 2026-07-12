import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { X } from "lucide-react";
import { useReducedMotion } from "motion/react";
import clsx from "clsx";
import { useStore } from "@/stores";
import type { AppConfig } from "@/api/core";
import { wikiSlug, type WikiLinkHandlers } from "@/lib/wikilink";
import {
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
import { addAlias, isMissingArtifactError, preferredAlias } from "@/features/memory/lib/wikiResolution";
import {
  buildNotebookRailModel,
  firstNotebookPath,
  isNotebookPage,
  isNotebookResourcePath,
  selectIndexDocuments,
} from "@/features/memory/lib/notebookIndex";
import type { MemoryArtifactDetail, MemoryArtifactSummary } from "@/features/memory/lib/notebookTypes";

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

  const summaryRequest = useRef<SummaryRequest | null>(null);
  const indexGeneration = useRef(0);
  const recordsRequestId = useRef(0);
  const detailCache = useRef(new Map<string, MemoryArtifactDetail>());
  const indexDetailCache = useRef(new Map<string, string>());
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
      const key = `${summary.path}@${summary.revision ?? "unknown"}`;
      const cached = indexDetailCache.current.get(key);
      if (cached != null) return { path: summary.path, content: cached, error: null };
      try {
        const response = await readMemoryArtifactDetail(config, summary.path);
        indexDetailCache.current.set(`${summary.path}@${response.artifact.revision}`, response.artifact.content);
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
    setSelected((current) => current && (
      navigableArtifacts.some((artifact) => artifact.path === current)
      || searchResults?.some((artifact) => artifact.path === current)
    ) ? current : fallback);
  }, [indexLoading, navigableArtifacts, railModel.entries, railModel.files, searchResults]);

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

  const selectFile = useCallback((path: string) => {
    const from = primaryOrder.indexOf(selectedMeta?.path ?? selected ?? "");
    const to = primaryOrder.indexOf(path);
    if (from !== -1 && to !== -1 && from !== to) setDirection(to > from ? 1 : -1);
    setContentNotice(null);
    setSelected(path);
    if (recordsOpen) {
      setRecordsOpen(false);
      focusRailNote(path);
    }
  }, [focusRailNote, primaryOrder, recordsOpen, selected, selectedMeta?.path]);

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
    const cacheKey = `${selectedMeta.path}@${selectedMeta.revision ?? "unknown"}`;
    const cached = detailCache.current.get(cacheKey);
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
        detailCache.current.set(`${detail.path}@${detail.revision}`, detail);
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

  const memoryVaultVersion = useStore((state) => state.memoryVaultVersion);
  const observedVaultVersion = useRef(memoryVaultVersion);
  useEffect(() => {
    if (memoryVaultVersion === observedVaultVersion.current) return;
    observedVaultVersion.current = memoryVaultVersion;
    const current = selectedMetaRef.current;
    if (current) {
      for (const key of detailCache.current.keys()) {
        if (key.startsWith(`${current.path}@`)) detailCache.current.delete(key);
      }
      setContentRefreshKey((key) => key + 1);
    }
    void load().then((accepted) => {
      if (accepted && queryRef.current) void search(queryRef.current);
    });
    if (recordsOpen) setRecordsRefreshKey((key) => key + 1);
  }, [load, memoryVaultVersion, recordsOpen, search]);

  const artifactPaths = useMemo(() => new Set(navigableArtifacts.map((artifact) => artifact.path)), [navigableArtifacts]);
  const artifactAliasMap = useMemo(() => {
    const map = new Map<string, Set<string>>();
    for (const artifact of navigableArtifacts) {
      const leaf = artifact.path.split("/").pop()?.replace(/\.md$/, "") ?? artifact.path;
      addAlias(map, artifact.path, artifact.path);
      addAlias(map, artifact.path.replace(/\.md$/, ""), artifact.path);
      addAlias(map, artifact.title, artifact.path);
      addAlias(map, wikiSlug(artifact.title), artifact.path);
      addAlias(map, leaf, artifact.path);
      addAlias(map, wikiSlug(leaf), artifact.path);
    }
    return map;
  }, [navigableArtifacts]);
  const resolveWiki = useMemo(() => (target: string): string | null => {
    const value = target.trim();
    if (artifactPaths.has(value)) return value;
    const directory = value.replace(/\/+$/, "");
    if (directory && artifactPaths.has(`${directory}/README.md`)) return `${directory}/README.md`;
    if (directory && artifactPaths.has(`${directory}/index.md`)) return `${directory}/index.md`;
    return preferredAlias(artifactAliasMap, value) ?? preferredAlias(artifactAliasMap, wikiSlug(value));
  }, [artifactAliasMap, artifactPaths]);
  const wikiHandlers = useMemo<WikiLinkHandlers>(() => ({
    exists: (target) => resolveWiki(target) !== null,
    onNavigate: (target) => {
      const path = resolveWiki(target);
      if (!path) return;
      setQuery("");
      selectFile(path);
    },
  }), [resolveWiki, selectFile]);

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

  return (
    <div
      ref={layoutRef}
      data-memory-layout="notebook"
      className={clsx(
        "grid h-full min-h-0",
        recordsOpen
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
          onToggleRecords={() => recordsOpen ? closeRecords() : setRecordsOpen(true)}
        />
      </nav>

      <main data-memory-zone="workspace" aria-label={recordsOpen ? "Raw record" : "Memory note"} className="min-h-0 overflow-hidden">
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
            detail={activeDetail?.path === selectedMeta?.path && activeDetail?.revision === selectedMeta?.revision
              ? activeDetail
              : null}
            loading={loading || indexLoading}
            direction={direction}
            contentNotice={contentNotice}
            contentError={contentError}
            contentLoading={contentLoading}
            wikiHandlers={wikiHandlers}
            onRetry={() => {
              if (selectedMeta) {
                for (const key of detailCache.current.keys()) {
                  if (key.startsWith(`${selectedMeta.path}@`)) detailCache.current.delete(key);
                }
              }
              setContentRefreshKey((key) => key + 1);
            }}
          />
        )}
      </main>

      <aside
        data-memory-zone="inspector"
        aria-label="Memory inspector"
        aria-hidden={!recordsOpen}
        className={clsx("min-h-0 overflow-hidden", recordsOpen && "border-l border-line-soft")}
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
      </aside>
    </div>
  );
}
