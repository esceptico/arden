import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { AnimatePresence, motion } from "motion/react";
import clsx from "clsx";
import { ChevronDown } from "@/components/icons";
import type { Automation } from "@/api/types";
import { isChannelAutomation, isInternalAutomation } from "@/lib/automationFilters";
import { formatRelative, formatTrigger } from "@/lib/agentRun";
import { EASE_OUT, MOTION } from "@/lib/tokens/motion";
import { ICON } from "@/lib/icons";
import { Skeleton } from "@/components/ui/Skeleton";
import { ContextMenu, type ContextMenuEntry, type ContextMenuPosition } from "@/components/ui/ContextMenu";

interface AutomationContextMenuState extends ContextMenuPosition {
  automation: Automation;
}

/** Groups mirror the old tabs, flattened into one scannable column: the
 *  user's own automations, then the seeded per-area agents, then system
 *  builtins. */
export function groupAutomations(automations: Automation[]) {
  const user: Automation[] = [];
  const area: Automation[] = [];
  const system: Automation[] = [];
  for (const a of automations) {
    if (isInternalAutomation(a)) system.push(a);
    else if (a.task_id.startsWith("area:")) area.push(a);
    else user.push(a);
  }
  return { user, area, system };
}

export function automationStatusWord(automation: Automation): string {
  if (automation.running_since != null) return "running";
  if (!automation.enabled) return "paused";
  if (automation.last_status === "failed" || automation.last_status === "error") return "failed";
  if (automation.last_status === "cancelled") return "cancelled";
  if (automation.last_run_at) return "completed";
  return "never run";
}

function scheduleLabel(automation: Automation): string {
  const trigger = automation.triggers[0];
  if (isChannelAutomation(automation) || trigger?.type === "message") return "on message";
  if (trigger) return formatTrigger(trigger);
  if (automation.next_run_at) return `next ${formatRelative(automation.next_run_at)}`;
  return "No trigger";
}

function RailRow({
  automation,
  selected,
  onSelect,
  onMenu,
}: {
  automation: Automation;
  selected: boolean;
  onSelect: () => void;
  onMenu: (position: ContextMenuPosition) => void;
}) {
  const status = automationStatusWord(automation);
  return (
    <button
      type="button"
      onClick={onSelect}
      onContextMenu={(event) => {
        event.preventDefault();
        onMenu({
          x: event.clientX,
          y: event.clientY,
          trigger: event.currentTarget,
          source: "pointer",
        });
      }}
      onKeyDown={(event) => {
        if (event.key !== "ContextMenu" && !(event.shiftKey && event.key === "F10")) return;
        event.preventDefault();
        const rect = event.currentTarget.getBoundingClientRect();
        onMenu({
          x: rect.left + 12,
          y: rect.bottom - 4,
          trigger: event.currentTarget,
          source: "keyboard",
        });
      }}
      aria-selected={selected}
      data-state={status}
      className={clsx(
        "automation-rail__row",
        !automation.enabled && "automation-rail__row--paused",
      )}
    >
      <span className="automation-rail__identity">
        <b>{automation.name?.trim() || "Untitled"}</b>
        <small>{scheduleLabel(automation)}</small>
      </span>
      <span className="automation-rail__state">{status}</span>
    </button>
  );
}

function GroupLabel({
  label,
  count,
  collapsed,
  onToggle,
}: {
  label: ReactNode;
  count: number;
  collapsed: boolean;
  onToggle: () => void;
}) {
  return (
    <button
      type="button"
      aria-expanded={!collapsed}
      data-collapsed={collapsed ? "true" : undefined}
      className="automation-rail__group-label"
      onClick={onToggle}
    >
      <span>{label}</span>
      <span className="automation-rail__group-count">{count}</span>
      <ChevronDown size={ICON.XS} strokeWidth={2.2} />
    </button>
  );
}

