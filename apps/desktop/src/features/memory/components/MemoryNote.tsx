import { useRef } from "react";
import { FileText } from "@/components/icons";
import { Markdown } from "@/components/ui/Markdown";
import { DetailPlaceholder } from "@/components/ui/EmptyState";
import { ListError } from "@/components/ui/ListColumn";
import { Skeleton } from "@/components/ui/Skeleton";
import { WikiLinkContext, type WikiLinkHandlers } from "@/lib/wikilink";
import { MemoryFindBar } from "@/features/memory/components/MemoryFindBar";
import { MemoryProperties, type MemoryFrontmatter } from "@/features/memory/components/MemoryProperties";
import { stripCites } from "@/features/memory/lib/format";
import type { MemoryArtifactDetail, MemoryArtifactSummary } from "@/features/memory/lib/notebookTypes";

function filenameStem(path: string): string {
  const name = path.split("/").pop() ?? path;
  return name.replace(/\.md$/, "");
}

function noteTrail(path: string): string {
  const parts = path.replace(/\.md$/, "").split("/");
  return parts.length > 1 ? parts.slice(0, -1).join(" / ") : "memory";
}

export function MemoryNote({
  summary,
  detail,
  listLoading,
  contentLoading,
  contentNotice,
  contentError,
  wikiHandlers,
  onFrontmatterChange,
  onRetry,
}: {
  summary: MemoryArtifactSummary | null;
  detail: MemoryArtifactDetail | null;
  listLoading: boolean;
  contentLoading: boolean;
  contentNotice: string | null;
  contentError: string | null;
  wikiHandlers: WikiLinkHandlers;
  /** Undefined = read-only properties (non-editable pages). */
  onFrontmatterChange?: (next: MemoryFrontmatter) => void;
  onRetry: () => void;
}) {
  const scrollerRef = useRef<HTMLDivElement>(null);

  if (!summary) {
    return (
      <div className="flex h-full min-h-0 flex-col">
        {listLoading ? (
          <DetailPlaceholder>Loading…</DetailPlaceholder>
        ) : (
          <DetailPlaceholder icon={FileText} hint="Choose a note from the index to read it.">
            Nothing selected
          </DetailPlaceholder>
        )}
      </div>
    );
  }

  const content = detail?.content ?? "";

  return (
    <article
      data-memory-note-path={summary.path}
      tabIndex={-1}
      className="relative flex h-full min-h-0 flex-col outline-none"
    >
      <MemoryFindBar scrollerRef={scrollerRef} />
      <div
        ref={scrollerRef}
        data-memory-note-scroll
        data-page-enter-item
        data-page-skeleton
        className="mw-scroller scroll-thin scroll-fade min-w-0"
      >
        <div className="mw-page">
          <p className="mw-note-crumb">{noteTrail(summary.path)} / <b>{filenameStem(summary.path)}</b></p>
          <h1 className="mw-note-title">{filenameStem(summary.path)}</h1>
          {detail && (
            <MemoryProperties
              frontmatter={detail.frontmatter}
              editable={onFrontmatterChange != null}
              onChange={onFrontmatterChange}
            />
          )}
          {contentNotice && (
            <div className="mb-5 mt-5 rounded-[10px] bg-surface-soft px-3 py-2 text-sm text-muted">{contentNotice}</div>
          )}
          <div>
            {contentError && !content ? (
              <ListError title="Couldn't load this note" message={contentError} onRetry={onRetry} />
            ) : contentLoading && !content ? (
              <div className="mt-6 grid gap-3" role="status" aria-label="Loading note…">
                <Skeleton width="42%" height={15} />
                <Skeleton lines={8} height={14} />
              </div>
            ) : (
              <>
                <WikiLinkContext.Provider value={wikiHandlers}>
                  <div className="mw-prose">
                    <Markdown content={stripCites(content)} className="memory-doc max-w-none" />
                  </div>
                </WikiLinkContext.Provider>
              </>
            )}
          </div>
        </div>
      </div>
    </article>
  );
}
