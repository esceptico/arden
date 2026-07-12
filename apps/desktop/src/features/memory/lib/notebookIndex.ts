import type { MemoryArtifactSummary } from "@/features/memory/lib/notebookTypes";

const INDEX_START = "<!-- ntrp:index:start -->";
const INDEX_END = "<!-- ntrp:index:end -->";
const MANAGED_ID = /^(?<line>- .*) <!-- ntrp:path=(?<path>\S+) -->$/;
const RESERVED_DIRECTORIES = new Set(["raw", ".ntrp", ".index", ".maintenance"]);
const RESERVED_ROOT_FILES = new Set(["README.md", "AGENTS.md", "health.md", "tooling.md"]);

export interface ManagedIndexEntry {
  path: string;
  description: string;
  directory: boolean;
}

export type NotebookRailEntry =
  | {
      kind: "note";
      path: string;
      description: string;
      artifact: MemoryArtifactSummary;
    }
  | {
      kind: "directory";
      path: string;
      title: string;
      description: string;
      children: NotebookRailEntry[];
    };

export interface NotebookRailModel {
  entries: NotebookRailEntry[];
  files: MemoryArtifactSummary[];
}

function pathParts(path: string): string[] {
  return path.split("/").filter(Boolean);
}

function parentDirectory(path: string): string {
  return pathParts(path).slice(0, -1).join("/");
}

export function isNotebookResourcePath(path: string): boolean {
  const parts = pathParts(path);
  if (parts.some((part) => RESERVED_DIRECTORIES.has(part))) return false;
  if (parts[0] === "changelog") return false;
  if (parts.length === 1 && RESERVED_ROOT_FILES.has(parts[0]!)) return false;
  return parts.length > 0;
}

export function isIndexDocumentPath(path: string): boolean {
  if (path === "index.md") return true;
  if (!isNotebookResourcePath(path)) return false;
  const name = pathParts(path).at(-1);
  return name === "README.md" || name === "index.md";
}

export function isNotebookPage(artifact: MemoryArtifactSummary): boolean {
  return isNotebookResourcePath(artifact.path) && !isIndexDocumentPath(artifact.path);
}

export function selectIndexDocuments(artifacts: MemoryArtifactSummary[]): MemoryArtifactSummary[] {
  const selected: MemoryArtifactSummary[] = [];
  const root = artifacts.find((artifact) => artifact.path === "index.md");
  if (root) selected.push(root);

  const nested = artifacts.filter((artifact) => artifact.path !== "index.md" && isIndexDocumentPath(artifact.path));
  const directories = [...new Set(nested.map((artifact) => parentDirectory(artifact.path)))].sort();
  for (const directory of directories) {
    const readme = nested.find((artifact) => artifact.path === `${directory}/README.md`);
    const index = nested.find((artifact) => artifact.path === `${directory}/index.md`);
    if (readme ?? index) selected.push((readme ?? index)!);
  }
  return selected;
}

function resolveManagedPath(documentPath: string, label: string): { path: string; directory: boolean } | null {
  if (!label || label.startsWith("/") || label.includes("\\")) return null;
  const directory = label.endsWith("/");
  const relative = label.replace(/\/+$/, "");
  const parts = relative.split("/");
  if (!parts.length || parts.some((part) => !part || part === "." || part === "..")) return null;
  const base = parentDirectory(documentPath);
  const path = base ? `${base}/${relative}` : relative;
  if (!isNotebookResourcePath(path)) return null;
  return { path, directory };
}

export function parseManagedIndex(documentPath: string, content: string): ManagedIndexEntry[] {
  const start = content.indexOf(INDEX_START);
  const end = content.indexOf(INDEX_END);
  if (start === -1 || end === -1 || start >= end) return [];
  const block = content.slice(start + INDEX_START.length, end);
  const entries: ManagedIndexEntry[] = [];
  for (const line of block.split(/\r?\n/)) {
    const identity = MANAGED_ID.exec(line.trim());
    if (!identity?.groups) continue;
    let label: string;
    try {
      label = decodeURIComponent(identity.groups.path!);
    } catch {
      continue;
    }
    const prefix = `- ${label} — `;
    const rendered = identity.groups.line!;
    if (!rendered.startsWith(prefix)) continue;
    const resolved = resolveManagedPath(documentPath, label);
    if (!resolved) continue;
    entries.push({ path: resolved.path, description: rendered.slice(prefix.length).trim(), directory: resolved.directory });
  }
  return entries;
}

function titleForDirectory(path: string): string {
  const leaf = pathParts(path).at(-1) ?? path;
  return leaf.replace(/[-_]+/g, " ").replace(/^./, (letter) => letter.toUpperCase());
}

function descriptorForDirectory(
  directory: string,
  selectedDocuments: Map<string, MemoryArtifactSummary>,
): MemoryArtifactSummary | null {
  return selectedDocuments.get(`${directory}/README.md`) ?? selectedDocuments.get(`${directory}/index.md`) ?? null;
}

export function buildNotebookRailModel(
  artifacts: MemoryArtifactSummary[],
  documents: ReadonlyMap<string, string>,
): NotebookRailModel {
  const pages = artifacts.filter(isNotebookPage);
  const pageByPath = new Map(pages.map((artifact) => [artifact.path, artifact]));
  const selectedDocuments = new Map(selectIndexDocuments(artifacts).map((artifact) => [artifact.path, artifact]));
  const consumed = new Set<string>();

  const build = (documentPath: string, ancestors: Set<string>): NotebookRailEntry[] => {
    const content = documents.get(documentPath);
    if (content == null) return [];
    const entries: NotebookRailEntry[] = [];
    for (const managed of parseManagedIndex(documentPath, content)) {
      if (managed.directory) {
        if (ancestors.has(managed.path)) continue;
        const descriptor = descriptorForDirectory(managed.path, selectedDocuments);
        const nextAncestors = new Set(ancestors).add(managed.path);
        entries.push({
          kind: "directory",
          path: managed.path,
          title: titleForDirectory(managed.path),
          description: managed.description,
          children: descriptor ? build(descriptor.path, nextAncestors) : [],
        });
        continue;
      }
      const artifact = pageByPath.get(managed.path);
      if (!artifact || consumed.has(artifact.path)) continue;
      consumed.add(artifact.path);
      entries.push({ kind: "note", path: artifact.path, description: managed.description, artifact });
    }
    return entries;
  };

  return {
    entries: build("index.md", new Set([""])),
    files: pages
      .filter((artifact) => !consumed.has(artifact.path))
      .sort((a, b) => a.title.localeCompare(b.title) || a.path.localeCompare(b.path)),
  };
}

export function firstNotebookPath(entries: NotebookRailEntry[]): string | null {
  for (const entry of entries) {
    if (entry.kind === "note") return entry.path;
    const nested = firstNotebookPath(entry.children);
    if (nested) return nested;
  }
  return null;
}
