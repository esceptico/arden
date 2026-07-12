import type { PageLinks } from "@/features/memory/lib/notebookTypes";

export function isMissingArtifactError(error: unknown) {
  return error instanceof Error && error.message.toLowerCase().includes("memory artifact not found");
}

export interface ResolvedWikiTarget {
  path: string;
  anchor: string | null;
}

/** Resolve only from the server-built link index. Client metadata is not a
 * substitute because aliases and ambiguity are vault semantics. */
export function resolveWikiTarget(links: PageLinks | null, target: string): ResolvedWikiTarget | null {
  if (!links) return null;
  const matches = links.outgoing.filter((link) => link.target === target);
  const paths = new Set(matches.flatMap((link) =>
    link.status === "resolved" && link.resolvedPath ? [link.resolvedPath] : []));
  if (matches.length === 0 || paths.size !== 1 || matches.some((link) => link.status !== "resolved")) return null;
  const hash = target.indexOf("#");
  const anchor = hash < 0 ? null : target.slice(hash + 1).trim() || null;
  return { path: [...paths][0]!, anchor };
}
