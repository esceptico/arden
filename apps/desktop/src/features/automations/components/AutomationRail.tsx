import { useMemo } from "react";
import clsx from "clsx";
import { X } from "lucide-react";
import { useStore } from "@/stores";
import type { Automation, AutomationSuggestion } from "@/api/types";
import { dismissSuggestion } from "@/actions/automations";
import { isChannelAutomation, isInternalAutomation } from "@/lib/automationFilters";
import { formatRelative } from "@/lib/agentRun";
import { Skeleton } from "@/components/ui/Skeleton";

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

function whenLabel(a: Automation): string {
  if (a.running_since != null) return "running";
  if (!a.enabled) return "paused";
  if (isChannelAutomation(a) || a.triggers[0]?.type === "message") return "on msg";
  if (a.next_run_at) return formatRelative(a.next_run_at);
  return "";
}

function RailRow({
  automation,
  selected,
  onSelect,
}: {
  automation: Automation;
  selected: boolean;
  onSelect: () => void;
}) {
  const running = automation.running_since != null;
  const paused = !automation.enabled;
  // Same row chassis as the chat sidebar's session rows (.app-row + py-0.5 +
  // text-base) — one list spacing across the app, not a parallel spec.
  return (
    <button
      type="button"
      onClick={onSelect}
      aria-selected={selected}
      className={clsx(
        "app-row w-full grid grid-cols-[minmax(0,1fr)_auto] items-center gap-2 px-2 py-0.5 rounded-lg text-left",
        paused ? "text-muted" : "text-ink-soft",
      )}
    >
      {/* No leading glyph: state reads through text — muted name + "paused",
          "running" in ink. Pause/enable lives in the detail footer. */}
      <span className="min-w-0 truncate text-base tracking-[-0.005em]">
        {automation.name?.trim() || "Untitled"}
      </span>
      <span
        className={clsx(
          "text-xs tabular-nums whitespace-nowrap",
          running ? "text-ink" : "text-faint",
        )}
      >
        {whenLabel(automation)}
      </span>
    </button>
  );
}

function GroupLabel({ children }: { children: string }) {
  // Mirrors the chat sidebar's section headers.
  return (
    <div className="px-2 pt-4 pb-1 text-xs font-semibold tracking-wide uppercase text-faint select-none">
      {children}
    </div>
  );
}

export function AutomationRail({
  automations,
  selectedId,
  onSelect,
  onPickSuggestion,
}: {
  automations: Automation[] | null;
  selectedId: string | null;
  onSelect: (id: string) => void;
  onPickSuggestion: (s: AutomationSuggestion) => void;
}) {
  const suggestions = useStore((st) => st.automationSuggestions);
  const groups = useMemo(
    () => (automations ? groupAutomations(automations) : null),
    [automations],
  );

  if (!groups) {
    return (
      <div className="grid gap-2 px-2 pt-4" role="status" aria-label="Loading automations…">
        {Array.from({ length: 6 }).map((_, i) => (
          <Skeleton key={i} height={36} radius={7} />
        ))}
      </div>
    );
  }

  const sections: [string, Automation[]][] = [
    ["Yours", groups.user],
    ["Area agents", groups.area],
    ["System", groups.system],
  ];

  return (
    <nav className="min-h-0 overflow-y-auto scroll-thin px-2 pb-2" aria-label="Automations">
      {sections.map(([label, items]) =>
        items.length === 0 ? null : (
          <section key={label}>
            <GroupLabel>{label}</GroupLabel>
            {items.map((a) => (
              <RailRow
                key={a.task_id}
                automation={a}
                selected={a.task_id === selectedId}
                onSelect={() => onSelect(a.task_id)}
              />
            ))}
          </section>
        ),
      )}
      {(suggestions?.length ?? 0) > 0 && (
        <section>
          <GroupLabel>Suggested</GroupLabel>
          {(suggestions ?? []).map((sug) => (
            <div
              key={sug.id}
              className="group/sug app-row grid grid-cols-[minmax(0,1fr)_auto] items-center gap-2 px-2 py-0.5 rounded-lg"
            >
              {/* Picking seeds a DRAFT — nothing is created until the user
                  hits Create in the detail pane. */}
              <button
                type="button"
                onClick={() => onPickSuggestion(sug)}
                title={sug.rationale}
                className="min-w-0 truncate text-left text-base tracking-[-0.005em] text-muted hover:text-ink transition-colors duration-check"
              >
                {sug.name}
              </button>
              <button
                type="button"
                aria-label="Dismiss suggestion"
                onClick={() => void dismissSuggestion(sug.id)}
                className="grid place-items-center w-5 h-5 rounded-[5px] text-faint opacity-0 group-hover/sug:opacity-100 focus-visible:opacity-100 transition-opacity hover:text-ink hover:bg-fill-hover"
              >
                <X size={11} strokeWidth={2} />
              </button>
            </div>
          ))}
        </section>
      )}
    </nav>
  );
}

/** "Next 24h" tape — every scheduled fire across ALL automations in the
 *  coming day as hollow marks on a dotted strip, with a now-cursor at the
 *  left. The rail's answer to "what is my system about to do". */
export function ScheduleTape({ automations }: { automations: Automation[] | null }) {
  const now = Date.now();
  const horizon = now + 24 * 3_600_000;
  const fires = useMemo(
    () =>
      (automations ?? [])
        .filter((a) => a.enabled && a.next_run_at)
        .map((a) => Date.parse(a.next_run_at as string))
        .filter((t) => Number.isFinite(t) && t > now && t <= horizon)
        .sort((a, b) => a - b),
    [automations, now, horizon],
  );

  const W = 256;
  const X = (t: number) => 8 + ((t - now) / (horizon - now)) * (W - 16);

  return (
    <div className="px-4 py-2.5 bg-fill-hover/60">
      <div className="flex items-baseline justify-between mb-1.5">
        <span className="text-2xs font-medium uppercase tracking-[0.08em] text-faint">
          Next 24h
        </span>
        <span className="font-mono tabular-nums text-2xs text-faint">
          {fires.length} {fires.length === 1 ? "run" : "runs"}
        </span>
      </div>
      <div className="relative h-[26px]">
        <svg
          className="absolute inset-0 w-full h-full overflow-visible"
          viewBox={`0 0 ${W} 26`}
          preserveAspectRatio="none"
          aria-hidden="true"
        >
          <line
            x1={0}
            y1={12}
            x2={W}
            y2={12}
            stroke="var(--color-whisper)"
            strokeWidth={1}
            strokeDasharray="1 4"
            vectorEffect="non-scaling-stroke"
          />
          <line
            x1={8}
            y1={6}
            x2={8}
            y2={18}
            stroke="var(--color-ink)"
            strokeWidth={1}
            vectorEffect="non-scaling-stroke"
          />
          <text
            x={8}
            y={25.5}
            textAnchor="middle"
            fontSize={7.5}
            className="font-mono"
            fill="var(--color-faint)"
          >
            now
          </text>
          {fires.map((t, i) => (
            <line
              key={i}
              x1={X(t)}
              y1={8}
              x2={X(t)}
              y2={16}
              stroke="var(--color-faint)"
              strokeWidth={1}
            />
          ))}
        </svg>
      </div>
    </div>
  );
}
