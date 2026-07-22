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
import { EmptyNote } from "@/components/ui/EmptyState";

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
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between gap-3">
        <p className="m-0 text-sm text-muted leading-[1.5]">
          {archivedCount > 0
            ? `${archivedCount} archived session${archivedCount === 1 ? "" : "s"}. Restore one to bring it back, or delete it for good.`
            : "Sessions you archive show up here."}
        </p>
        {archivedCount > 0 && (
          <SearchInput
            value={query}
            onChange={setQuery}
            placeholder="Search sessions…"
            ariaLabel="Search sessions"
            showClear
            className="w-[200px] shrink-0"
          />
        )}
      </div>

      {filtered === null ? (
        <div className="flex flex-col gap-1" role="status" aria-label="Loading archived items…">
          {Array.from({ length: 5 }).map((_, i) => (
            <Skeleton key={i} height={52} radius={10} />
          ))}
        </div>
      ) : filtered.length === 0 ? (
        <EmptyNote>
          {archived && archived.length > 0
            ? "No matches."
            : "Nothing here. Archived sessions will show up in this view."}
        </EmptyNote>
      ) : (
        // Keyed by query so filter keystrokes swap the list instantly (no
        // exits, no FLIP); restore/delete under a stable query still animates.
        <ul key={query} className="flex flex-col gap-1">
          <AnimatePresence mode="popLayout" initial={false}>
            {filtered.map((s) => (
              <ArchivedRow key={s.session_id} session={s} />
            ))}
          </AnimatePresence>
        </ul>
      )}
    </div>
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
      className="app-row group flex items-center gap-3 px-3 py-2 rounded-[10px]"
    >
      <div className="min-w-0 flex-1">
        <div className="text-base font-medium text-ink tracking-[-0.005em] truncate">
          {session.name || "untitled"}
        </div>
        <div className="text-xs text-faint tabular-nums">
          archived {formatRelativePast(session.archived_at)} ago · {session.message_count} msg
          {session.message_count === 1 ? "" : "s"}
        </div>
        {error && (
          <div aria-live="polite" className="mt-1 text-xs text-bad truncate" title={error}>
            {error}
          </div>
        )}
      </div>
      <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 focus-within:opacity-100 transition-opacity">
        <RowAction
          icon={<ArchiveRestore size={ICON.XS} strokeWidth={2} />}
          label="Restore"
          onClick={onRestore}
          busy={busyOp === "restore"}
        />
        <RowAction
          icon={<Trash2 size={ICON.XS} strokeWidth={2} />}
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
        "inline-flex items-center gap-1.5 h-6 px-2 rounded-md text-xs font-medium tracking-[-0.005em] transition-[color,background-color,scale] duration-check ease-out active:scale-[0.97]",
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

