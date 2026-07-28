import type { MemoryArtifactDetail } from "@/features/memory/lib/notebookTypes";

function key(path: string, revision: string) {
  return `${path}\u0000${revision}`;
}

export class ArtifactCache {
  private readonly entries = new Map<string, MemoryArtifactDetail>();
  private readonly aliases = new Map<string, string>();

  constructor(private readonly limit = 50) {
    if (!Number.isInteger(limit) || limit < 1) throw new Error("Artifact cache limit must be positive");
  }

  get size() { return this.entries.size; }
  get aliasSize() { return this.aliases.size; }

  get(path: string, revision: string | null | undefined) {
    if (!revision) return null;
    const requestedKey = key(path, revision);
    const cacheKey = this.aliases.get(requestedKey) ?? requestedKey;
    const value = this.entries.get(cacheKey) ?? null;
    if (value == null) {
      this.aliases.delete(requestedKey);
      return null;
    }
    this.touch(this.entries, cacheKey, value);
    if (this.aliases.has(requestedKey)) this.touch(this.aliases, requestedKey, cacheKey);
    return value;
  }

  set(detail: MemoryArtifactDetail, requestedRevision = detail.revision) {
    this.invalidatePath(detail.path);
    const actualKey = key(detail.path, detail.revision);
    this.entries.set(actualKey, detail);
    if (requestedRevision !== detail.revision) {
      this.aliases.set(key(detail.path, requestedRevision), actualKey);
    }
    this.evict();
  }

  invalidatePath(path: string) {
    this.clearAliases(path);
    const prefix = `${path}\u0000`;
    for (const cacheKey of this.entries.keys()) {
      if (cacheKey.startsWith(prefix)) this.deleteEntry(cacheKey);
    }
  }

  retainRevision(path: string, revision: string | null | undefined) {
    const keep = revision ? key(path, revision) : null;
    const prefix = `${path}\u0000`;
    for (const cacheKey of this.entries.keys()) {
      if (cacheKey.startsWith(prefix) && cacheKey !== keep) this.deleteEntry(cacheKey);
    }
    for (const [aliasKey, targetKey] of this.aliases) {
      if (aliasKey.startsWith(prefix) && targetKey !== keep) this.aliases.delete(aliasKey);
    }
  }

  clear() {
    this.aliases.clear();
    this.entries.clear();
  }

  private clearAliases(path: string) {
    const prefix = `${path}\u0000`;
    for (const cacheKey of this.aliases.keys()) {
      if (cacheKey.startsWith(prefix)) this.aliases.delete(cacheKey);
    }
  }

  private deleteEntry(cacheKey: string) {
    this.entries.delete(cacheKey);
    for (const [aliasKey, targetKey] of this.aliases) {
      if (targetKey === cacheKey) this.aliases.delete(aliasKey);
    }
  }

  private evict() {
    while (this.entries.size > this.limit) {
      const oldest = this.entries.keys().next().value;
      if (oldest == null) break;
      this.deleteEntry(oldest);
    }
    while (this.aliases.size > this.limit) {
      const oldest = this.aliases.keys().next().value;
      if (oldest == null) break;
      this.aliases.delete(oldest);
    }
  }

  private touch<T>(entries: Map<string, T>, cacheKey: string, value: T) {
    entries.delete(cacheKey);
    entries.set(cacheKey, value);
  }
}
