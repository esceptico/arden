import { useMemo, type ReactNode } from "react";
import { useStore } from "@/stores";
import { WikiLinkContext, type WikiLinkHandlers } from "@/lib/wikilink";

/**
 * Chat's wiki-link contract: a relative `.md` href — or a backticked bare
 * path — the agent cites renders as a clickable memory link with a hover
 * peek. Paths are the exact wiki-tool paths (`directory/file.md`, no `wiki/`
 * prefix). Existence is deliberately not checked here: the peek card surfaces
 * a missing page on hover, and a dead click opens the vault at its default
 * page. `[[Subject]]` wikilinks stay inert — alias resolution is server-side
 * state the memory view owns.
 */
export function ChatWikiLinks({ children }: { children: ReactNode }) {
  const openMemory = useStore((s) => s.openMemory);
  const handlers = useMemo<WikiLinkHandlers>(
    () => ({
      exists: () => false,
      existsInline: (target) => /^[^\s\\]+\.md$/.test(target),
      onNavigate: (target) => openMemory(target),
      onNavigateInline: (target, anchor) => openMemory(target, anchor ?? undefined),
      peek: true,
    }),
    [openMemory],
  );
  return <WikiLinkContext.Provider value={handlers}>{children}</WikiLinkContext.Provider>;
}
