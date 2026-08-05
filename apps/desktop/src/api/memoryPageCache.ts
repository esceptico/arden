import type { AppConfig } from "@/api/core";
import type { WikiPageSummary } from "@/api/wiki";

export interface MemoryPageCacheRead {
  readonly owner: symbol;
  readonly configKey: string;
  readonly sequence: number;
}

interface PageEntry {
  page: WikiPageSummary;
  sequence: number;
}

interface MemoryPageCacheState {
  byPath: Map<string, string>;
  byId: Map<string, PageEntry>;
  idTombstones: Map<string, number>;
  pathTombstones: Map<string, number>;
  nextSequence: number;
  lastSnapshotSequence: number;
  minimumReadSequence: number;
}

function keyForConfig(config: AppConfig): string {
  return `${config.serverUrl}\0${config.apiKey}`;
}

function emptyState(): MemoryPageCacheState {
  return {
    byPath: new Map(),
    byId: new Map(),
    idTombstones: new Map(),
    pathTombstones: new Map(),
    nextSequence: 0,
    lastSnapshotSequence: 0,
    minimumReadSequence: 0,
  };
}

function setTombstone(tombstones: Map<string, number>, key: string, sequence: number): void {
  if ((tombstones.get(key) ?? -1) <= sequence) tombstones.set(key, sequence);
}

function pruneTombstones(tombstones: Map<string, number>, snapshotSequence: number): void {
  for (const [key, sequence] of tombstones) {
    if (sequence < snapshotSequence) tombstones.delete(key);
  }
}

function removeEntry(state: MemoryPageCacheState, pageId: string, sequence: number): void {
  const entry = state.byId.get(pageId);
  if (entry && entry.sequence <= sequence) {
    state.byId.delete(pageId);
    if (state.byPath.get(entry.page.path) === pageId) state.byPath.delete(entry.page.path);
    setTombstone(state.pathTombstones, entry.page.path, sequence);
  }
  setTombstone(state.idTombstones, pageId, sequence);
}

function upsert(state: MemoryPageCacheState, page: WikiPageSummary, sequence: number): boolean {
  if ((state.idTombstones.get(page.pageId) ?? -1) > sequence) return false;
  if ((state.pathTombstones.get(page.path) ?? -1) > sequence) return false;

  const previousForId = state.byId.get(page.pageId);
  if (previousForId && previousForId.sequence > sequence) return false;
  const displacedId = state.byPath.get(page.path);
  const displaced = displacedId == null ? null : state.byId.get(displacedId);
  if (displaced && displaced.page.pageId !== page.pageId && displaced.sequence > sequence) return false;

  if (previousForId && previousForId.page.path !== page.path) {
    if (state.byPath.get(previousForId.page.path) === page.pageId) {
      state.byPath.delete(previousForId.page.path);
    }
    setTombstone(state.pathTombstones, previousForId.page.path, sequence);
  }
  if (displaced && displaced.page.pageId !== page.pageId) {
    removeEntry(state, displaced.page.pageId, sequence);
  }

  state.byId.set(page.pageId, { page, sequence });
  state.byPath.set(page.path, page.pageId);
  if ((state.idTombstones.get(page.pageId) ?? -1) <= sequence) {
    state.idTombstones.delete(page.pageId);
  }
  if ((state.pathTombstones.get(page.path) ?? -1) <= sequence) {
    state.pathTombstones.delete(page.path);
  }
  return true;
}

export class MemoryPageCache {
  private readonly owner = Symbol("MemoryPageCache");
  private readonly states = new Map<string, MemoryPageCacheState>();

  beginRead(config: AppConfig): MemoryPageCacheRead {
    const configKey = keyForConfig(config);
    const state = this.state(configKey);
    state.nextSequence += 1;
    return { owner: this.owner, configKey, sequence: state.nextSequence };
  }

  getByPath(config: AppConfig, path: string): WikiPageSummary | undefined {
    const state = this.states.get(keyForConfig(config));
    const pageId = state?.byPath.get(path);
    return pageId == null ? undefined : state?.byId.get(pageId)?.page;
  }

  getById(config: AppConfig, pageId: string): WikiPageSummary | undefined {
    return this.states.get(keyForConfig(config))?.byId.get(pageId)?.page;
  }

  commitSnapshot(
    read: MemoryPageCacheRead,
    pages: readonly WikiPageSummary[],
    signal?: AbortSignal,
  ): boolean {
    const state = this.readState(read, signal);
    if (!state || read.sequence < state.lastSnapshotSequence) return false;

    const incomingIds = new Set(pages.map((page) => page.pageId));
    for (const [pageId, entry] of [...state.byId]) {
      if (!incomingIds.has(pageId) && entry.sequence <= read.sequence) {
        removeEntry(state, pageId, read.sequence);
      }
    }
    for (const page of pages) upsert(state, page, read.sequence);
    state.lastSnapshotSequence = read.sequence;
    pruneTombstones(state.idTombstones, read.sequence);
    pruneTombstones(state.pathTombstones, read.sequence);
    return true;
  }

  commitPage(
    read: MemoryPageCacheRead,
    page: WikiPageSummary,
    signal?: AbortSignal,
  ): boolean {
    const state = this.readState(read, signal);
    if (!state || read.sequence !== state.nextSequence) return false;
    return upsert(state, page, read.sequence);
  }

  commitMutation(config: AppConfig, page: WikiPageSummary): void {
    const state = this.state(keyForConfig(config));
    state.nextSequence += 1;
    upsert(state, page, state.nextSequence);
  }

  commitRemoval(config: AppConfig, page: WikiPageSummary): void {
    const state = this.state(keyForConfig(config));
    state.nextSequence += 1;
    const sequence = state.nextSequence;
    removeEntry(state, page.pageId, sequence);
    const suppliedPathOwner = state.byPath.get(page.path);
    if (suppliedPathOwner == null || suppliedPathOwner === page.pageId) {
      if (suppliedPathOwner === page.pageId) state.byPath.delete(page.path);
      setTombstone(state.pathTombstones, page.path, sequence);
    }
  }

  invalidate(config: AppConfig): void {
    const state = this.state(keyForConfig(config));
    state.nextSequence += 1;
    const sequence = state.nextSequence;
    state.minimumReadSequence = sequence;
    for (const pageId of [...state.byId.keys()]) removeEntry(state, pageId, sequence);
  }

  private state(configKey: string): MemoryPageCacheState {
    const existing = this.states.get(configKey);
    if (existing) return existing;
    const created = emptyState();
    this.states.set(configKey, created);
    return created;
  }

  private readState(
    read: MemoryPageCacheRead,
    signal?: AbortSignal,
  ): MemoryPageCacheState | null {
    if (read.owner !== this.owner || signal?.aborted === true) return null;
    const state = this.states.get(read.configKey);
    if (!state || read.sequence < state.minimumReadSequence) return null;
    return state;
  }
}
