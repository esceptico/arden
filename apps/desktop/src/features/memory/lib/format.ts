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

// The detail header already shows the page title, so a leading `# Title` H1 in the
// body just double-prints it (index.md, health.md, topic pages). Drop it for the view.
export function stripLeadingH1(s: string): string {
  return s.replace(/^\s*#\s+.*\r?\n+/, "");
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
