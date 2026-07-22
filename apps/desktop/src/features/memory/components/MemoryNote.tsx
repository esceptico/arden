import { useRef, useState } from "react";
import { motion } from "motion/react";
import clsx from "clsx";
import { ChevronRight, FileText } from "@/components/icons";
import { EASE_EMPHASIZED, MOTION } from "@/lib/tokens/motion";
import { Markdown } from "@/components/ui/Markdown";
import { DetailPlaceholder } from "@/components/ui/EmptyState";
import { ListError } from "@/components/ui/ListColumn";
import { Skeleton } from "@/components/ui/Skeleton";
import { ScrollFadeTop } from "@/components/ui/ScrollBlur";
import { WikiLinkContext, type WikiLinkHandlers } from "@/lib/wikilink";
import { MemoryFindBar } from "@/features/memory/components/MemoryFindBar";
import { MemoryProperties, type MemoryFrontmatter } from "@/features/memory/components/MemoryProperties";
import { isRecordListPage, kindLabel, stripCites } from "@/features/memory/lib/format";
import type { MemoryArtifactDetail, MemoryArtifactSummary } from "@/features/memory/lib/notebookTypes";

const FRONTMATTER_RE = /^---\r?\n[\s\S]*?\r?\n---\r?\n?/;
const ENT_TAG_RE = /\s*\[ent:[^\]]+\]/g;
// Markdown-table heuristic — the draft widens the page for tabular content.
const TABLE_RE = /\n\|.*\|/;

function filenameStem(path: string): string {
  const name = path.split("/").pop() ?? path;
  return name.replace(/\.md$/, "");
}

// The page's dated record ledger, collapsed under the prose (draft's krecs).
function RecordsDisclosure({
  records,
  defaultOpen,
}: {
  records: MemoryArtifactDetail["timeline"];
  defaultOpen: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <section className={clsx("mw-krecs", open && "open")}>
      <button type="button" className="mw-krecs-head" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
        <ChevronRight className="mw-chev" strokeWidth={2} aria-hidden />
        Records <span className="n">{records.length}</span>
      </button>
      {open && (
        <div className="mw-krecs-body">
          {records.map((entry) => (
            <div key={entry.id} className="mw-krec">
              <div className="kmeta">
                <div>{entry.date}</div>
                <span className="kind">{kindLabel(entry.kind)}</span>
              </div>
              <p className="ktext">{entry.text.replace(ENT_TAG_RE, "")}</p>
            </div>
          ))}
        </div>
      )}
    </section>
  );
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
  const body = content.replace(FRONTMATTER_RE, "");
  const records = (detail?.timeline ?? []).filter((entry) => !entry.superseded);

  return (
    <article
      data-memory-note-path={summary.path}
      tabIndex={-1}
      className="relative flex h-full min-h-0 flex-col outline-none"
    >
      <MemoryFindBar scrollerRef={scrollerRef} />
      <div ref={scrollerRef} data-memory-note-scroll className="mw-scroller scroll-thin min-w-0">
        <ScrollFadeTop />
        <div className={clsx("mw-page", TABLE_RE.test(content) && "wide")}>
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
          {/* Fog-of-war reveal: the not-yet-resolved sharpens out of blur.
              Enter-only (keyed remount) — exits would churn on fast paging. */}
          <motion.div
            key={contentError && !content ? "error" : contentLoading && !content ? "loading" : "ready"}
            initial={{ opacity: 0, filter: "blur(3px)" }}
            animate={{ opacity: 1, filter: "blur(0px)", transitionEnd: { filter: "none" } }}
            transition={{ duration: MOTION.palette, ease: EASE_EMPHASIZED }}
          >
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
                {detail && records.length > 0 && !isRecordListPage(detail.path) && (
                  <RecordsDisclosure
                    key={detail.path}
                    records={records}
                    defaultOpen={stripCites(body).trim() === ""}
                  />
                )}
              </>
            )}
          </motion.div>
        </div>
      </div>
    </article>
  );
}
