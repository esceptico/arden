import { TabPanels } from "@/components/ui/TabPanels";
import type { WikiLinkHandlers } from "@/lib/wikilink";
import { MemoryNote } from "@/features/memory/components/MemoryNote";
import type { MemoryArtifactDetail, MemoryArtifactSummary } from "@/features/memory/lib/notebookTypes";

export function FileDetailPane({
  summary,
  detail,
  loading,
  direction,
  contentNotice,
  contentError,
  contentLoading,
  wikiHandlers,
  onRetry,
}: {
  summary: MemoryArtifactSummary | null;
  detail: MemoryArtifactDetail | null;
  loading: boolean;
  direction: number;
  contentNotice: string | null;
  contentError: string | null;
  contentLoading: boolean;
  wikiHandlers: WikiLinkHandlers;
  onRetry: () => void;
}) {
  return (
    <TabPanels
      value={summary?.path ?? "empty"}
      direction={direction}
      className="h-full min-h-0 grid-rows-[minmax(0,1fr)] overflow-hidden"
    >
      <MemoryNote
        summary={summary}
        detail={detail}
        listLoading={loading}
        contentLoading={contentLoading}
        contentNotice={contentNotice}
        contentError={contentError}
        wikiHandlers={wikiHandlers}
        onRetry={onRetry}
      />
    </TabPanels>
  );
}
