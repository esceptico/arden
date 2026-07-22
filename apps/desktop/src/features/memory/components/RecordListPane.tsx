import { Database, Pin, Search } from "@/components/icons";
import { AnimatePresence, motion } from "motion/react";
import clsx from "clsx";
import { IconButton } from "@/components/ui/IconButton";
import { RISE_IN, RISE_SETTLED, ROW_EXIT, SPRING_ROW_ENTRY } from "@/lib/tokens/motion";
import { ListColumn, ListError } from "@/components/ui/ListColumn";
import { GhostBtn, relativeTime } from "@/features/memory/components/shared";
import { kindLabel, scopeLabel } from "@/features/memory/lib/format";
import type { MemoryItem } from "@/api/memoryItems";

// Search field + mode toggle + kind filter live in ArtifactMemoryView's
// shared list header (stable across mode switches); this pane is just the
// record list.
export function RecordListPane({
  query,
  onQueryChange,
  records,
  recordsLoading,
  recordsError,
  selectedRecordId,
  pinningId,
  reduce,
  onSelectRecord,
  onTogglePinned,
  onRetry,
}: {
  query: string;
  onQueryChange: (value: string) => void;
  records: MemoryItem[];
  recordsLoading: boolean;
  recordsError: string | null;
  selectedRecordId: string | null;
  pinningId: string | null;
  reduce: boolean;
  onSelectRecord: (id: string) => void;
  onTogglePinned: (record: MemoryItem) => void;
  onRetry: () => void;
}) {
  return (
    <ListColumn
        toolbar={null}
        items={records}
        loading={recordsLoading}
        error={
          recordsError ? (
            <ListError
              title="Couldn't load memory records"
              message={recordsError}
              onRetry={onRetry}
            />
          ) : undefined
        }
        empty={query.trim() ? "No records match your search" : "No memory records yet"}
        emptyIcon={query.trim() ? Search : Database}
        emptyAction={query.trim() ? <GhostBtn onClick={() => onQueryChange("")}>Clear search</GhostBtn> : undefined}
        totalLabel={null}
        wrapItems={(children) => <AnimatePresence initial={false}>{children}</AnimatePresence>}
        renderItem={(record) => (
          <motion.li
            key={record.id}
            layout={!reduce}
            initial={reduce ? false : RISE_IN}
            animate={RISE_SETTLED}
            exit={reduce ? { opacity: 0 } : ROW_EXIT}
            transition={SPRING_ROW_ENTRY}
            className="group/row relative"
          >
            <button
              type="button"
              onClick={() => onSelectRecord(record.id)}
              className="app-row w-full rounded-lg p-2 pr-7 text-left"
              data-active={selectedRecordId === record.id}
            >
              <div className="line-clamp-2 text-sm text-ink">{record.content}</div>
              <div className="mt-1.5 flex items-center gap-1.5 text-2xs text-muted">
                <span className="font-medium">{kindLabel(record.kind)}</span>
                {record.scope?.kind && record.scope.kind !== "global" && (
                  <>
                    <span className="text-faint">·</span>
                    <span>{scopeLabel(record.scope)}</span>
                  </>
                )}
                {record.pinned && (
                  <>
                    <span className="text-faint">·</span>
                    <span>pinned</span>
                  </>
                )}
                <span className="text-faint">·</span>
                <span className="tabular-nums">{relativeTime(record.updated_at)}</span>
              </div>
            </button>
            <IconButton
              size="xs"
              tone="faint"
              disabled={pinningId === record.id}
              title={record.pinned ? "Unpin — drop from always-on Profile" : "Pin — always keep in context"}
              aria-label={record.pinned ? "Unpin — drop from always-on Profile" : "Pin — always keep in context"}
              aria-pressed={record.pinned}
              onClick={() => onTogglePinned(record)}
              className={clsx("absolute right-1 top-1 focus-visible:opacity-100", record.pinned ? "opacity-100" : "opacity-0 group-hover/row:opacity-100 group-focus-within/row:opacity-100")}
            >
              <Pin className="h-3.5 w-3.5" />
            </IconButton>
          </motion.li>
        )}
      />
  );
}
