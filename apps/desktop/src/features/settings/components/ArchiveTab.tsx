import { useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { ArchiveRestore, Trash2 } from "@/components/icons";
import clsx from "clsx";
import { useStore } from "@/stores";
import { EASE_OUT, MOTION, ROW_EXIT, SPRING_LAYOUT } from "@/lib/tokens/motion";
import { fetchArchivedSessions, permanentlyDeleteSession, restoreArchivedSession } from "@/actions/sessions";
import type { ArchivedSession } from "@/api/sessions";
import { useMutationState } from "@/lib/hooks";
import { formatRelativePast } from "@/lib/format";
import { ICON } from "@/lib/icons";
import { Skeleton } from "@/components/ui/Skeleton";
import { SearchInput } from "@/components/ui/SearchInput";
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
    <>
      {archivedCount > 0 && (
        <div className="settings-list-toolbar">
          <SearchInput
            value={query}
            onChange={setQuery}
            placeholder="Search archived sessions"
            ariaLabel="Search archived sessions"
            showClear
            trailing={`${filtered?.length ?? archivedCount} items`}
            className="settings-list-search"
          />
        </div>
      )}

      <SettingsSection title="Sessions" detail="newest first">
        {filtered === null ? (
          <div className="flex flex-col gap-1" role="status" aria-label="Loading archived items…">
            {Array.from({ length: 5 }).map((_, i) => (
              <Skeleton key={i} height={52} radius={10} />
            ))}
          </div>
        ) : filtered.length === 0 ? (
          <div className="settings-empty-note">
            {archived && archived.length > 0
              ? "No matches."
              : "Nothing here. Archived sessions will show up in this view."}
          </div>
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
    </>
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
  const { busy: anyBusy, error, run } = useMutationState();
  const [busyOp, setBusyOp] = useState<"restore" | "delete" | null>(null);
  // Inline two-click confirm replaces the native confirm() dialog: first
  // click arms ("Confirm delete"), second commits; auto-reverts after 3s.
  const [confirming, setConfirming] = useState(false);
  const confirmTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => {
    if (confirmTimer.current) clearTimeout(confirmTimer.current);
  }, []);

  const trigger = async (op: "restore" | "delete", fn: () => Promise<void>) => {
    if (anyBusy) return;
    setBusyOp(op);
    await run(fn);
    setBusyOp(null);
  };

  const onRestore = () =>
    void trigger("restore", () => restoreArchivedSession(session.session_id));
  const onDelete = () => {
    if (confirmTimer.current) clearTimeout(confirmTimer.current);
    if (confirming) {
      setConfirming(false);
      void trigger("delete", () => permanentlyDeleteSession(session.session_id));
      return;
    }
    setConfirming(true);
    confirmTimer.current = setTimeout(() => setConfirming(false), 3000);
  };

  return (
    <motion.li
      ref={ref}
      layout
      exit={{ ...ROW_EXIT, transition: { duration: MOTION.row, ease: EASE_OUT } }}
      transition={{ layout: SPRING_LAYOUT }}
      onMouseLeave={() => {
        if (confirmTimer.current) clearTimeout(confirmTimer.current);
        setConfirming(false);
      }}
      className="settings-archive-row group"
    >
      <div className="min-w-0 flex-1">
        <div className="settings-archive-title">
          {session.name || "untitled"}
        </div>
        <div className="settings-archive-meta">
          archived {formatRelativePast(session.archived_at)} ago · {session.message_count} msg
          {session.message_count === 1 ? "" : "s"}
        </div>
        {error && (
          <div aria-live="polite" className="mt-1 text-xs text-bad truncate" title={error}>
            {error}
          </div>
        )}
      </div>
      <div className="settings-archive-actions">
        <RowAction
          icon={<ArchiveRestore size={ICON.XS} />}
          label="Restore"
          onClick={onRestore}
          busy={busyOp === "restore"}
        />
        <RowAction
          icon={<Trash2 size={ICON.XS} />}
          label={confirming ? "Confirm delete" : "Delete"}
          onClick={onDelete}
          busy={busyOp === "delete"}
          danger
        />
      </div>
    </motion.li>
  );
}

function RowAction({
  icon,
  label,
  onClick,
  busy,
  danger,
}: {
  icon: React.ReactNode;
  label: string;
  onClick: () => void;
  busy?: boolean;
  danger?: boolean;
}) {
  // Bespoke (not Button): a dense list-row action whose destructive form is
  // neutral-at-rest / red-on-hover (the app's destructive convention, cf.
  // ConfirmDeleteButton) — Button's `danger` variant is red-at-rest, which
  // doesn't fit. Kept hand-rolled at h-6/text-xs density.
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={busy}
      className={clsx(
        "inline-flex items-center gap-1.5 h-6 px-2 rounded-[var(--r-control)] text-xs font-medium tracking-[var(--tracking-tight)] transition-[color,background-color,scale] duration-check ease-out active:scale-[0.97]",
        busy
          ? "text-faint cursor-wait"
          : danger
            ? "text-ink-soft hover:bg-bad-soft hover:text-bad"
            : "text-ink-soft hover:bg-surface-soft hover:text-ink",
      )}
    >
      {icon}
      {label}
    </button>
  );
}
