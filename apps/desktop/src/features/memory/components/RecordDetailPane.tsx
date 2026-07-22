import { Database, Pin } from "@/components/icons";
import { TabPanels } from "@/components/ui/TabPanels";
import { DetailPlaceholder } from "@/components/ui/EmptyState";
import { DetailShell } from "@/components/ui/DetailShell";
import { MetaGrid } from "@/components/ui/MetaGrid";
import { Pill } from "@/components/ui/Pill";
import { GhostBtn, relativeTime } from "@/features/memory/components/shared";
import { kindLabel, scopeLabel } from "@/features/memory/lib/format";
import type { MemoryItem } from "@/api/memoryItems";

export function RecordDetailPane({
  record,
  direction,
  pinningId,
  onTogglePinned,
}: {
  record: MemoryItem | null;
  direction: number;
  pinningId: string | null;
  onTogglePinned: (record: MemoryItem) => void;
}) {
  if (!record) {
    return (
      <DetailPlaceholder icon={Database} hint="Pick a record from the list to inspect it.">
        Nothing selected
      </DetailPlaceholder>
    );
  }
  return (
    <TabPanels
      value={record.id}
      direction={direction}
      className="h-full min-h-0 grid-rows-[minmax(0,1fr)] overflow-hidden"
    >
    <DetailShell
      header={
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <h1 className="text-2xl font-medium capitalize tracking-tight text-ink">{kindLabel(record.kind)}</h1>
            <div className="mt-1 font-mono text-xs text-muted break-all">{record.id}</div>
          </div>
          <GhostBtn
            onClick={() => onTogglePinned(record)}
            disabled={pinningId === record.id}
            title={record.pinned ? "Drop from the always-on Profile block" : "Always keep this record in context"}
          >
            <Pin className="h-3.5 w-3.5" />
            {record.pinned ? "Pinned" : "Pin"}
          </GhostBtn>
        </div>
      }
      body={
        <div className="min-w-0 whitespace-pre-wrap break-words text-base leading-relaxed text-ink">
          {record.content}
        </div>
      }
      meta={
        <MetaGrid
          rows={[
            { label: "Kind", value: kindLabel(record.kind) },
            { label: "Scope", value: scopeLabel(record.scope) },
            { label: "Status", value: record.status },
            { label: "Updated", value: relativeTime(record.updated_at) },
            record.source_refs.length > 0 && {
              label: "Sources",
              value: record.source_refs.map((s) => `${s.kind}: ${s.ref}`).join("\n"),
              mono: true,
            },
          ]}
        />
      }
      actions={
        record.labels.length > 0 ? (
          <div className="mr-auto flex flex-wrap items-center gap-1">
            {record.labels.map((label) => (
              <Pill key={label} tone="neutral">
                {label}
              </Pill>
            ))}
          </div>
        ) : null
      }
    />
    </TabPanels>
  );
}
