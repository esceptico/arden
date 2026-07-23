import type { HistoryPage } from "@/api/chat";
import type { HistoryPhase } from "@/stores/domains";

export interface SessionViewState {
  currentSessionId: string | null;
  historyPhase: HistoryPhase;
  cachePreviewRestoredFor: string | null;
  canonicalHistoryRequired: boolean;
  historyLoadedFor: string | null;
  historyReloadingFor: string | null;
  historyHasMoreBefore: boolean;
  historyHasMoreAfter: boolean;
  historyBeforeCursor: string | null;
  historyAfterCursor: string | null;
  historyLoadingBefore: boolean;
  historyLoadingAfter: boolean;
}

export type HistoryPageMergeMode = "replace" | "prepend" | "append";

const replaceHistoryLoadVersions = new Map<string, number>();

export function createInitialSessionViewState(): SessionViewState {
  return {
    currentSessionId: null,
    historyPhase: "idle",
    cachePreviewRestoredFor: null,
    canonicalHistoryRequired: false,
    historyLoadedFor: null,
    historyReloadingFor: null,
    historyHasMoreBefore: false,
    historyHasMoreAfter: false,
    historyBeforeCursor: null,
    historyAfterCursor: null,
    historyLoadingBefore: false,
    historyLoadingAfter: false,
  };
}

export function nextHistoryReplaceRequestVersion(sessionId: string): number {
  const version = (replaceHistoryLoadVersions.get(sessionId) ?? 0) + 1;
  replaceHistoryLoadVersions.set(sessionId, version);
  return version;
}

export function isCurrentHistoryReplaceRequestVersion(
  sessionId: string,
  version: number,
): boolean {
  return replaceHistoryLoadVersions.get(sessionId) === version;
}

export function reduceSessionSelected(
  state: SessionViewState,
  sessionId: string | null,
): SessionViewState {
  if (sessionId === null) return createInitialSessionViewState();
  if (state.currentSessionId === sessionId) return state;

  return {
    ...createInitialSessionViewState(),
    currentSessionId: sessionId,
    canonicalHistoryRequired: true,
  };
}

export function reduceCachePreviewRestored(
  state: SessionViewState,
  sessionId: string,
): SessionViewState {
  const hasLoadedHistory = state.historyLoadedFor === sessionId;
  return {
    ...state,
    currentSessionId: sessionId,
    historyPhase: "cached-preview",
    cachePreviewRestoredFor: sessionId,
    canonicalHistoryRequired: true,
    historyLoadedFor: hasLoadedHistory ? sessionId : null,
    historyReloadingFor: null,
    historyHasMoreBefore: hasLoadedHistory ? state.historyHasMoreBefore : false,
    historyHasMoreAfter: hasLoadedHistory ? state.historyHasMoreAfter : false,
    historyBeforeCursor: hasLoadedHistory ? state.historyBeforeCursor : null,
    historyAfterCursor: hasLoadedHistory ? state.historyAfterCursor : null,
    historyLoadingBefore: false,
    historyLoadingAfter: false,
  };
}

export function reduceHistoryLoadStarted(
  state: SessionViewState,
  sessionId: string,
): SessionViewState {
  return {
    ...state,
    currentSessionId: sessionId,
    historyPhase: "loading-history",
    canonicalHistoryRequired: true,
    historyReloadingFor: sessionId,
    historyLoadingBefore: false,
    historyLoadingAfter: false,
  };
}

export function reduceHistoryLoadSucceeded(
  state: SessionViewState,
  sessionId: string,
  page?: HistoryPage,
  mode: HistoryPageMergeMode = "replace",
): SessionViewState {
  return {
    ...state,
    currentSessionId: sessionId,
    historyPhase: "idle",
    cachePreviewRestoredFor: null,
    canonicalHistoryRequired: false,
    historyLoadedFor: sessionId,
    historyReloadingFor: null,
    historyHasMoreBefore: historyHasMoreBeforeAfterSuccess(state, page, mode),
    historyHasMoreAfter: historyHasMoreAfterAfterSuccess(state, page, mode),
    historyBeforeCursor: historyBeforeCursorAfterSuccess(state, page, mode),
    historyAfterCursor: historyAfterCursorAfterSuccess(state, page, mode),
    historyLoadingBefore: false,
    historyLoadingAfter: false,
  };
}

export function reduceHistoryLoadFailed(
  state: SessionViewState,
  sessionId: string,
): SessionViewState {
  if (state.currentSessionId !== sessionId && state.historyReloadingFor !== sessionId) {
    return state;
  }

  return {
    ...state,
    historyPhase:
      state.cachePreviewRestoredFor === sessionId ? "cached-preview" : "idle",
    canonicalHistoryRequired: true,
    historyReloadingFor: null,
    historyLoadingBefore: false,
    historyLoadingAfter: false,
  };
}

export function reduceHistoryPageLoading(
  state: SessionViewState,
  direction: "before" | "after",
  loading: boolean,
): SessionViewState {
  return direction === "before"
    ? { ...state, historyLoadingBefore: loading }
    : { ...state, historyLoadingAfter: loading };
}

function historyHasMoreBeforeAfterSuccess(
  state: SessionViewState,
  page: HistoryPage | undefined,
  mode: HistoryPageMergeMode,
): boolean {
  if (mode === "append") return state.historyHasMoreBefore || Boolean(page?.has_more_before);
  return page?.has_more_before ?? false;
}

function historyHasMoreAfterAfterSuccess(
  state: SessionViewState,
  page: HistoryPage | undefined,
  mode: HistoryPageMergeMode,
): boolean {
  if (mode === "prepend") return state.historyHasMoreAfter || Boolean(page?.has_more_after);
  return page?.has_more_after ?? false;
}

function historyBeforeCursorAfterSuccess(
  state: SessionViewState,
  page: HistoryPage | undefined,
  mode: HistoryPageMergeMode,
): string | null {
  if (mode === "append") return state.historyBeforeCursor ?? page?.before ?? null;
  return page?.before ?? state.historyBeforeCursor ?? null;
}

function historyAfterCursorAfterSuccess(
  state: SessionViewState,
  page: HistoryPage | undefined,
  mode: HistoryPageMergeMode,
): string | null {
  if (mode === "prepend") return state.historyAfterCursor ?? page?.after ?? null;
  return page?.after ?? state.historyAfterCursor ?? null;
}
