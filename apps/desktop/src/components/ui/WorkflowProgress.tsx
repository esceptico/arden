import clsx from "clsx";
import { ChevronRight, Stop, Workflow as WorkflowIcon } from "@/components/icons";
import { ICON } from "@/lib/icons";
import { useTimeTicker } from "@/lib/hooks";
import { formatDuration } from "@/lib/agentRun";
import { stopRun } from "@/actions/messages";
import {
  isActiveWorkflow,
  type Workflow,
  type WorkflowAgent,
  type WorkflowPhaseStatus,
} from "@/stores/workflow-domain";
import { Badge, type BadgeTone } from "@/components/ui/Badge";
import { BlurSwap } from "@/components/ui/BlurSwap";

// Shared workflow presentation. Both the activity-trace chip and the sidebar hub
// render WorkflowProgressCard; the overlay (WorkflowPanel) reuses the helpers, so
// a workflow reads as the same object at two zoom levels. Status is carried by
// glyph tone, badge, and segment color — no decorative rails.

export const WORKFLOW_BADGE_TONE: Record<Workflow["status"], BadgeTone> = {
  running: "accent",
  completed: "ok",
  failed: "bad",
  cancelled: "neutral",
};

export function phaseSegmentClass(status: WorkflowPhaseStatus): string {
  if (status === "completed") return "bg-ok";
  if (status === "failed") return "bg-bad";
  if (status === "running") return "bg-accent";
  return "bg-line"; // pending — bg-line (not surface-sunken) so segments stay visible on the surface-sunken card; line-soft == surface-sunken in light, so it must be bg-line
}

const TERMINAL_AGENT = new Set<WorkflowAgent["status"]>([
  "completed",
  "failed",
  "cancelled",
  "interrupted",
]);

export function countAgents(workflow: Workflow): number {
  let n = 0;
  for (const phase of Object.values(workflow.phasesByName)) {
    n += Object.keys(phase.agentsByTaskId).length;
  }
  return n;
}

function settledAgents(workflow: Workflow): number {
  let n = 0;
  for (const phase of Object.values(workflow.phasesByName)) {
    for (const agent of Object.values(phase.agentsByTaskId)) {
      if (TERMINAL_AGENT.has(agent.status)) n += 1;
    }
  }
  return n;
}

export function sumTokens(workflow: Workflow): number {
  let total = 0;
  for (const phase of Object.values(workflow.phasesByName)) {
    for (const agent of Object.values(phase.agentsByTaskId)) {
      total += agent.tokens?.total ?? 0;
    }
  }
  return total;
}

export function formatTokens(total: number): string {
  if (total < 1000) return `${total}`;
  if (total < 10_000) return `${(total / 1000).toFixed(1)}k`;
  if (total < 1_000_000) return `${Math.round(total / 1000)}k`;
  return `${(total / 1_000_000).toFixed(1)}M`;
}

export function sumCost(workflow: Workflow): number {
  let total = 0;
  for (const phase of Object.values(workflow.phasesByName)) {
    for (const agent of Object.values(phase.agentsByTaskId)) {
      total += agent.cost ?? 0;
    }
  }
  return total;
}

export function formatCost(cost: number): string {
  if (cost < 0.01) return "<$0.01";
  return `$${cost >= 100 ? Math.round(cost) : cost.toFixed(2)}`;
}

export function plural(n: number, word: string): string {
  return n === 1 ? word : `${word}s`;
}

export function PhaseSparkline({ agents }: { agents: WorkflowAgent[] }) {
  if (agents.length === 0) return null;
  const settled = agents.filter((agent) => TERMINAL_AGENT.has(agent.status)).length;
  return (
    <span
      className="shrink-0 text-2xs tabular-nums text-faint"
      aria-label={`${settled} of ${agents.length} agents settled`}
    >
      {settled}/{agents.length}
    </span>
  );
}

function statusWord(status: Workflow["status"]): string {
  return status === "cancelled" ? "stopped" : status;
}