export function AutomationRail({
  automations,
  selectedId,
  onSelect,
  onRunNow,
  onDuplicate,
  onToggle,
  query = "",
}: {
  automations: Automation[] | null;
  selectedId: string | null;
  onSelect: (id: string) => void;
  onRunNow: (automation: Automation) => void;
  onDuplicate: (automation: Automation) => void;
  onToggle: (automation: Automation) => void;
  query?: string;
}) {
  const [contextMenu, setContextMenu] = useState<AutomationContextMenuState | null>(null);
  // The user's own automations are the working set; the seeded per-area
  // agents and system builtins start folded, revealed via the group heading
  // (same interaction as the chat sidebar's groups).
  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(
    () => new Set(["Area agents", "System"]),
  );
  const groups = useMemo(
    () => (automations ? groupAutomations(automations) : null),
    [automations],
  );

  const toggleGroup = useCallback((label: string) => {
    setCollapsedGroups((prev) => {
      const next = new Set(prev);
      if (next.has(label)) next.delete(label);
      else next.add(label);
      return next;
    });
  }, []);

  // A selection must never sit inside a folded group (deep links, run-now
  // jumps): unfold its group when it lands there.
  useEffect(() => {
    if (!selectedId || !groups) return;
    const label = groups.system.some((a) => a.task_id === selectedId)
      ? "System"
      : groups.area.some((a) => a.task_id === selectedId)
        ? "Area agents"
        : null;
    if (!label) return;
    setCollapsedGroups((prev) => {
      if (!prev.has(label)) return prev;
      const next = new Set(prev);
      next.delete(label);
      return next;
    });
  }, [selectedId, groups]);

  const openContextMenu = useCallback((automation: Automation, position: ContextMenuPosition) => {
    setContextMenu({ automation, ...position });
  }, []);

  const contextEntries = useMemo<ContextMenuEntry[]>(() => {
    if (!contextMenu) return [];
    const { automation } = contextMenu;
    return [
      { id: "open", label: "Open", onSelect: () => onSelect(automation.task_id) },
      { id: "run-now", label: "Run now", onSelect: () => onRunNow(automation) },
      { id: "duplicate", label: "Duplicate", onSelect: () => onDuplicate(automation) },
      { id: "divider", type: "separator" },
      {
        id: "toggle",
        label: automation.enabled ? "Pause" : "Resume",
        onSelect: () => onToggle(automation),
      },
    ];
  }, [contextMenu, onDuplicate, onRunNow, onSelect, onToggle]);

  if (!groups) {
    return (
      <div className="automation-rail__loading" role="status" aria-label="Loading automations…">
        {Array.from({ length: 6 }).map((_, i) => (
          <Skeleton key={i} height={42} radius={8} />
        ))}
      </div>
    );
  }

  const sections: [string, Automation[]][] = [
    ["Yours", groups.user],
    ["Area agents", groups.area],
    ["System", groups.system],
  ];

  const normalizedQuery = query.trim().toLocaleLowerCase();
  const visibleSections = sections
    .map(([label, items]) => [
      label,
      normalizedQuery
        ? items.filter((automation) =>
            `${automation.name} ${automation.description ?? ""}`.toLocaleLowerCase().includes(normalizedQuery),
          )
        : items,
    ] as const)
    .filter(([, items]) => items.length > 0);

  return (
    <nav className="automation-rail__groups scroll-thin scroll-fade" aria-label="Automation list">
      {visibleSections.map(([label, items]) => {
        // An active search overrides folding — matches must be visible.
        const isCollapsed = !normalizedQuery && collapsedGroups.has(label);
        return (
          <section key={label}>
            <GroupLabel
              label={label}
              count={items.length}
              collapsed={isCollapsed}
              onToggle={() => toggleGroup(label)}
            />
            <AnimatePresence initial={false}>
              {!isCollapsed && (
                <motion.div
                  key="rows"
                  initial={{ opacity: 0, y: -4, filter: "blur(2px)" }}
                  animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
                  exit={{ opacity: 0, transition: { duration: MOTION.fast, ease: EASE_OUT } }}
                  transition={{ duration: MOTION.row, ease: EASE_OUT }}
                >
                  <div className="automation-rail__rows">
                    {items.map((a) => (
                      <RailRow
                        key={a.task_id}
                        automation={a}
                        selected={a.task_id === selectedId}
                        onSelect={() => onSelect(a.task_id)}
                        onMenu={(position) => openContextMenu(a, position)}
                      />
                    ))}
                  </div>
                </motion.div>
              )}
            </AnimatePresence>
          </section>
        );
      })}
      {visibleSections.length === 0 && (
        <p className="automation-rail__empty">
          {normalizedQuery ? "No automations match this search." : "No automations yet."}
        </p>
      )}
      <ContextMenu
        state={contextMenu}
        onClose={() => setContextMenu(null)}
        entries={contextEntries}
        ariaLabel="Automation actions"
      />
    </nav>
  );
}
