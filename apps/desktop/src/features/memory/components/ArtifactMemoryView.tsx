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
import {
  buildNotebookSections,
  NotebookRail,
  isNotebookArtifact,
  isNotebookResource,
} from "@/features/memory/components/NotebookRail";
import { FileDetailPane } from "@/features/memory/components/FileDetailPane";
import { RecordDetailPane } from "@/features/memory/components/RecordDetailPane";
import { RecordListPane } from "@/features/memory/components/RecordListPane";
import { addAlias, isMissingArtifactError, preferredAlias } from "@/features/memory/lib/wikiResolution";
import type { MemoryArtifactDetail, MemoryArtifactSummary } from "@/features/memory/lib/notebookTypes";

const RECORD_PAGE_SIZE = 100;

export function ArtifactMemoryView({ config }: { config: AppConfig }) {
  const reduce = useReducedMotion();
  const [artifacts, setArtifacts] = useState<MemoryArtifactSummary[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [activeDetail, setActiveDetail] = useState<MemoryArtifactDetail | null>(null);
  const [contentLoading, setContentLoading] = useState(false);
  const [contentError, setContentError] = useState<string | null>(null);
  const [contentNotice, setContentNotice] = useState<string | null>(null);
  const [contentRefreshKey, setContentRefreshKey] = useState(0);
  const [query, setQuery] = useState("");
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
  const loadRequestId = useRef(0);
  const recordsRequestId = useRef(0);
  const detailCache = useRef(new Map<string, MemoryArtifactDetail>());

  const applySummaries = useCallback((next: MemoryArtifactSummary[]) => {
    setArtifacts(next);
    const visible = next.filter(isNotebookResource);
    const hierarchy = buildNotebookSections(next);
    const defaultPath = hierarchy.sections[0]?.artifacts[0]?.path ?? hierarchy.files[0]?.path ?? null;
    setSelected((previous) => previous && visible.some((artifact) => artifact.path === previous)
      ? previous
      : defaultPath);
    return next;
  }, []);

  const load = useCallback(() => {
    const requestId = ++loadRequestId.current;
    setLoading(true);
    setError(null);
    return listMemoryArtifactSummaries(config)
      .then((response) => requestId === loadRequestId.current ? applySummaries(response.artifacts) : [])
      .catch((reason) => {
        if (requestId !== loadRequestId.current) return [];
        setError(reason instanceof Error ? reason.message : String(reason));
        return [];
      })
      .finally(() => {
        if (requestId === loadRequestId.current) setLoading(false);
      });
  }, [applySummaries, config]);

  useEffect(() => {
    void load();
  }, [load]);

  const memoryVaultVersion = useStore((state) => state.memoryVaultVersion);
  useEffect(() => {
    if (memoryVaultVersion === 0) return;
    void load();
    if (recordsOpen) setRecordsRefreshKey((key) => key + 1);
  }, [load, memoryVaultVersion, recordsOpen]);

  const visibleArtifacts = useMemo(() => artifacts.filter(isNotebookArtifact), [artifacts]);
  const navigableArtifacts = useMemo(() => artifacts.filter(isNotebookResource), [artifacts]);
  const selectedMeta = navigableArtifacts.find((artifact) => artifact.path === selected) ?? visibleArtifacts[0] ?? null;
  const selectFile = useCallback((path: string) => {
    const order = visibleArtifacts.map((artifact) => artifact.path);
    const from = order.indexOf(selectedMeta?.path ?? selected ?? "");
    const to = order.indexOf(path);
    if (from !== -1 && to !== -1 && from !== to) setDirection(to > from ? 1 : -1);
    setContentNotice(null);
    setSelected(path);
    setRecordsOpen(false);
  }, [selected, selectedMeta?.path, visibleArtifacts]);

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
    setRebuilding(true);
    setContentNotice(null);
    rebuildMemoryArtifactSummaries(config)
      .then((response) => applySummaries(response.artifacts))
      .catch((reason) => setError(reason instanceof Error ? reason.message : String(reason)))
      .finally(() => setRebuilding(false));
  };

  return (
    <div
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
          artifacts={artifacts}
          selectedPath={selectedMeta?.path ?? null}
          query={query}
          loading={loading}
          error={error}
          rebuilding={rebuilding}
          recordsOpen={recordsOpen}
          onQueryChange={setQuery}
          onSelect={selectFile}
          onRetry={() => void load()}
          onRebuild={rebuild}
          onToggleRecords={() => setRecordsOpen((open) => !open)}
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
            loading={loading}
            direction={direction}
            contentNotice={contentNotice}
            contentError={contentError}
            contentLoading={contentLoading}
            wikiHandlers={wikiHandlers}
            onRetry={() => setContentRefreshKey((key) => key + 1)}
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
                <h2 className="text-sm font-semibold text-ink">Raw records</h2>
                <p className="text-2xs text-muted">Diagnostic view</p>
              </div>
              <button
                type="button"
                onClick={() => setRecordsOpen(false)}
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
