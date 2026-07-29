const RESERVED_DIRECTORIES = new Set(["raw", ".arden", ".index", ".maintenance"]);
const RESERVED_ROOT_FILES = new Set(["AGENTS.md", "tooling.md"]);

function pathParts(path: string): string[] {
  return path.split("/").filter(Boolean);
}

export function isNotebookResourcePath(path: string): boolean {
  const parts = pathParts(path);
  if (parts.some((part) => RESERVED_DIRECTORIES.has(part))) return false;
  if (parts[0] === "changelog") return false;
  if (parts.length === 1 && RESERVED_ROOT_FILES.has(parts[0]!)) return false;
  return parts.length > 0;
}