// A compact inline progress card: identity + status + meta on line 1, a
// segmented phase bar (one segment per phase, colored by phase status) plus an
// agent-completion fraction on line 2. In the hub it expands in place (see
// `expanded`); from the chat it opens the hub focused on this workflow.
export function WorkflowProgressCard({
  workflow,
  onOpen,
  expanded,
}: {
  workflow: Workflow;
  onOpen: () => void;
  /** When provided, the chevron is an expand-in-place toggle (sidebar); when
   *  omitted, it's a static "open" affordance (chat trace). */
  expanded?: boolean;
}) {
  const running = isActiveWorkflow(workflow);
  // Tick while running so elapsed stays live; settled runs barely tick.
  useTimeTicker(running ? 1000 : 60_000);

  const phases = Object.values(workflow.phasesByName);
  const total = workflow.totalAgents || countAgents(workflow);
  const done = settledAgents(workflow);
  const durationMs = (workflow.completedAt ?? Date.now()) - workflow.startedAt;
  const elapsedLabel = durationMs > 0 ? formatDuration(durationMs) : "";
  const totalTokens = sumTokens(workflow);
  const totalCost = sumCost(workflow);

  // Stats live on line 2 with the progress bar so line 1 is pure identity and
  // the name keeps its room in the narrow (~268px) sidebar.
  const meta = [
    elapsedLabel || null,
    totalTokens > 0 ? `Σ ${formatTokens(totalTokens)}` : null,
    totalCost > 0 ? formatCost(totalCost) : null,
    total > 0 ? `${done}/${total}` : null,
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onOpen}
      onKeyDown={(e) => {
        if (e.target !== e.currentTarget) return;
        if (e.key === "Enter" || e.key === " ") {
          if (e.key === " ") e.preventDefault();
          onOpen();
        }
      }}
      title={workflow.description ?? `${workflow.name ?? "Workflow"} — open`}
      className="group/workflow flex w-full flex-col gap-1.5 rounded-md border border-line bg-surface-sunken px-2.5 py-2 text-left transition-[background-color,border-color,scale] duration-row ease-out hover:border-line-strong hover:bg-surface-soft active:scale-[0.985]"
    >
      <div className="flex min-w-0 items-center gap-2">
        <span
          aria-hidden
          className={clsx(
            "grid place-items-center w-5 h-5 shrink-0 rounded-md transition-colors duration-trace ease-out",
            running ? "bg-accent-soft text-accent-strong" : "bg-surface-sunken text-faint",
          )}
        >
          <WorkflowIcon size={ICON.SM} strokeWidth={2} />
        </span>
        <span
          className={clsx(
            "min-w-0 flex-1 truncate font-medium transition-colors duration-trace ease-out",
            running ? "text-ink" : workflow.status === "failed" ? "text-bad" : "text-muted",
          )}
        >
          {workflow.name ?? "Workflow"}
        </span>
        <Badge
          size="sm"
          tone={WORKFLOW_BADGE_TONE[workflow.status]}
          className="transition-colors duration-trace ease-out"
        >
          <BlurSwap swapKey={workflow.status} blur={2}>
            {statusWord(workflow.status)}
          </BlurSwap>
        </Badge>
        {running && (
          // Stops the parent run — the workflow is awaited by it, so this is
          // the kill switch for a runaway fan-out. stopPropagation keeps the
          // card's open handler from firing too.
          <button
            type="button"
            title="Stop run"
            aria-label="Stop run"
            onClick={(e) => {
              e.stopPropagation();
              void stopRun();
            }}
            className="grid place-items-center w-5 h-5 shrink-0 rounded text-faint transition-[background-color,color,scale] duration-check ease-out hover:bg-bad-soft hover:text-bad active:scale-[0.97]"
          >
            <Stop size={ICON.XS} />
          </button>
        )}
        <ChevronRight
          size={ICON.XS}
          strokeWidth={2}
          className={clsx(
            "shrink-0 text-faint transition-[rotate,color] duration-row ease-out group-hover/workflow:text-muted",
            expanded && "rotate-90 text-muted", // expanded-in-place (sidebar) → points down
          )}
          aria-hidden
        />
      </div>
      {(phases.length > 0 || meta) && (
        <div className="grid gap-1">
          <div className="flex items-center gap-2.5">
            {phases.length > 0 ? (
              <span className="flex flex-1 items-center gap-[2px]" aria-hidden>
                {phases.map((phase) => (
                  <span key={phase.name} className="relative flex-1 min-w-[2px]">
                    {phase.status === "running" && phases.length > 1 && (
                      <span className="absolute bottom-full mb-0.5 left-0 text-2xs text-faint truncate whitespace-nowrap">
                        <BlurSwap swapKey={phase.name} blur={2}>{phase.name}</BlurSwap>
                      </span>
                    )}
                    <span
                      className={clsx(
                        "block h-[3px] w-full rounded-[var(--r-control)] transition-colors duration-trace ease-out",
                        phaseSegmentClass(phase.status),
                      )}
                    />
                  </span>
                ))}
              </span>
            ) : (
              <span className="flex-1" />
            )}
            {meta && <span className="shrink-0 text-2xs tabular-nums text-muted">{meta}</span>}
          </div>
        </div>
      )}
    </div>
  );
}
