import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type Dispatch,
  type RefObject,
  type SetStateAction,
} from "react";
import { ApiError, type AppConfig } from "@/api/core";
import {
  archiveMemoryArtifact,
  getPageHistory,
  getPageLinks,
  readMemoryArtifactDetail,
  readMemoryArtifactTimeline,
  rebuildMemoryArtifactSummaries,
  restorePageMaintenanceChange,
} from "@/api/memoryArtifacts";
import { canRestorePageEdit } from "@/features/memory/components/MemoryDiffOverlay";
import { ArtifactCache } from "@/features/memory/lib/artifactCache";
import { clearDraft } from "@/features/memory/lib/draftStore";
import type { MemoryConflict } from "@/features/memory/components/MemoryEditReview";
import type {
  MemoryArtifactDetail,
  MemoryArtifactSummary,
  PageEditEvent,
  PageEditHistory,
  PageLinks,
} from "@/features/memory/lib/notebookTypes";
import { isMissingArtifactError } from "@/features/memory/lib/wikiResolution";

interface SummaryRequest {
  epoch: number;
  controller: AbortController;
}

interface UseMemoryDocumentOptions {
  config: AppConfig;
  selectedArtifact: MemoryArtifactSummary | null;
  navigationVersion: number;
  activeDetail: MemoryArtifactDetail | null;
  setActiveDetail: Dispatch<SetStateAction<MemoryArtifactDetail | null>>;
  mutationPendingRef: RefObject<boolean>;
  diffEvent: PageEditEvent | null;
  onRevision: (path: string, revision: string) => void;
  onMissing: (path: string) => void;
  onRestoreArtifact: (artifact: MemoryArtifactDetail) => void;
  onArchived: (path: string) => void;
  refreshSummaries: () => Promise<boolean>;
  beginSummaryRequest: () => SummaryRequest;
  isCurrentSummaryRequest: (request: SummaryRequest) => boolean;
  acceptSummaries: (artifacts: MemoryArtifactSummary[], directories?: string[]) => void;
  closeDiff: () => void;
  isSelected: (path: string) => boolean;
}

interface InspectorRefresh {
  begin: () => void;
  accept: (path: string, history: PageEditHistory, links: PageLinks) => void;
}

function revisionConflict(reason: unknown): MemoryConflict | null {
  if (!(reason instanceof ApiError) || reason.status !== 409 || !reason.data || typeof reason.data !== "object") return null;
  const detail = (reason.data as { detail?: unknown }).detail;
  if (!detail || typeof detail !== "object") return null;
  const value = detail as { error?: unknown; current_revision?: unknown; current_content?: unknown };
  if (value.error !== "page_revision_conflict" || typeof value.current_revision !== "string" || typeof value.current_content !== "string") return null;
  return { currentRevision: value.current_revision, currentContent: value.current_content };
}

