import type { MemoryArtifactSummary } from "@/features/memory/lib/notebookTypes";

/** Plain directory structure derived from canonical wiki page paths. */
export interface WorkspaceDir {
  name: string;
  /** Directory key with trailing slash ("topics/"); "" for the root. */
  path: string;
  dirs: WorkspaceDir[];
  files: MemoryArtifactSummary[];
}

export type SortKey = "name" | "modified" | "created";

export interface SortOrder {
  key: SortKey;
  asc: boolean;
}

export function stem(path: string): string {
  const leaf = path.split("/").at(-1) ?? path;
  return leaf.replace(/\.md$/, "");
}

/** Managed wiki pages own a human title in frontmatter. */
export function displayTitle(artifact: Pick<MemoryArtifactSummary, "path" | "source" | "title">): string {
  return artifact.title.trim() || stem(artifact.path);
}

export function parentDir(path: string): string {
  const parts = path.split("/");
  return parts.length > 1 ? parts.slice(0, -1).join("/") : "";
}

export function buildWorkspaceTree(artifacts: MemoryArtifactSummary[]): WorkspaceDir {
  const root: WorkspaceDir = { name: "", path: "", dirs: [], files: [] };
  const byPath = new Map<string, WorkspaceDir>([["", root]]);
  const ensureDir = (key: string): WorkspaceDir => {
    const existing = byPath.get(key);
    if (existing) return existing;
    const trimmed = key.replace(/\/$/, "");
    const parts = trimmed.split("/");
    const parent = ensureDir(parts.length > 1 ? `${parts.slice(0, -1).join("/")}/` : "");
    const dir: WorkspaceDir = {
      name: parts.at(-1)!,
      path: key,
      dirs: [],
      files: [],
    };
    parent.dirs.push(dir);
    byPath.set(key, dir);
    return dir;
  };
  for (const artifact of artifacts) {
    const parent = parentDir(artifact.path);
    ensureDir(parent ? `${parent}/` : "").files.push(artifact);
  }
  const sortDirs = (dir: WorkspaceDir) => {
    dir.dirs.sort((a, b) => a.name.localeCompare(b.name));
    dir.dirs.forEach(sortDirs);
  };
  sortDirs(root);
  return root;
}

/** Folder overviews stay first, followed by the requested note ordering. */
export function sortNotes(files: readonly MemoryArtifactSummary[], order: SortOrder, createdAt: (artifact: MemoryArtifactSummary) => string): MemoryArtifactSummary[] {
  const field = (artifact: MemoryArtifactSummary) => (order.key === "modified" ? (artifact.updatedAt ?? "") : order.key === "created" ? createdAt(artifact) : "");
  return [...files].sort((a, b) => {
    if (a.path.split("/").at(-1) === "README.md") return -1;
    if (b.path.split("/").at(-1) === "README.md") return 1;
    if (a.path === "index.md") return -1;
    if (b.path === "index.md") return 1;
    const cmp = order.key === "name" ? displayTitle(a).localeCompare(displayTitle(b)) : field(a).localeCompare(field(b)) || displayTitle(a).localeCompare(displayTitle(b));
    return order.asc ? cmp : -cmp;
  });
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];

export function prettyDate(iso: string | null | undefined): string {
  if (!iso) return "";
  return `${MONTHS[Number(iso.slice(5, 7)) - 1]} ${Number(iso.slice(8, 10))}, ${iso.slice(0, 4)}`;
}

/** Notebook Navigator's date rail: the recent past in relative buckets, then
 *  calendar months (year-qualified once it leaves the current one). Both sides
 *  are read as calendar days at UTC midnight so a local evening note doesn't
 *  land a day off. */
export function noteDateGroup(iso: string | null | undefined, now: Date): string {
  if (!iso) return "Undated";
  const localToday = Date.UTC(now.getFullYear(), now.getMonth(), now.getDate());
  const day = Date.parse(`${iso.slice(0, 10)}T00:00:00Z`);
  const days = Math.round((localToday - day) / 86_400_000);
  if (days <= 0) return "Today";
  if (days === 1) return "Yesterday";
  if (days <= 7) return "Previous 7 days";
  if (days <= 30) return "Previous 30 days";
  const year = Number(iso.slice(0, 4));
  const month = MONTH_NAMES[Number(iso.slice(5, 7)) - 1]!;
  return year === now.getFullYear() ? month : `${month} ${year}`;
}
