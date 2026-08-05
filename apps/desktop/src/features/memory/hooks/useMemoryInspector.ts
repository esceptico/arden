import { useCallback, useEffect, useRef, useState } from "react";
import type { AppConfig } from "@/api/core";
import { getPageHistory, getPageLinks } from "@/api/memoryArtifacts";
import {
  loadMemoryInspectorPane,
  persistMemoryInspectorPane,
  type MemoryInspectorPane,
} from "@/features/memory/components/MemoryInspector";
import type {
  MemoryArtifactDetail,
  PageEditHistory,
  PageLinks,
} from "@/features/memory/lib/notebookTypes";

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
    // Persistence is optional when storage is unavailable.
  }
}

interface UseMemoryInspectorOptions {
  config: AppConfig;
  selectedPath: string | null;
  activeDetail: MemoryArtifactDetail | null;
  visibleDetail: MemoryArtifactDetail | null;
  contentRefreshKey: number;
}

export function useMemoryInspector({
  config,
  selectedPath,
  activeDetail,
  visibleDetail,
  contentRefreshKey,
}: UseMemoryInspectorOptions) {
  const [open, setOpen] = useState(loadInspectorOpen);
  const [pane, setPane] = useState<MemoryInspectorPane>(loadMemoryInspectorPane);
  const [pageLinks, setPageLinks] = useState<PageLinks | null>(null);
  const [pageHistory, setPageHistory] = useState<PageEditHistory | null>(null);
  const [pageHistoryPath, setPageHistoryPath] = useState<string | null>(null);
  const [linksLoading, setLinksLoading] = useState(false);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [linkError, setLinkError] = useState<string | null>(null);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [linksRefreshKey, setLinksRefreshKey] = useState(0);
  const linksRequestId = useRef(0);
  const historyRequestId = useRef(0);
  const retainedDetail = useRef<MemoryArtifactDetail | null>(null);

  const setVisible = useCallback((visible: boolean) => {
    persistInspectorOpen(visible);
    setOpen(visible);
  }, []);

  const openPane = useCallback((nextPane: MemoryInspectorPane) => {
    persistMemoryInspectorPane(nextPane);
    setPane(nextPane);
    setVisible(true);
  }, [setVisible]);

  const retryLinks = useCallback(() => {
    setLinksRefreshKey((key) => key + 1);
  }, []);

  useEffect(() => () => {
    linksRequestId.current += 1;
    historyRequestId.current += 1;
  }, []);

  const shouldLoadLinks = open || (
    activeDetail?.path === selectedPath
    && activeDetail.content.includes("[[")
  );
  useEffect(() => {
    const controller = new AbortController();
    const requestId = ++linksRequestId.current;
    setPageLinks(null);
    setLinkError(null);
    if (!selectedPath || !shouldLoadLinks) {
      setLinksLoading(false);
      return;
    }
    setLinksLoading(true);
    getPageLinks(config, { path: selectedPath, limit: 100, offset: 0 }, { signal: controller.signal })
      .then((links) => {
        if (!controller.signal.aborted && linksRequestId.current === requestId) setPageLinks(links);
      })
      .catch((reason) => {
        if (controller.signal.aborted || linksRequestId.current !== requestId) return;
        setLinkError(reason instanceof Error ? reason.message : String(reason));
      })
      .finally(() => {
        if (!controller.signal.aborted && linksRequestId.current === requestId) setLinksLoading(false);
      });
    return () => controller.abort();
  }, [config, contentRefreshKey, linksRefreshKey, selectedPath, shouldLoadLinks]);

  useEffect(() => {
    const controller = new AbortController();
    const requestId = ++historyRequestId.current;
    setPageHistory(null);
    setPageHistoryPath(null);
    setHistoryError(null);
    if (!selectedPath || !open) {
      setHistoryLoading(false);
      return;
    }
    setHistoryLoading(true);
    getPageHistory(config, { path: selectedPath, limit: 100 }, { signal: controller.signal })
      .then((history) => {
        if (controller.signal.aborted || historyRequestId.current !== requestId) return;
        setPageHistory(history);
        setPageHistoryPath(selectedPath);
      })
      .catch((reason) => {
        if (controller.signal.aborted || historyRequestId.current !== requestId) return;
        setHistoryError(reason instanceof Error ? reason.message : String(reason));
      })
      .finally(() => {
        if (!controller.signal.aborted && historyRequestId.current === requestId) setHistoryLoading(false);
      });
    return () => controller.abort();
  }, [config, contentRefreshKey, open, selectedPath]);

  if (visibleDetail) retainedDetail.current = visibleDetail;
  const detail = visibleDetail ?? (selectedPath ? retainedDetail.current : null);

  const beginExternalRefresh = useCallback(() => {
    linksRequestId.current += 1;
    historyRequestId.current += 1;
  }, []);

  const acceptExternalRefresh = useCallback((
    path: string,
    history: PageEditHistory,
    links: PageLinks,
  ) => {
    setPageHistory(history);
    setPageHistoryPath(path);
    setHistoryError(null);
    setHistoryLoading(false);
    setPageLinks(links);
    setLinkError(null);
    setLinksLoading(false);
  }, []);

  return {
    open,
    pane,
    detail,
    detailIsCurrent: detail?.path === selectedPath,
    links: pageLinks?.path === selectedPath ? pageLinks : null,
    history: pageHistoryPath === selectedPath ? pageHistory : null,
    linksLoading,
    historyLoading,
    linkError,
    historyError,
    setVisible,
    openPane,
    retryLinks,
    beginExternalRefresh,
    acceptExternalRefresh,
  };
}
