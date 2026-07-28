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

function yamlValue(value: FrontmatterValue): string {
  if (value === null) return "null";
  if (Array.isArray(value) || typeof value === "object") return JSON.stringify(value);
  if (typeof value !== "string") return String(value);
  // JSON strings are valid YAML scalars and preserve values such as "true",
  // "null", dates, and numbers as strings when the whole block is rewritten.
  return JSON.stringify(value);
}

export function serializeFrontmatter(
  frontmatter: Record<string, FrontmatterValue>,
): string {
  const keys = Object.keys(frontmatter);
  if (keys.length === 0) return "";
  const lines = keys.map((key) =>
    `${/^[A-Za-z_][A-Za-z0-9_-]*$/.test(key) ? key : JSON.stringify(key)}: ${yamlValue(frontmatter[key]!)}`,
  );
  return `---\n${lines.join("\n")}\n---\n`;
}
