import type { AppConfig } from "@/api/core";
import type { WikiPageSummary } from "@/api/wiki";

export interface MemoryPageCacheRead {
  readonly owner: symbol;
  readonly configKey: string;
  readonly generation: number;
}

interface MemoryPageCacheState {
  byPath: Map<string, string>;
  byId: Map<string, WikiPageSummary>;
  generation: number;
  repositoryHead: string | null;
}

function keyForConfig(config: AppConfig): string {
  return `${config.serverUrl}\0${config.apiKey}`;
}

function emptyState(): MemoryPageCacheState {
  return {
    byPath: new Map(),
    byId: new Map(),
    generation: 0,
    repositoryHead: null,
  };
}

function clear(state: MemoryPageCacheState, repositoryHead: string | null): void {
  state.byPath.clear();
  state.byId.clear();
  state.repositoryHead = repositoryHead;
}

function upsert(state: MemoryPageCacheState, page: WikiPageSummary): void {
  const previous = state.byId.get(page.pageId);
  if (previous && previous.path !== page.path) state.byPath.delete(previous.path);

  const displacedId = state.byPath.get(page.path);
  if (displacedId && displacedId !== page.pageId) state.byId.delete(displacedId);

  state.byId.set(page.pageId, page);
  state.byPath.set(page.path, page.pageId);
}

export class MemoryPageCache {
  private readonly owner = Symbol("MemoryPageCache");
  private readonly states = new Map<string, MemoryPageCacheState>();

  beginRead(config: AppConfig): MemoryPageCacheRead {
    const configKey = keyForConfig(config);
    const state = this.state(configKey);
    state.generation += 1;
    return { owner: this.owner, configKey, generation: state.generation };
  }

  getByPath(config: AppConfig, path: string): WikiPageSummary | undefined {
    const state = this.states.get(keyForConfig(config));
    const pageId = state?.byPath.get(path);
    return pageId == null ? undefined : state?.byId.get(pageId);
  }

  getById(config: AppConfig, pageId: string): WikiPageSummary | undefined {
    return this.states.get(keyForConfig(config))?.byId.get(pageId);
  }

  commitSnapshot(
    read: MemoryPageCacheRead,
    repositoryHead: string | null,
    pages: readonly WikiPageSummary[],
    signal?: AbortSignal,
  ): boolean {
    const state = this.currentRead(read, signal);
    if (!state) return false;

    const pageIds = new Set<string>();
    const paths = new Set<string>();
    for (const page of pages) {
      if (page.repositoryHead !== repositoryHead) {
        throw new Error(`Wiki snapshot head mismatch for ${page.pageId}`);
      }
      if (pageIds.has(page.pageId)) throw new Error(`Duplicate wiki page id: ${page.pageId}`);
      if (paths.has(page.path)) throw new Error(`Duplicate wiki page path: ${page.path}`);
      pageIds.add(page.pageId);
      paths.add(page.path);
    }

    clear(state, repositoryHead);
    for (const page of pages) upsert(state, page);
    return true;
  }

  commitPage(read: MemoryPageCacheRead, page: WikiPageSummary, signal?: AbortSignal): boolean {
    const state = this.currentRead(read, signal);
    if (!state) return false;
    if (state.repositoryHead !== page.repositoryHead) clear(state, page.repositoryHead);
    upsert(state, page);
    return true;
  }

  commitMutation(config: AppConfig, page: WikiPageSummary): void {
    const state = this.advance(config);
    clear(state, page.repositoryHead);
    upsert(state, page);
  }

  invalidate(config: AppConfig): void {
    clear(this.advance(config), null);
  }

  private advance(config: AppConfig): MemoryPageCacheState {
    const state = this.state(keyForConfig(config));
    state.generation += 1;
    return state;
  }

  private state(configKey: string): MemoryPageCacheState {
    const existing = this.states.get(configKey);
    if (existing) return existing;
    const created = emptyState();
    this.states.set(configKey, created);
    return created;
  }

  private currentRead(
    read: MemoryPageCacheRead,
    signal?: AbortSignal,
  ): MemoryPageCacheState | null {
    if (read.owner !== this.owner || signal?.aborted === true) return null;
    const state = this.states.get(read.configKey);
    return state?.generation === read.generation ? state : null;
  }
}
