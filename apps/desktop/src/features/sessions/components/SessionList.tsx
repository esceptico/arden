import { useEffect, useMemo, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { ChevronDown, Folder, Inbox, Pin, Plus } from "@/components/icons";
import { MOTION, EASE_EMPHASIZED, EASE_OUT } from "@/lib/tokens/motion";
import { useStore } from "@/stores";
import type { SessionListItem } from "@/api/types";
import { archiveArea, archiveSession, createSession, moveSessionToArea, switchSession, toggleSessionPin } from "@/actions/sessions";
import { ICON } from "@/lib/icons";
import { useTimeTicker } from "@/lib/hooks";
import { EmptyState } from "@/components/ui/EmptyState";
import { groupSessions, primarySidebarSessions } from "@/features/sessions/lib/areas";
import { IconButton } from "@/components/ui/IconButton";
import { SessionRow } from "@/features/sessions/components/SessionRow";
import { SessionContextMenu, type ContextMenuState } from "@/features/sessions/components/SessionContextMenu";
import { ContextMenu, type ContextMenuPosition } from "@/components/ui/ContextMenu";
import { SidebarFilters } from "@/features/sessions/components/SidebarFilters";
import { WorkspaceRailSectionHeader } from "@/components/workspace/WorkspaceRail";

// Rows shown per group before the "Show more" toggle reveals the rest, in
// steps of REVEAL_STEP — a 40-chat group unfolds gradually instead of
// exploding. New area / search / archived live in ⌘K; filter & group-by
// live in the SidebarFilters popover.
const MAX_VISIBLE = 4;
const REVEAL_STEP = 5;

export function SessionList() {
  useTimeTicker();
  const recordsById = useStore((s) => s.areas.recordsById);
  const recordOrder = useStore((s) => s.areas.recordOrder);
  const areas = useMemo(
    () => recordOrder.flatMap((id) => (recordsById[id] ? [recordsById[id]] : [])),
    [recordOrder, recordsById],
  );
  const sessions = useStore((s) => s.sessions);
  const currentSessionId = useStore((s) => s.currentSessionId);
  const activeRunSessionIds = useStore((s) => s.activeRunSessionIds);
  const backgroundedRunSessionIds = useStore((s) => s.backgroundedRunSessionIds);
  const unreadDoneSessionIds = useStore((s) => s.unreadDoneSessionIds);
  const connected = useStore((s) => s.connected);
  const groupBy = useStore((s) => s.prefs.sidebarGroupBy);
  const unreadOnly = useStore((s) => s.prefs.sidebarUnreadOnly);
  const channelsOnly = useStore((s) => s.prefs.sidebarChannelsOnly);
  const pinnedSessionIds = useStore((s) => s.prefs.pinnedSessionIds);

  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [menu, setMenu] = useState<ContextMenuState | null>(null);
  const [areaMenu, setAreaMenu] = useState<(ContextMenuPosition & { areaId: string; label: string }) | null>(null);
  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(new Set());
  // Per-group count of rows revealed past MAX_VISIBLE (in REVEAL_STEP
  // increments); absent key = collapsed to the cap.
  const [revealedCounts, setRevealedCounts] = useState<Map<string, number>>(new Map());

  const toggleSet = (set: Set<string>, key: string) => {
    const next = new Set(set);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    return next;
  };
  const toggleGroup = (key: string) => setCollapsedGroups((prev) => toggleSet(prev, key));
  const revealMore = (key: string) =>
    setRevealedCounts((prev) => new Map(prev).set(key, (prev.get(key) ?? 0) + REVEAL_STEP));
  const revealReset = (key: string) =>
    setRevealedCounts((prev) => {
      const next = new Map(prev);
      next.delete(key);
      return next;
    });

  const pinnedSet = useMemo(() => new Set(pinnedSessionIds), [pinnedSessionIds]);

  const chatSessions = useMemo(() => primarySidebarSessions(sessions), [sessions]);
  const activeSet = useMemo(
    () => new Set([...activeRunSessionIds, ...backgroundedRunSessionIds]),
    [activeRunSessionIds, backgroundedRunSessionIds],
  );
  const grouped = useMemo(
    () =>
      groupSessions(areas, chatSessions, {
        groupBy,
        unreadOnly,
        channelsOnly,
        pinned: pinnedSet,
        unread: unreadDoneSessionIds,
        active: activeSet,
      }),
    [areas, chatSessions, groupBy, unreadOnly, channelsOnly, pinnedSet, unreadDoneSessionIds, activeSet],
  );
  // Drop reveal state for groups that no longer overflow, so a group that
  // shrinks below the cap and later grows back doesn't silently auto-expand.
  useEffect(() => {
    setRevealedCounts((prev) => {
      if (prev.size === 0) return prev;
      const overflowing = new Set(
        grouped.filter((g) => g.sessions.length > MAX_VISIBLE).map((g) => g.key),
      );
      const next = new Map([...prev].filter(([k]) => overflowing.has(k)));
      return next.size === prev.size ? prev : next;
    });
  }, [grouped]);

  const closeMenu = () => setMenu(null);

  // Roving tabindex over the rendered rows: the whole list is ONE Tab stop
  // (the active session when visible, else the first row); arrows move
  // between rows. Mirrors radioGroupKeyDown's DOM-order query so collapsed
  // groups, filters, and the "…" cap never need index bookkeeping.
  const renderedSessionIds = useMemo(() => {
    const ids: string[] = [];
    for (const group of grouped) {
      if (collapsedGroups.has(group.key)) continue;
      const visible = group.sessions.slice(0, MAX_VISIBLE + (revealedCounts.get(group.key) ?? 0));
      for (const session of visible) ids.push(session.session_id);
    }
    return ids;
  }, [grouped, collapsedGroups, revealedCounts]);
  const tabbableId =
    currentSessionId && renderedSessionIds.includes(currentSessionId)
      ? currentSessionId
      : (renderedSessionIds[0] ?? null);

  const onListKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(e.key)) return;
    const rows = Array.from(e.currentTarget.querySelectorAll<HTMLElement>(".session-row"));
    if (rows.length === 0) return;
    // Only when focus is ON a row — arrows inside the rename input or on
    // group headers keep their native behavior.
    const idx = rows.indexOf(e.target as HTMLElement);
    if (idx === -1) return;
    e.preventDefault();
    const next =
      e.key === "Home" ? 0
      : e.key === "End" ? rows.length - 1
      : e.key === "ArrowDown" ? (idx + 1) % rows.length
      : (idx - 1 + rows.length) % rows.length;
    rows[next].focus();
  };

  const renderRow = (session: SessionListItem) => (
    <div key={session.session_id} role="listitem">
    <SessionRow
      sessionId={session.session_id}
      name={session.name ?? null}
      lastActivity={session.last_activity}
      active={session.session_id === currentSessionId}
      streaming={
        activeRunSessionIds.has(session.session_id) ||
        backgroundedRunSessionIds.has(session.session_id)
      }
      unread={unreadDoneSessionIds.has(session.session_id)}
      pinned={pinnedSet.has(session.session_id)}
      depth={0}
      tabbable={session.session_id === tabbableId}
      renaming={renamingId === session.session_id}
      onStartRename={() => setRenamingId(session.session_id)}
      onCancelRename={() => setRenamingId(null)}
      onMenu={(position) => setMenu({ sessionId: session.session_id, ...position })}
    />
    </div>
  );

  const hasFilter = unreadOnly || channelsOnly;

  return (
    <div className="workspace-rail__sessions group/sessions flex flex-col flex-1 min-h-0">
      <WorkspaceRailSectionHeader title="Areas" actions={<SidebarFilters />} />

      <div
        className="workspace-rail__session-list flex-1 min-h-0 overflow-y-auto scroll-thin scroll-fade"
        onKeyDown={onListKeyDown}
      >
        {grouped.length === 0 ? (
          <motion.div
            initial={{ opacity: 0, y: 4 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: MOTION.panel, ease: EASE_EMPHASIZED }}
            className="mt-10"
          >
            <EmptyState size="sm" icon={Inbox}>
              {!connected
                ? "Connect to load sessions."
                : hasFilter
                  ? "No sessions match this filter."
                  : "No sessions yet."}
            </EmptyState>
          </motion.div>
        ) : (
          grouped.map((group) => {
            const isCollapsed = collapsedGroups.has(group.key);
            const revealed = revealedCounts.get(group.key) ?? 0;
            const visibleCount = Math.min(group.sessions.length, MAX_VISIBLE + revealed);
            const head = group.sessions.slice(0, MAX_VISIBLE);
            const rest = group.sessions.slice(MAX_VISIBLE, visibleCount);
            const remaining = group.sessions.length - visibleCount;
            return (
              /* Compact rows, air BETWEEN groups — the group gap is what
                 makes the zones scannable, not taller rows. */
              <div
                key={group.key}
                className="workspace-rail__group"
              >
                {/* The + is a positioned SIBLING of the toggle (a button
                    cannot nest a button), hover/focus-revealed like the
                    session rows' action cluster. */}
                <div className="group/heading relative">
                  <button
                    type="button"
                    onClick={() => toggleGroup(group.key)}
                    onContextMenu={group.area ? (event) => {
                      event.preventDefault();
                      setAreaMenu({
                        areaId: group.area!.area_id,
                        label: group.label,
                        x: event.clientX,
                        y: event.clientY,
                        trigger: event.currentTarget,
                        source: "pointer",
                      });
                    } : undefined}
                    onKeyDown={group.area ? (event) => {
                      if (event.key !== "ContextMenu" && !(event.shiftKey && event.key === "F10")) return;
                      event.preventDefault();
                      const rect = event.currentTarget.getBoundingClientRect();
                      setAreaMenu({
                        areaId: group.area!.area_id,
                        label: group.label,
                        x: rect.left + 12,
                        y: rect.bottom - 4,
                        trigger: event.currentTarget,
                        source: "keyboard",
                      });
                    } : undefined}
                    aria-expanded={!isCollapsed}
                    data-collapsed={isCollapsed ? "true" : undefined}
                    className="workspace-rail__group-heading arden-row"
                  >
                    <span aria-hidden>
                      {group.pinned ? (
                        <Pin size={ICON.MD} strokeWidth={2} />
                      ) : group.key === "inbox" ? (
                        <Inbox size={ICON.MD} strokeWidth={2} />
                      ) : group.area ? (
                        <Folder size={ICON.MD} strokeWidth={2} />
                      ) : null}
                    </span>
                    <span>{group.label}</span>
                    <ChevronDown size={ICON.XS} strokeWidth={2.2} />
                  </button>
                  {!group.pinned && (
                    <IconButton
                      size="xs"
                      tone="faint"
                      aria-label={`New chat in ${group.label}`}
                      title={`New chat in ${group.label}`}
                      onClick={() => void createSession(group.area?.area_id ?? null)}
                      className="!absolute top-1/2 right-7 -translate-y-1/2 opacity-0 transition-opacity duration-check ease-out group-hover/heading:opacity-100 focus-visible:opacity-100"
                    >
                      <Plus size={ICON.XS} />
                    </IconButton>
                  )}
                </div>
                <AnimatePresence initial={false}>
                  {!isCollapsed && (
                    <motion.div
                      key="rows"
                      className="workspace-rail__session-body"
                      initial={{ opacity: 0, y: -4, filter: "blur(2px)" }}
                      animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
                      exit={{ opacity: 0, transition: { duration: MOTION.fast, ease: EASE_OUT } }}
                      transition={{ duration: MOTION.row, ease: EASE_OUT }}
                    >
                      <div
                        role="list"
                        aria-label={group.label}
                        className="workspace-rail__session-group"
                      >
                        {head.map(renderRow)}
                        <AnimatePresence initial={false}>
                          {rest.length > 0 && (
                            <motion.div
                              key="more"
                              className="workspace-rail__session-rest"
                              initial={{ opacity: 0, y: -4, filter: "blur(2px)" }}
                              animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
                              exit={{ opacity: 0, transition: { duration: MOTION.fast, ease: EASE_OUT } }}
                              transition={{ duration: MOTION.row, ease: EASE_OUT }}
                            >
                              {rest.map(renderRow)}
                            </motion.div>
                          )}
                        </AnimatePresence>
                        {group.sessions.length > MAX_VISIBLE && (
                          <button
                            type="button"
                            onClick={() =>
                              remaining > 0 ? revealMore(group.key) : revealReset(group.key)
                            }
                            aria-expanded={revealed > 0}
                            aria-label={
                              remaining > 0
                                ? `Show ${Math.min(REVEAL_STEP, remaining)} more session${remaining === 1 ? "" : "s"}`
                                : "Show fewer sessions"
                            }
                            className="workspace-rail__show-more arden-row app-row grid grid-cols-[16px_minmax(0,1fr)] items-center gap-2 w-full px-2 py-0.5 text-faint hover:text-ink transition-colors"
                          >
                            <span className="text-left text-base">
                              {remaining > 0 ? `Show ${Math.min(REVEAL_STEP, remaining)} more` : "Show less"}
                            </span>
                          </button>
                        )}
                      </div>
                    </motion.div>
                  )}
                </AnimatePresence>
              </div>
            );
          })
        )}
      </div>

      <ContextMenu
        state={areaMenu}
        onClose={() => setAreaMenu(null)}
        ariaLabel="Area actions"
        entries={areaMenu ? [
          {
            id: "open-area",
            label: "Open area",
            onSelect: () => useStore.getState().openArea(areaMenu.areaId),
          },
          {
            id: "new-chat",
            label: "New chat",
            onSelect: () => void createSession(areaMenu.areaId),
          },
          {
            id: "set-aside",
            label: "Set aside",
            onSelect: () => void archiveArea(areaMenu.areaId),
          },
        ] : []}
      />

      <SessionContextMenu
        state={menu}
        areas={areas}
        currentAreaId={
          menu ? (chatSessions.find((x) => x.session_id === menu.sessionId)?.area_id ?? null) : null
        }
        pinned={menu ? pinnedSet.has(menu.sessionId) : false}
        onTogglePin={() => {
          if (!menu) return;
          toggleSessionPin(menu.sessionId);
        }}
        onMove={(areaId) => {
          if (!menu) return;
          void moveSessionToArea(menu.sessionId, areaId);
        }}
        onClose={closeMenu}
        onOpen={() => {
          if (!menu) return;
          void switchSession(menu.sessionId);
        }}
        onRename={() => {
          if (!menu) return;
          setRenamingId(menu.sessionId);
        }}
        onArchive={async () => {
          if (!menu) return;
          const sessionId = menu.sessionId;
          try {
            await archiveSession(sessionId);
          } catch {
            useStore.getState().pushToast({
              id: `archive-fail:${sessionId}`,
              title: "Couldn’t archive session",
              status: "failed",
              target: { kind: "session", sessionId },
            });
          }
        }}
      />
    </div>
  );
}
