import { useEffect, useRef, useState } from "react";
import { Archive01, Pin } from "@/components/icons";
import { archiveSession, renameSession, switchSession, toggleSessionPin } from "@/actions/sessions";
import { formatRelativePast } from "@/lib/format";
import { ICON } from "@/lib/icons";
import { WorkspaceRailStatus } from "@/components/workspace/WorkspaceRail";
import type { ContextMenuPosition } from "@/components/ui/ContextMenu";

export function SessionRow({
  sessionId,
  name,
  lastActivity,
  active,
  streaming,
  unread,
  pinned = false,
  depth = 0,
  tabbable,
  renaming,
  onStartRename,
  onCancelRename,
  onMenu,
}: {
  sessionId: string;
  name: string | null;
  lastActivity: string;
  active: boolean;
  streaming: boolean;
  unread: boolean;
  pinned?: boolean;
  depth?: number;
  /** Roving tabindex: exactly one row per list is the Tab stop; arrows on
   *  the list container move focus between rows. */
  tabbable: boolean;
  renaming: boolean;
  onStartRename: () => void;
  onCancelRename: () => void;
  onMenu: (position: ContextMenuPosition) => void;
}) {
  const [draft, setDraft] = useState(name ?? "");
  const inputRef = useRef<HTMLInputElement>(null);
  const status = streaming
    ? { label: "live", tone: "live" as const }
    : unread
      ? { label: "done", tone: "done" as const }
      : { label: formatRelativePast(lastActivity), tone: "neutral" as const };

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
        className="workspace-rail__session workspace-rail__session--rename grid grid-cols-[minmax(0,1fr)] items-center gap-2 w-full px-2 py-0.5 text-ink"
        style={depth > 0 ? {
          paddingLeft: `calc(var(--interactive-row-inline-padding) + var(--workspace-rail-icon-lane) + var(--space-2) + ${depth * 16}px)`,
        } : undefined}
      >
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
          className="min-w-0 w-full bg-transparent border-0 p-0 text-base text-ink outline-none"
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
        // Keys targeting the hover-revealed archive button stay its own.
        if (e.target !== e.currentTarget) return;
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          void switchSession(sessionId);
          return;
        }
        // Keyboard route to the context menu. Anchor from the mock's row
        // geometry because native keyboard contextmenu events report 0,0.
        if (e.key === "ContextMenu" || (e.shiftKey && e.key === "F10")) {
          e.preventDefault();
          const r = e.currentTarget.getBoundingClientRect();
          onMenu({
            x: r.left + 12,
            y: r.bottom - 4,
            trigger: e.currentTarget,
            source: "keyboard",
          });
        }
      }}
      onContextMenu={(e) => {
        e.preventDefault();
        onMenu({
          x: e.clientX,
          y: e.clientY,
          trigger: e.currentTarget,
          source: "pointer",
        });
      }}
      onDoubleClick={(e) => {
        e.preventDefault();
        onStartRename();
      }}
      data-streaming={streaming ? "true" : undefined}
      data-active={active ? "true" : undefined}
      aria-current={active ? "page" : undefined}
      data-depth={depth || undefined}
      style={depth > 0 ? {
        paddingLeft: `calc(var(--interactive-row-inline-padding) + var(--workspace-rail-icon-lane) + var(--space-2) + ${depth * 16}px)`,
      } : undefined}
      className="workspace-rail__session arden-row app-row session-row group/row relative grid grid-cols-[minmax(0,1fr)_auto] items-center gap-2 w-full px-2 py-0.5 text-ink-soft text-left"
    >
      <span
        title={name || "untitled"}
        className="workspace-rail__session-title session-row-title min-w-0 truncate"
      >
        {name || "untitled"}
      </span>
      <span className="workspace-rail__session-meta">
        <WorkspaceRailStatus tone={status.tone}>{status.label}</WorkspaceRailStatus>
        <button
          type="button"
          tabIndex={-1}
          aria-label={pinned ? "Unpin chat" : "Pin chat"}
          title={pinned ? "Unpin" : "Pin"}
          data-active={pinned ? "true" : undefined}
          className="workspace-rail__session-action"
          onClick={(e) => {
            e.stopPropagation();
            toggleSessionPin(sessionId);
          }}
        >
          <Pin size={ICON.SM} />
        </button>
        <button
          type="button"
          tabIndex={-1}
          aria-label="Archive chat"
          title="Archive"
          className="workspace-rail__session-action"
          onClick={(e) => {
            e.stopPropagation();
            void archiveSession(sessionId);
          }}
        >
          <Archive01 size={ICON.SM} />
        </button>
      </span>
    </div>
  );
}
