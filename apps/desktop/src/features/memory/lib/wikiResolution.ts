import { wikiSlug } from "@/lib/wikilink";

function aliasKey(value: string) {
  return value.trim().toLowerCase();
}

export function addAlias(map: Map<string, Set<string>>, key: string, path: string) {
  const exact = aliasKey(key);
  if (!exact) return;
  const paths = map.get(exact) ?? new Set<string>();
  paths.add(path);
  map.set(exact, paths);
}

export function preferredAlias(map: Map<string, Set<string>>, key: string): string | null {
  const paths = map.get(aliasKey(key));
  if (!paths) return null;
  if (paths.size === 1) return [...paths][0];
  const slug = wikiSlug(key);
  for (const candidate of [`topics/${slug}.md`, `entities/${slug}.md`, `areas/${slug}.md`, `context/integrations/${slug}.md`]) {
    if (paths.has(candidate)) return candidate;
  }
  return null;
}

export function isMissingArtifactError(error: unknown) {
  return error instanceof Error && error.message.toLowerCase().includes("memory artifact not found");
}
