import { useEffect, useMemo, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { Archive01, ArchiveRestore } from "@/components/icons";
import { useStore } from "@/stores";
import { EASE_OUT, MOTION, ROW_EXIT, SPRING_LAYOUT } from "@/lib/tokens/motion";
import { fetchArchivedSessions, permanentlyDeleteSession, restoreArchivedSession } from "@/actions/sessions";
import type { ArchivedSession } from "@/api/sessions";
import { useMutationState } from "@/lib/hooks";
import { formatBytes, formatRelativePast } from "@/lib/format";
import { ICON } from "@/lib/icons";
import { Skeleton } from "@/components/ui/Skeleton";
import { SearchInput } from "@/components/ui/SearchInput";
import { EmptyState } from "@/components/ui/EmptyState";
import { IconButton } from "@/components/ui/IconButton";
import { ConfirmDeleteButton } from "@/components/ui/ConfirmDeleteButton";
import { SettingsSection, SettingsSurface } from "@/features/settings/components/SettingsPage";

export function ArchiveTab() {
  const archived = useStore((s) => s.archivedSessions);
  const [query, setQuery] = useState("");

  useEffect(() => {
    void fetchArchivedSessions();
  }, []);

  const filtered = useMemo(() => {
    if (!archived) return null;
    const q = query.trim().toLowerCase();
    if (!q) return archived;
    return archived.filter((s) => (s.name ?? "untitled").toLowerCase().includes(q));
  }, [archived, query]);

  const archivedCount = archived?.length ?? 0;

  return (
    <SettingsSection
      title="Archived chats"
      detail={filtered === null ? "loading" : `${filtered.length} of ${archivedCount}`}
      action={
        archivedCount > 0 ? (
          <SearchInput
            value={query}
            onChange={setQuery}
            placeholder="Search archived chats"
            showClear
            size="compact"
            className="w-[260px]"
          />
        ) : undefined
      }
    >
      {filtered === null ? (
        <div className="flex flex-col gap-1" role="status" aria-label="Loading archived chats…">
          {Array.from({ length: 5 }).map((_, i) => (
            <Skeleton key={i} height={62} radius={10} />
          ))}
        </div>
      ) : filtered.length === 0 ? (
        <SettingsSurface className="py-10">
          <EmptyState icon={Archive01} hint={archivedCount > 0 ? "Try a different search." : undefined}>
            {archivedCount > 0
              ? "No archived chat matches that."
              : "Nothing archived yet. Archiving a chat moves it out of the sidebar and into this list."}
          </EmptyState>
        </SettingsSurface>
      ) : (
        <SettingsSurface>
          {/* Keyed by query so filter keystrokes swap the list instantly; restore/delete under a stable query still animates. */}
          <ul key={query} className="settings-archive-list">
            <AnimatePresence mode="popLayout" initial={false}>
              {filtered.map((s) => (
                <ArchivedRow key={s.session_id} session={s} />
              ))}
            </AnimatePresence>
          </ul>
        </SettingsSurface>
      )}
    </SettingsSection>
  );
}

// `ref` reaches the li so AnimatePresence popLayout can measure the row
// before popping it out of the layout on exit.
function ArchivedRow({
  session,
  ref,
}: {
  session: ArchivedSession;
  ref?: React.Ref<HTMLLIElement>;
}) {
  const { busy, error, run } = useMutationState();
  // The action cluster only fades in on hover; an armed countdown has to pin it
  // open so it stays cancellable once the pointer moves away.
  const [armed, setArmed] = useState(false);

  return (
    <motion.li
      ref={ref}
      layout
      exit={{ ...ROW_EXIT, transition: { duration: MOTION.row, ease: EASE_OUT } }}
      transition={{ layout: SPRING_LAYOUT }}
      className="settings-archive-row group"
    >
      <div className="min-w-0 flex-1">
        <div className="settings-archive-title">
          {session.name || "untitled"}
        </div>
        <div className="settings-archive-meta">
          archived {formatRelativePast(session.archived_at)} ago · {session.message_count} msg
          {session.message_count === 1 ? "" : "s"}
          {session.storage_state === "cold" ? ` · cold · ${formatBytes(session.cold_bundle_bytes ?? 0)}` : ""}
        </div>
        {error && (
          <div aria-live="polite" className="mt-1 text-xs text-bad truncate" title={error}>
            {error}
          </div>
        )}
      </div>
      <div className="settings-archive-actions" data-armed={armed ? "true" : undefined}>
        <IconButton
          size="xs"
          title="Restore"
          disabled={busy}
          onClick={() => void run(() => restoreArchivedSession(session.session_id))}
        >
          <ArchiveRestore size={ICON.XS} />
        </IconButton>
        <ConfirmDeleteButton
          size="sm"
          label="Delete permanently"
          busy={busy}
          onActiveChange={setArmed}
          onConfirm={() => void run(() => permanentlyDeleteSession(session.session_id))}
        />
      </div>
    </motion.li>
  );
}
