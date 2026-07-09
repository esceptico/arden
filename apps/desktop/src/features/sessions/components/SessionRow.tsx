import { useEffect, useRef, useState } from "react";
import { MoreHorizontal } from "lucide-react";
import { renameSession, switchSession } from "@/actions/sessions";
import { ICON } from "@/lib/icons";
import { formatRelativePast } from "@/lib/format";
import { SessionStateIcon } from "@/features/sessions/components/SessionStateIcon";
import { ConfirmDeleteButton } from "@/components/ui/ConfirmDeleteButton";

export function SessionRow({
  sessionId,
  name,
  lastActivity,
  active,
  streaming,
  unread,
  depth = 0,
  tabbable,
  renaming,
  onStartRename,
  onCancelRename,
  onMenu,
  onContextMenu,
  onArchive,
}: {
  sessionId: string;
  name: string | null;
  lastActivity: string;
  active: boolean;
  streaming: boolean;
  unread: boolean;
  depth?: number;
  /** Roving tabindex: exactly one row per list is the Tab stop; arrows on
   *  the list container move focus between rows. */
  tabbable: boolean;
  renaming: boolean;
  onStartRename: () => void;
  onCancelRename: () => void;
  onMenu: (pos: { x: number; y: number }) => void;
  onContextMenu: (e: React.MouseEvent) => void;
  onArchive: () => void;
}) {
  const [draft, setDraft] = useState(name ?? "");
  const [deleting, setDeleting] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (renaming) {
      setDraft(name ?? "");
      requestAnimationFrame(() => inputRef.current?.select());
    }
  }, [renaming, name]);

  async function commitRename() {
    const trimmed = draft.trim();
    onCancelRename();
    if (!trimmed || trimmed === (name ?? "")) return;
    try {
      await renameSession(sessionId, trimmed);
    } catch {
      /* surfaced via store error elsewhere */
    }
  }

  if (renaming) {
    return (
      <div
        className="surface-card grid grid-cols-[16px_minmax(0,1fr)] items-center gap-2 w-full px-2 py-0.5 rounded-lg text-ink"
        style={depth > 0 ? { paddingLeft: 8 + depth * 16 } : undefined}
      >
        <span aria-hidden />
        <input
          ref={inputRef}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={() => void commitRename()}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              void commitRename();
            } else if (e.key === "Escape") {
              e.preventDefault();
              onCancelRename();
            }
          }}
          className="min-w-0 w-full bg-transparent border-0 p-0 text-base font-medium tracking-[-0.005em] text-ink outline-none"
        />
      </div>
    );
  }

  return (
    <div
      role="button"
      tabIndex={tabbable ? 0 : -1}
      onClick={() => void switchSession(sessionId)}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          void switchSession(sessionId);
          return;
        }
        // Keyboard route to the context menu (its actions replace the
        // hover cluster, which is out of the Tab order). Anchored to the
        // row rect — a keyboard-triggered native contextmenu event would
        // report clientX/Y = 0.
        if (e.key === "ContextMenu" || (e.shiftKey && e.key === "F10")) {
          e.preventDefault();
          const r = e.currentTarget.getBoundingClientRect();
          onMenu({ x: r.left + 8, y: r.bottom });
        }
      }}
      onContextMenu={onContextMenu}
      onDoubleClick={(e) => {
        e.preventDefault();
        onStartRename();
      }}
      data-streaming={streaming ? "true" : undefined}
      data-active={active ? "true" : undefined}
      aria-current={active ? "true" : undefined}
      data-depth={depth || undefined}
      style={depth > 0 ? { paddingLeft: 8 + depth * 16 } : undefined}
      className="app-row session-row group/row relative grid grid-cols-[16px_minmax(0,1fr)] items-center gap-2 w-full px-2 py-0.5 rounded-lg text-ink-soft text-left"
    >
      <SessionStateIcon streaming={streaming} unread={unread} />
      {/* Title uses the full width at rest; on hover its right edge fades under
          the overlaid time + ⋯ cluster (mask is color-independent, so it works
          on any row background and needs no reserved gutter). */}
      <span title={name || "untitled"} className="min-w-0 truncate text-base font-medium tracking-[-0.005em] group-hover/row:[mask-image:linear-gradient(to_right,#000_calc(100%_-_6.25rem),transparent_calc(100%_-_4.5rem))]">
        {name || "untitled"}
      </span>
      <span
        className={`absolute right-2 top-0 bottom-0 flex items-center gap-1 group-hover/row:opacity-100 focus-within:opacity-100 transition-opacity duration-row ${deleting ? "opacity-100" : "opacity-0"}`}
      >
        <span className="text-xs tabular-nums text-faint">
          {formatRelativePast(lastActivity)}
        </span>
        {/* Inline countdown-archive — the destructive session gesture, on
            every row's hover instead of buried in a settings tab. Wrapper
            swallows the click so it doesn't also open the session. */}
        <span
          onClick={(e) => e.stopPropagation()}
          onMouseDown={(e) => e.stopPropagation()}
          className="contents"
        >
          <ConfirmDeleteButton
            size="sm"
            label="Archive session"
            tabIndex={-1}
            onConfirm={onArchive}
            onActiveChange={setDeleting}
          />
        </span>
        <button
          type="button"
          aria-label="Session actions"
          title="More"
          tabIndex={-1}
          onClick={(e) => {
            e.stopPropagation();
            const r = e.currentTarget.getBoundingClientRect();
            onMenu({ x: r.left, y: r.bottom + 4 });
          }}
          onMouseDown={(e) => e.stopPropagation()}
          className="grid place-items-center w-5 h-5 shrink-0 rounded-[5px] text-faint hover:text-ink hover:bg-surface-soft/70 transition-[background-color,color,scale] duration-check ease-out active:scale-[0.97]"
        >
          <MoreHorizontal size={ICON.SM} strokeWidth={2} />
        </button>
      </span>
    </div>
  );
}
