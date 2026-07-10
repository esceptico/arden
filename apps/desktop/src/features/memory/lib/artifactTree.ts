import type { MemoryArtifact } from "@/api/memoryArtifacts";

export type TreeNode = {
  name: string;
  path: string;
  kind: "directory" | "file";
  artifact?: MemoryArtifact;
  children: TreeNode[];
};

// Folder sort + default-expand order, post topics/-unification (entities/ & areas/
// are folded into topics/).
const DIRECTORY_ORDER = ["topics", "feeds", "daily", "insights", "context", "facts", "changelog"];
// Only the subject pages open by default — dated logs / feeds / audit stay folded
// until asked for. The tree should read as "who and what", not "everything".
const DEFAULT_EXPANDED_DIRS = new Set(["topics"]);

// Root reads top-down by how often a page matters: the hero pages, then the
// folders, then stray files, then generated system reports (quiet tail).
const ROOT_FILE_ORDER = ["me.md", "active-work.md", "directives.md", "lessons.md", "references.md"];
export const SYSTEM_FILES = new Set(["index.md", "health.md", "AGENTS.md", "README.md", "tooling.md"]);

function rootRank(n: TreeNode): number {
  if (n.kind === "directory") {
    const i = DIRECTORY_ORDER.indexOf(n.name);
    return 100 + (i === -1 ? 99 : i);
  }
  const i = ROOT_FILE_ORDER.indexOf(n.path);
  if (i !== -1) return i;
  return SYSTEM_FILES.has(n.path) ? 300 : 200;
}

function rootSort(a: TreeNode, b: TreeNode) {
  return rootRank(a) - rootRank(b) || a.name.localeCompare(b.name);
}

function directorySort(a: TreeNode, b: TreeNode) {
  if (a.kind !== b.kind) return a.kind === "directory" ? -1 : 1;
  const ai = DIRECTORY_ORDER.indexOf(a.name);
  const bi = DIRECTORY_ORDER.indexOf(b.name);
  return (ai === -1 ? 999 : ai) - (bi === -1 ? 999 : bi) || a.name.localeCompare(b.name);
}

function addNode(parent: TreeNode, parts: string[], artifact: MemoryArtifact, pathPrefix = "") {
  const [part, ...rest] = parts;
  if (!part) return;
  const nodePath = pathPrefix ? `${pathPrefix}/${part}` : part;
  if (rest.length === 0) {
    parent.children.push({ name: part, path: artifact.path, kind: "file", artifact, children: [] });
    return;
  }
  let child = parent.children.find((n) => n.kind === "directory" && n.name === part);
  if (!child) {
    child = { name: part, path: nodePath, kind: "directory", children: [] };
    parent.children.push(child);
  }
  addNode(child, rest, artifact, nodePath);
}

export function buildArtifactTree(artifacts: MemoryArtifact[]) {
  const root: TreeNode = { name: "root", path: "", kind: "directory", children: [] };
  for (const artifact of artifacts) {
    // Root files (me.md, index.md, …) live AT the vault root — render them at the top
    // level alongside the real folders (daily/, topics/, …), not under a fake "memory" dir.
    addNode(root, artifact.path.split("/"), artifact);
  }
  const sortRec = (node: TreeNode) => {
    node.children.sort(directorySort);
    node.children.forEach(sortRec);
  };
  root.children.forEach(sortRec);
  root.children.sort(rootSort);
  return root.children;
}

export function collectDefaultFolderPaths(nodes: TreeNode[]): string[] {
  const out: string[] = [];
  const walk = (ns: TreeNode[]) => {
    for (const n of ns) {
      if (n.kind === "directory") {
        if (DEFAULT_EXPANDED_DIRS.has(n.path)) out.push(n.path);
        walk(n.children);
      }
    }
  };
  walk(nodes);
  return out;
}

export function countFiles(node: TreeNode): number {
  if (node.kind === "file") return 1;
  return node.children.reduce((sum, child) => sum + countFiles(child), 0);
}

/** File-leaf paths in the order the tree renders them (depth-first, already
 *  directory-sorted). Drives the detail-pane slide direction so it matches the
 *  visible list position — selecting a note further down slides in from the
 *  right, further up from the left. */
export function flattenTreeFiles(nodes: TreeNode[]): string[] {
  const out: string[] = [];
  const walk = (ns: TreeNode[]) => {
    for (const n of ns) {
      if (n.kind === "file") out.push(n.path);
      else walk(n.children);
    }
  };
  walk(nodes);
  return out;
}
