// Pages whose body already IS their records (never prose-synthesized) — keep in sync
// with the server's artifacts._is_record_list_page.
const RECORD_LIST_PAGES = new Set(["directives.md", "lessons.md", "references.md"]);
export function isRecordListPage(path: string): boolean {
  return RECORD_LIST_PAGES.has(path) || path.split("/")[0] === "insights";
}

// Strip inline (record:XXXXXXXX) provenance groups from synthesized prose for the
// human view — they stay on disk. Mirrors the server's _CITATION_GROUP_RE so a lone
// 8-hex token not wrapped in (record:…) is left alone.
const _CITE_RE = /\s*\(record:[0-9a-fA-F]{8}(?:,\s*record:[0-9a-fA-F]{8})*\)/g;
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

function yamlScalar(value: string | number | boolean | null): string {
  if (value === null) return "null";
  if (typeof value !== "string") return String(value);
  return /^[\w./ -]*$/.test(value) && value.trim() === value && value !== "" ? value : JSON.stringify(value);
}

export function serializeFrontmatter(
  frontmatter: Record<string, string | number | boolean | null | Array<string | number | boolean | null>>,
): string {
  const keys = Object.keys(frontmatter);
  if (keys.length === 0) return "";
  const lines = keys.map((key) => {
    const value = frontmatter[key]!;
    if (Array.isArray(value)) return `${key}: [${value.map(yamlScalar).join(", ")}]`;
    return `${key}: ${yamlScalar(value)}`;
  });
  return `---\n${lines.join("\n")}\n---\n`;
}