export function useMemoryDocument({
  config,
  selectedArtifact,
  navigationVersion,
  activeDetail,
  setActiveDetail,
  mutationPendingRef,
  diffEvent,
  onRevision,
  onMissing,
  onRestoreArtifact,
  onArchived,
  refreshSummaries,
  beginSummaryRequest,
  isCurrentSummaryRequest,
  acceptSummaries,
  closeDiff,
  isSelected,
}: UseMemoryDocumentOptions) {
  const [contentLoading, setContentLoading] = useState(false);
  const [contentError, setContentError] = useState<string | null>(null);
  const [contentNotice, setContentNotice] = useState<string | null>(null);
  const [contentRefreshKey, setContentRefreshKey] = useState(0);
  const [timelineLoading, setTimelineLoading] = useState(false);
  const [timelineError, setTimelineError] = useState<string | null>(null);
  const [timelineRefreshKey, setTimelineRefreshKey] = useState(0);
  const [restorePending, setRestorePending] = useState(false);
  const [restoreError, setRestoreError] = useState<string | null>(null);
  const [archivePending, setArchivePending] = useState(false);
  const cacheRef = useRef(new ArtifactCache());
  const restoreHydration = useRef<{ path: string; revision: string } | null>(null);
  const restoreGeneration = useRef(0);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      restoreGeneration.current += 1;
    };
  }, []);

  useEffect(() => {
    if (navigationVersion > 0) setContentNotice(null);
  }, [navigationVersion]);

  useEffect(() => {
    const controller = new AbortController();
    if (!selectedArtifact) {
      setActiveDetail(null);
      setContentLoading(false);
      setTimelineLoading(false);
      setTimelineError(null);
      return;
    }
    if (
      restoreHydration.current?.path === selectedArtifact.path
      && restoreHydration.current.revision === selectedArtifact.revision
    ) {
      setContentLoading(false);
      return;
    }
    const cached = cacheRef.current.get(selectedArtifact.path, selectedArtifact.revision);
    if (cached) {
      setActiveDetail(cached);
      setContentError(null);
      setContentLoading(false);
      setTimelineLoading(false);
      setTimelineError(null);
      return;
    }
    setActiveDetail((current) => current?.path === selectedArtifact.path ? current : null);
    setContentLoading(true);
    setContentError(null);
    setTimelineLoading(false);
    setTimelineError(null);
    readMemoryArtifactDetail(config, selectedArtifact.path, { signal: controller.signal })
      .then((response) => {
        if (controller.signal.aborted || !mountedRef.current) return;
        const detail = response.artifact;
        setActiveDetail(detail);
        if (detail.revision !== selectedArtifact.revision) onRevision(detail.path, detail.revision);
        if (response.citations.length === 0) {
          cacheRef.current.set(detail, selectedArtifact.revision ?? undefined);
          return;
        }
        setTimelineLoading(true);
        void readMemoryArtifactTimeline(config, response.citations, { signal: controller.signal })
          .then((timeline) => {
            if (controller.signal.aborted || !mountedRef.current) return;
            const hydrated = { ...detail, timeline };
            cacheRef.current.set(hydrated, selectedArtifact.revision ?? undefined);
            setActiveDetail((current) => current?.path === detail.path && current.revision === detail.revision ? hydrated : current);
            setTimelineError(null);
          })
          .catch((reason) => {
            if (controller.signal.aborted || !mountedRef.current) return;
            setTimelineError(reason instanceof Error ? reason.message : String(reason));
          })
          .finally(() => {
            if (!controller.signal.aborted && mountedRef.current) setTimelineLoading(false);
          });
      })
      .catch((reason) => {
        if (controller.signal.aborted || !mountedRef.current) return;
        if (isMissingArtifactError(reason)) {
          onMissing(selectedArtifact.path);
          setActiveDetail(null);
          setContentNotice("That memory note changed or disappeared; refreshed the index.");
          void refreshSummaries();
          return;
        }
        setContentError(reason instanceof Error ? reason.message : String(reason));
      })
      .finally(() => {
        if (!controller.signal.aborted && mountedRef.current) setContentLoading(false);
      });
    return () => controller.abort();
  }, [
    config,
    contentRefreshKey,
    onMissing,
    onRevision,
    refreshSummaries,
    selectedArtifact?.path,
    selectedArtifact?.revision,
    setActiveDetail,
    timelineRefreshKey,
  ]);

  const visibleDetail = activeDetail != null
    && selectedArtifact != null
    && activeDetail.path === selectedArtifact.path
    && activeDetail.revision === selectedArtifact.revision
    ? activeDetail
    : null;

  const invalidate = useCallback((path: string) => {
    cacheRef.current.invalidatePath(path);
  }, []);

  const refresh = useCallback(() => {
    setContentRefreshKey((key) => key + 1);
  }, []);

  const retry = useCallback((path: string) => {
    cacheRef.current.invalidatePath(path);
    setContentRefreshKey((key) => key + 1);
  }, []);

  const handleSaved = useCallback((path: string, revision: string) => {
    cacheRef.current.invalidatePath(path);
    onRevision(path, revision);
    setContentNotice("Saved memory note.");
    setContentRefreshKey((key) => key + 1);
    void refreshSummaries();
  }, [onRevision, refreshSummaries]);

  const handleRebased = useCallback((path: string, revision: string) => {
    cacheRef.current.invalidatePath(path);
    setActiveDetail(null);
    onRevision(path, revision);
    setContentRefreshKey((key) => key + 1);
  }, [onRevision, setActiveDetail]);

  const restore = useCallback(async (inspector: InspectorRefresh) => {
    const event = diffEvent;
    const page = visibleDetail;
    if (
      !event
      || !page
      || page.path !== event.path
      || page.repositoryHead == null
      || !canRestorePageEdit(event, page.revision)
      || mutationPendingRef.current
    ) return;

    const generation = ++restoreGeneration.current;
    mutationPendingRef.current = true;
    setRestorePending(true);
    setRestoreError(null);
    try {
      const restored = await restorePageMaintenanceChange(config, {
        path: page.path,
        commitId: event.id,
        expectedVersion: page.revision,
        expectedHead: page.repositoryHead,
      });
      if (!mountedRef.current || restoreGeneration.current !== generation) return;

      cacheRef.current.invalidatePath(page.path);
      const needsTimeline = restored.citations.length > 0;
      if (needsTimeline) {
        restoreHydration.current = { path: restored.artifact.path, revision: restored.artifact.revision };
      } else {
        cacheRef.current.set(restored.artifact);
      }
      onRestoreArtifact(restored.artifact);
      setActiveDetail(restored.artifact);
      setContentError(null);
      setContentNotice("Restored Maintenance edit.");
      setTimelineError(null);
      setTimelineLoading(needsTimeline);
      closeDiff();

      const refreshRequest = beginSummaryRequest();
      inspector.begin();
      try {
        const [summaries, history, links, timeline] = await Promise.all([
          rebuildMemoryArtifactSummaries(config, { signal: refreshRequest.controller.signal }),
          getPageHistory(config, { path: page.path, limit: 100 }, { signal: refreshRequest.controller.signal }),
          getPageLinks(config, { path: page.path, limit: 100, offset: 0 }, { signal: refreshRequest.controller.signal }),
          readMemoryArtifactTimeline(config, restored.citations, { signal: refreshRequest.controller.signal }),
        ]);
        if (
          refreshRequest.controller.signal.aborted
          || !isCurrentSummaryRequest(refreshRequest)
          || restoreGeneration.current !== generation
        ) return;

        acceptSummaries(summaries.artifacts, summaries.directories);
        inspector.accept(page.path, history, links);
        const hydrated = { ...restored.artifact, timeline };
        cacheRef.current.set(hydrated);
        if (isSelected(page.path)) {
          setActiveDetail(hydrated);
          setTimelineError(null);
        }
      } catch (reason) {
        if (
          refreshRequest.controller.signal.aborted
          || !isCurrentSummaryRequest(refreshRequest)
          || restoreGeneration.current !== generation
        ) return;
        const detail = reason instanceof Error ? reason.message : String(reason);
        setContentNotice(`Restored Maintenance edit. Related page data refresh failed: ${detail}`);
        setContentRefreshKey((key) => key + 1);
        void refreshSummaries();
      } finally {
        if (
          restoreHydration.current?.path === restored.artifact.path
          && restoreHydration.current.revision === restored.artifact.revision
        ) {
          restoreHydration.current = null;
        }
        if (isSelected(page.path)) setTimelineLoading(false);
      }
    } catch (reason) {
      if (!mountedRef.current || restoreGeneration.current !== generation) return;
      setRestoreError(
        reason instanceof ApiError && reason.status === 409
          ? "A newer edit prevents automatic restoration."
          : reason instanceof Error ? reason.message : String(reason),
      );
    } finally {
      if (restoreGeneration.current === generation) {
        mutationPendingRef.current = false;
        if (mountedRef.current) setRestorePending(false);
      }
    }
  }, [
    acceptSummaries,
    beginSummaryRequest,
    closeDiff,
    config,
    diffEvent,
    isCurrentSummaryRequest,
    isSelected,
    mutationPendingRef,
    onRestoreArtifact,
    refreshSummaries,
    setActiveDetail,
    visibleDetail,
  ]);

  const archive = useCallback(async (page: MemoryArtifactDetail) => {
    if (archivePending || restorePending || mutationPendingRef.current) return;
    setArchivePending(true);
    setContentError(null);
    try {
      await archiveMemoryArtifact(config, {
        path: page.path,
        baseRevision: page.revision,
      });
      clearDraft(page.path, page.revision);
      cacheRef.current.invalidatePath(page.path);
      onArchived(page.path);
      setActiveDetail((current) => current?.path === page.path ? null : current);
      setContentNotice(`Archived ${page.title}.`);
      await refreshSummaries();
    } catch (reason) {
      if (revisionConflict(reason)) {
        setContentError("This page changed. Reload it before archiving.");
        cacheRef.current.invalidatePath(page.path);
        setContentRefreshKey((key) => key + 1);
      } else {
        setContentError(reason instanceof Error ? reason.message : String(reason));
      }
    } finally {
      setArchivePending(false);
    }
  }, [
    archivePending,
    config,
    mutationPendingRef,
    onArchived,
    refreshSummaries,
    restorePending,
    setActiveDetail,
  ]);

  const loadPreviewDetail = useCallback(async (path: string, signal: AbortSignal) => {
    const response = await readMemoryArtifactDetail(config, path, { signal });
    return response.artifact;
  }, [config]);

  const clearRestoreError = useCallback(() => setRestoreError(null), []);
  const clearCache = useCallback(() => cacheRef.current.clear(), []);
  const retryTimeline = useCallback(() => setTimelineRefreshKey((key) => key + 1), []);
  const clearNotice = useCallback(() => setContentNotice(null), []);

  return {
    visibleDetail,
    contentLoading,
    contentError,
    contentNotice,
    contentRefreshKey,
    timelineLoading,
    timelineError,
    restorePending,
    restoreError,
    pageMutationPending: archivePending || restorePending,
    cache: cacheRef.current,
    invalidate,
    clearCache,
    refresh,
    retry,
    retryTimeline,
    clearNotice,
    handleSaved,
    handleRebased,
    restore,
    archive,
    clearRestoreError,
    loadPreviewDetail,
  };
}
