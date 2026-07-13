import { FileText } from "lucide-react";
import { Markdown } from "@/components/ui/Markdown";
import { DetailPlaceholder } from "@/components/ui/EmptyState";
import { ListError } from "@/components/ui/ListColumn";
import { Skeleton } from "@/components/ui/Skeleton";
import { ScrollFadeTop } from "@/components/ui/ScrollBlur";
import { WikiLinkContext, type WikiLinkHandlers } from "@/lib/wikilink";
import { Properties } from "@/features/memory/components/shared";
import { TimelineDisclosure } from "@/features/memory/components/MemoryTimelineDisclosure";
import { CopyPath } from "@/features/memory/components/CopyPath";
import { isRecordListPage, stripCites, stripLeadingH1 } from "@/features/memory/lib/format";
import type { MemoryArtifactDetail, MemoryArtifactSummary } from "@/features/memory/lib/notebookTypes";

export function MemoryNote({
  summary,
  detail,
  listLoading,
  contentLoading,
  contentNotice,
  contentError,
  wikiHandlers,
  onRetry,
}: {
  summary: MemoryArtifactSummary | null;
  detail: MemoryArtifactDetail | null;
  listLoading: boolean;
  contentLoading: boolean;
  contentNotice: string | null;
  contentError: string | null;
  wikiHandlers: WikiLinkHandlers;
  onRetry: () => void;
}) {
  if (!summary) {
    return listLoading ? (
      <DetailPlaceholder>Loading…</DetailPlaceholder>
    ) : (
      <DetailPlaceholder icon={FileText} hint="Choose a note from the index to read it.">
        Nothing selected
      </DetailPlaceholder>
    );
  }

  const content = detail?.content ?? "";
  return (
    <article data-memory-note-path={summary.path} className="flex h-full min-h-0 flex-col bg-bg-main">
      <div data-memory-note-scroll className="flex-1 min-h-0 overflow-y-auto scroll-thin">
        <ScrollFadeTop />
        <div className="mx-auto w-full max-w-[780px] px-8 pb-10 pt-8 sm:px-10 sm:pt-10">
          <header className="mb-7">
            <h1 className="text-[2rem] font-semibold leading-[1.12] tracking-[-0.025em] text-ink">{summary.title}</h1>
          </header>

          {contentNotice && <div className="mb-5 rounded-[10px] bg-surface-soft px-3 py-2 text-sm text-muted">{contentNotice}</div>}
          {contentError && !content ? (
            <ListError title="Couldn't load this note" message={contentError} onRetry={onRetry} />
          ) : contentLoading && !content ? (
            <div className="grid gap-3" role="status" aria-label="Loading note…">
              <Skeleton width="42%" height={15} />
              <Skeleton lines={8} height={14} />
            </div>
          ) : (
            <WikiLinkContext.Provider value={wikiHandlers}>
              <Markdown
                content={stripLeadingH1(stripCites(content))}
                className="max-w-none text-[15px] leading-[1.7]"
                provenance
              />
              {detail && (
                <div className="mt-10 flex flex-col gap-3">
                  {Object.keys(detail.frontmatter).length > 0 && (
                    <details data-memory-properties className="rounded-[10px] bg-surface-soft/35 px-3 py-2">
                      <summary className="cursor-pointer text-xs font-medium text-muted hover:text-ink-soft">
                        Properties
                      </summary>
                      <div className="mt-3">
                        <Properties frontmatter={detail.frontmatter} />
                      </div>
                    </details>
                  )}
                  {!isRecordListPage(detail.path) && <TimelineDisclosure timeline={detail.timeline} />}
                </div>
              )}
            </WikiLinkContext.Provider>
          )}

          {detail && (
            <footer
              data-memory-note-metadata
              className="mt-10 flex flex-wrap items-center gap-x-2 gap-y-1 border-t border-line-soft/60 pt-4 text-2xs text-faint"
            >
              <span className="font-mono break-all">{detail.path}</span>
              <CopyPath path={detail.path} />
              {detail.labels.map((label) => <span key={label}>#{label}</span>)}
              {detail.generated && <span>generated</span>}
              {detail.source && <span>{detail.source}</span>}
              <span title={detail.revision} className="font-mono">rev {detail.revision.slice(0, 12)}</span>
              {detail.editable && detail.editableContent != null && <span>⌘E to edit</span>}
              {detail.readonlyReason && <span className="basis-full pt-1 text-muted">{detail.readonlyReason}</span>}
            </footer>
          )}
        </div>
      </div>
    </article>
  );
}
