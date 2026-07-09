import { useEffect, useMemo, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { ArrowUpRight, ChevronDown, Folder, Inbox, Pin, Plus, Settings } from "lucide-react";
import clsx from "clsx";
import { MOTION, EASE_EMPHASIZED, EASE_OUT } from "@/lib/tokens/motion";
import { useStore } from "@/stores";
import { compactSessionApi } from "@/api/core";
import type { SessionListItem } from "@/api/types";
import { loadHistory } from "@/actions/history";
import { archiveSession, createSession, moveSessionToProject } from "@/actions/sessions";
import { ICON } from "@/lib/icons";
import { useTimeTicker } from "@/lib/hooks";
import { ScrollFadeBottom, ScrollFadeTop } from "@/components/ui/ScrollBlur";
import { EmptyState } from "@/components/ui/EmptyState";
import { groupSessions, primarySidebarSessions } from "@/features/sessions/lib/projects";
import { SessionRow } from "@/features/sessions/components/SessionRow";
import { SessionContextMenu, type ContextMenuState } from "@/features/sessions/components/SessionContextMenu";
import { ProjectSettingsModal } from "@/features/sessions/components/ProjectSettingsModal";
import { SidebarFilters } from "@/features/sessions/components/SidebarFilters";
import { RowAction } from "@/features/sessions/components/RowAction";

// Rows shown per group before the "Show more" toggle reveals the rest, in
// steps of REVEAL_STEP — a 40-chat group unfolds gradually instead of
// exploding. New project / search / archived live in ⌘K; filter & group-by
// live in the SidebarFilters popover.
const MAX_VISIBLE = 4;
const REVEAL_STEP = 5;

export function SessionList() {
  useTimeTicker();
  const projects = useStore((s) => s.projects);
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
  const setPref = useStore((s) => s.setPref);

  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [menu, setMenu] = useState<ContextMenuState | null>(null);
  const [editingProjectId, setEditingProjectId] = useState<string | null>(null);
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

  // Every group opens as a room (slices and projects are one container) —
  // the ↗ action is the sidebar's door into it.
  const openSlice = useStore((s) => s.openSlice);

  const pinnedSet = useMemo(() => new Set(pinnedSessionIds), [pinnedSessionIds]);
  const togglePin = (sessionId: string) => {
    const current = useStore.getState().prefs.pinnedSessionIds;
    const next = current.includes(sessionId)
      ? current.filter((id) => id !== sessionId)
      : [sessionId, ...current];
    setPref("pinnedSessionIds", next);
  };

  const chatSessions = useMemo(() => primarySidebarSessions(sessions), [sessions]);
  const activeSet = useMemo(
    () => new Set([...activeRunSessionIds, ...backgroundedRunSessionIds]),
    [activeRunSessionIds, backgroundedRunSessionIds],
  );
  const grouped = useMemo(
    () =>
      groupSessions(projects, chatSessions, {
        groupBy,
        unreadOnly,
        channelsOnly,
        pinned: pinnedSet,
        unread: unreadDoneSessionIds,
        active: activeSet,
      }),
    [projects, chatSessions, groupBy, unreadOnly, channelsOnly, pinnedSet, unreadDoneSessionIds, activeSet],
  );
  const editingProject = projects.find((project) => project.project_id === editingProjectId) ?? null;

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
      isChannel={session.session_type === "channel"}
      isAgent={false}
      depth={0}
      tabbable={session.session_id === tabbableId}
      renaming={renamingId === session.session_id}
      onStartRename={() => setRenamingId(session.session_id)}
      onCancelRename={() => setRenamingId(null)}
      onMenu={(pos) => setMenu({ sessionId: session.session_id, x: pos.x, y: pos.y })}
      onArchive={async () => {
        try {
          await archiveSession(session.session_id);
        } catch {
          useStore.getState().pushToast({
            id: `archive-fail:${session.session_id}`,
            title: "Couldn’t archive session",
            status: "failed",
            target: { kind: "session", sessionId: session.session_id },
          });
        }
      }}
      onContextMenu={(e) => {
        e.preventDefault();
        setMenu({ sessionId: session.session_id, x: e.clientX, y: e.clientY });
      }}
    />
    </div>
  );

  const hasFilter = unreadOnly || channelsOnly;

  return (
    <div className="group/sessions flex flex-col flex-1 min-h-0">
      {/* Zone label: the list is a named region (mirrors Home's SLICES
          strip label), so the nav above doesn't bleed into the groups. */}
      <div className="flex items-center justify-between pl-[18px] pr-2.5 pt-3 pb-1">
        <span className="text-xs font-semibold tracking-wide text-faint uppercase select-none">
          Slices
        </span>
        <SidebarFilters />
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto scroll-thin pb-3" onKeyDown={onListKeyDown}>
        <ScrollFadeTop />
        <ScrollFadeBottom />
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
          grouped.map((group, groupIndex) => {
            const isCollapsed = collapsedGroups.has(group.key);
            const revealed = revealedCounts.get(group.key) ?? 0;
            const visibleCount = Math.min(group.sessions.length, MAX_VISIBLE + revealed);
            const head = group.sessions.slice(0, MAX_VISIBLE);
            const rest = group.sessions.slice(MAX_VISIBLE, visibleCount);
            const remaining = group.sessions.length - visibleCount;
            return (
              /* Compact rows, air BETWEEN groups — the group gap is what
                 makes the zones scannable, not taller rows. */
              <div key={group.key} className={clsx(groupIndex > 0 && "mt-2")}>
                <div className="group/prow flex items-center gap-1 pr-[18px]">
                  <button
                    type="button"
                    onClick={() => toggleGroup(group.key)}
                    aria-expanded={!isCollapsed}
                    className="flex-1 flex items-center gap-2 min-w-0 pl-[18px] py-1 text-base font-medium text-muted hover:text-ink transition-colors select-none"
                  >
                    {/* Fixed 16px glyph slot — the same rail the rows' icon
                        column sits on, so header labels and chat titles share
                        one text edge (Codex-style tree without extra indent). */}
                    <span aria-hidden className="grid w-4 shrink-0 place-items-center text-faint">
                      {group.pinned ? (
                        <Pin size={ICON.LG} strokeWidth={2} />
                      ) : group.key === "inbox" ? (
                        <Inbox size={ICON.LG} strokeWidth={2} />
                      ) : group.project ? (
                        <Folder size={ICON.LG} strokeWidth={2} />
                      ) : null}
                    </span>
                    <span className="min-w-0 truncate">{group.label}</span>
                    <ChevronDown
                      size={ICON.XS}
                      strokeWidth={2.2}
                      className={clsx(
                        "shrink-0 text-faint opacity-0 group-hover/prow:opacity-100 transition-[opacity,transform] duration-row",
                        isCollapsed && "-rotate-90",
                      )}
                    />
                  </button>
                  {group.project && (
                    <div className="flex items-center gap-0.5 shrink-0 opacity-0 group-hover/prow:opacity-100 focus-within:opacity-100 transition-opacity duration-row">
                      <RowAction
                        icon={<ArrowUpRight size={ICON.SM} strokeWidth={2} />}
                        label={`Open the ${group.label} room`}
                        onClick={() => openSlice(group.project?.project_id ?? null)}
                      />
                      <RowAction
                        icon={<Settings size={ICON.SM} strokeWidth={2} />}
                        label={`Slice settings — ${group.project.name}`}
                        onClick={() => setEditingProjectId(group.project?.project_id ?? null)}
                      />
                      <RowAction
                        icon={<Plus size={ICON.SM} strokeWidth={2} />}
                        label={`New session in ${group.label}`}
                        onClick={() => void createSession(group.project?.project_id ?? null)}
                      />
                    </div>
                  )}
                </div>
                <AnimatePresence initial={false}>
                  {!isCollapsed && (
                    <motion.div
                      key="rows"
                      initial={{ opacity: 0, y: -4, filter: "blur(2px)" }}
                      animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
                      exit={{ opacity: 0, transition: { duration: MOTION.fast, ease: EASE_OUT } }}
                      transition={{ duration: MOTION.row, ease: EASE_OUT }}
                    >
                      {/* px-2.5 puts the rows' 16px icon column on the same
                          rail as the header glyph, so chat titles align with
                          the group name — hierarchy from the glyph, not a
                          second indent level. */}
                      <div role="list" aria-label={group.label} className="px-2.5 flex flex-col gap-0">
                        {head.map(renderRow)}
                        <AnimatePresence initial={false}>
                          {rest.length > 0 && (
                            <motion.div
                              key="more"
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
                            className="app-row grid grid-cols-[16px_minmax(0,1fr)] items-center gap-2 w-full px-2 py-0.5 rounded-lg text-faint hover:text-ink transition-colors"
                          >
                            {/* Empty icon column keeps the label in the title
                                column, flush with the session names above.
                                Words, not "…" — and long groups unfold
                                REVEAL_STEP at a time instead of exploding. */}
                            <span aria-hidden />
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

      <SessionContextMenu
        state={menu}
        onClose={closeMenu}
        isPinned={menu ? pinnedSet.has(menu.sessionId) : false}
        onTogglePin={() => {
          if (!menu) return;
          togglePin(menu.sessionId);
          closeMenu();
        }}
        onRename={() => {
          if (!menu) return;
          setRenamingId(menu.sessionId);
          closeMenu();
        }}
        onCompact={async () => {
          if (!menu) return;
          const sessionId = menu.sessionId;
          closeMenu();
          const cfg = useStore.getState().config;
          try {
            const result = await compactSessionApi(cfg, sessionId);
            if (result.status === "compacted" && useStore.getState().currentSessionId === sessionId) {
              await loadHistory(sessionId);
            }
          } catch {
            /* ignore */
          }
        }}
        onArchive={async () => {
          if (!menu) return;
          const sessionId = menu.sessionId;
          closeMenu();
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
        onMoveProject={async (projectId) => {
          if (!menu) return;
          const sessionId = menu.sessionId;
          closeMenu();
          try {
            await moveSessionToProject(sessionId, projectId);
          } catch {
            /* ignore */
          }
        }}
        projects={projects}
      />
      <ProjectSettingsModal project={editingProject} onClose={() => setEditingProjectId(null)} />
    </div>
  );
}
