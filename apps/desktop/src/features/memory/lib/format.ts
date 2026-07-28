// Pages whose body already IS their records (never prose-synthesized) — keep in sync
// with the server's artifacts._is_record_list_page.
const RECORD_LIST_PAGES = new Set(["directives.md", "lessons.md", "references.md"]);
export function isRecordListPage(path: string): boolean {
  return RECORD_LIST_PAGES.has(path) || path.split("/")[0] === "insights";
}

// Strip inline (record:XXXXXXXX) provenance groups from synthesized prose for the
// human view — they stay on disk. Mirrors the server's _CITATION_GROUP_RE so a lone
// hex token not wrapped in (record:…) is left alone. Ids are NOT a fixed
// width — pinning the length to 8 left whole groups rendered raw in the prose.
const _CITE_RE = /\s*\(record:[0-9a-fA-F]{6,}(?:,\s*record:[0-9a-fA-F]{6,})*\)/g;
export function stripCites(s: string): string {
  return s.replace(_CITE_RE, "");
}

export function scopeLabel(scope: { kind: string; key: string | null }) {
  return scope.key ? `${scope.kind}:${scope.key}` : scope.kind;
}

// Plain user-facing words for internal kind values.
const _KIND_LABELS: Record<string, string> = {
  directive: "rule",
  observation: "observed",
  lesson: "lesson",
  changelog: "change",
};
export function kindLabel(kind: string) {
  return _KIND_LABELS[kind] ?? kind;
}

// Frontmatter handling for body-only editing and Properties saves. The
// draft edits the BODY in the editor and the frontmatter through the
// Properties grid; both recompose the full source before the review flow.
const _FM_RE = /^---\r?\n[\s\S]*?\r?\n---\r?\n?/;

export function splitFrontmatter(source: string): { frontmatter: string; body: string } {
  const match = _FM_RE.exec(source);
  if (!match) return { frontmatter: "", body: source };
  return { frontmatter: match[0], body: source.slice(match[0].length) };
}

type FrontmatterValue =
  | string
  | number
  | boolean
  | null
  | FrontmatterValue[]
  | { [key: string]: FrontmatterValue };

type FrontmatterScalar = string | number | boolean | null;

function yamlScalar(value: FrontmatterScalar): string {
  if (value === null) return "null";
  if (typeof value !== "string") return String(value);
  return JSON.stringify(value);
}

function yamlValue(value: FrontmatterValue): string {
  if (Array.isArray(value) && value.every((item): item is FrontmatterScalar => item === null || typeof item !== "object")) {
    return `[${value.map(yamlScalar).join(", ")}]`;
  }
  if (typeof value === "object" && value !== null) return JSON.stringify(value);
  return yamlScalar(value);
}

export function serializeFrontmatter(
  frontmatter: Record<string, FrontmatterValue>,
): string {
  const keys = Object.keys(frontmatter);
  if (keys.length === 0) return "";
  const lines = keys.map((key) => `${key}: ${yamlValue(frontmatter[key]!)}`);
  return `---\n${lines.join("\n")}\n---\n`;
}
