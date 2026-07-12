import type { MemoryArtifactDetail } from "@/features/memory/lib/notebookTypes";

function key(path: string, revision: string) {
  return `${path}\u0000${revision}`;
}

export class ArtifactCache {
  private readonly cache: RevisionCache<MemoryArtifactDetail>;

  constructor(limit = 50) {
    this.cache = new RevisionCache(limit);
  }

  get size() { return this.cache.size; }

  get(path: string, revision: string | null | undefined) {
    return revision ? this.cache.get(path, revision) : null;
  }

  set(detail: MemoryArtifactDetail) {
    this.cache.set(detail.path, detail.revision, detail);
  }

  invalidatePath(path: string) {
    this.cache.invalidatePath(path);
  }

  retainRevision(path: string, revision: string | null | undefined) {
    this.cache.retainRevision(path, revision);
  }

  clear() { this.cache.clear(); }
}

export class RevisionCache<T> {
  private readonly entries = new Map<string, T>();

  constructor(private readonly limit = 50) {
    if (!Number.isInteger(limit) || limit < 1) throw new Error("Revision cache limit must be positive");
  }

  get size() { return this.entries.size; }

  get(path: string, revision: string) {
    const cacheKey = key(path, revision);
    const value = this.entries.get(cacheKey) ?? null;
    if (value != null) {
      this.entries.delete(cacheKey);
      this.entries.set(cacheKey, value);
    }
    return value;
  }

  set(path: string, revision: string, value: T) {
    this.invalidatePath(path);
    this.entries.set(key(path, revision), value);
    while (this.entries.size > this.limit) {
      const oldest = this.entries.keys().next().value;
      if (oldest == null) break;
      this.entries.delete(oldest);
    }
  }

  invalidatePath(path: string) {
    const prefix = `${path}\u0000`;
    for (const cacheKey of this.entries.keys()) {
      if (cacheKey.startsWith(prefix)) this.entries.delete(cacheKey);
    }
  }

  retainRevision(path: string, revision: string | null | undefined) {
    const keep = revision ? key(path, revision) : null;
    const prefix = `${path}\u0000`;
    for (const cacheKey of this.entries.keys()) {
      if (cacheKey.startsWith(prefix) && cacheKey !== keep) this.entries.delete(cacheKey);
    }
  }

  clear() { this.entries.clear(); }
}
