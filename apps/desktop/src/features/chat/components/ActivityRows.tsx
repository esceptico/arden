import { useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import {
  ArrowUpRight,
  AiChat02,
  Bell,
  Brain01,
  CalendarDays,
  ChevronDown,
  Clock,
  Dot,
  File01,
  FilePlus2,
  Folder,
  Globe02,
  History,
  Image,
  LeftToRightListBullet,
  type ArdenIcon,
  Mail,
  MessageSquare,
  PencilEdit02,
  Search,
  Stop,
  Terminal,
  Wrench,
} from "@/components/icons";
import clsx from "clsx";
import type { ActivityItem } from "@/stores";
import { activityItemStatus, isAgent } from "@/lib/agent";
import { switchSession } from "@/actions/sessions";
import { cancelSubagent } from "@/actions/messages";
import { ICON } from "@/lib/icons";
import { Reveal } from "@/components/ui/Reveal";
import { ThinkingStep } from "@/components/ui/ThinkingStep";
import { Tooltip } from "@/components/ui/Tooltip";
import { callTitle, groupSummary, operationLabel, type StepIconKey } from "@/features/chat/lib/operationLabel";
import {
  agentRunFromActivityItem,
  agentRunStatusLabel,
  isActiveAgentStatus,
} from "@/lib/agentRun";
import { EASE_OUT, MOTION, STAGGER } from "@/lib/tokens/motion";
import { MAX_NEST_DEPTH, NEST_PX } from "@/features/chat/lib/trace";

type RowProps = {
  item: ActivityItem;
  onOpen: (item: ActivityItem) => void;
  last?: boolean;
};

const ICON_BY_KEY: Record<StepIconKey, ArdenIcon> = {
  search: Search,
  globe: Globe02,
  folder: Folder,
  file: File01,
  edit: PencilEdit02,
  "file-plus": FilePlus2,
  terminal: Terminal,
  brain: Brain01,
  list: LeftToRightListBullet,
  mail: Mail,
  slack: MessageSquare,
  calendar: CalendarDays,
  clock: Clock,
  bell: Bell,
  image: Image,
  wrench: Wrench,
  history: History,
  dot: Dot,
};

// Semantic step glyph: a category lucide icon (every tool resolves to one — the
// `dot` fallback is itself a lucide glyph so it shares the icons' 14px metrics
// and never looks mis-sized). Colour comes from the gutter (text-muted) via
// currentColor; errors tint red. Lives OUTSIDE the label so the running
// label-shimmer never masks it.
function StepGlyph({ iconKey, errored }: { iconKey: StepIconKey; errored: boolean }) {
  const Icon = ICON_BY_KEY[iconKey];
  return <Icon size={14} className={errored ? "text-bad" : undefined} />;
}

export function ItemButton({ item, onOpen, last }: RowProps) {
  const depth = Math.min(item.depth ?? 0, MAX_NEST_DEPTH);
  if (isAgent(item)) {
    return <AgentRow item={item} depth={depth} onOpen={onOpen} last={last} />;
  }
  const running = activityItemStatus(item) === "ongoing";
  const errored = !!item.error;
  const { verb, detail, iconKey } = operationLabel(item);

  return (
    <ThinkingStep
      node={<StepGlyph iconKey={iconKey} errored={errored} />}
      last={last}
      className="board-trace-row rounded-[var(--r-row)] px-0.5 py-1.5 transition-colors hover:bg-surface-soft/60"
      style={depth > 0 ? { paddingLeft: depth * NEST_PX } : undefined}
    >
      <button
        type="button"
        onClick={() => onOpen(item)}
        title={`${item.target || item.kind} — click to inspect`}
        className="flex w-full min-w-0 flex-col gap-1 border-0 bg-transparent p-0 text-left"
      >
        <span
          className={clsx(
            "truncate font-medium leading-tight",
            errored ? "text-bad" : running ? "text-accent" : "text-ink",
          )}
        >
          {verb}
          {running && "…"}
        </span>
        {detail && <span className="truncate text-sm text-muted leading-snug">{detail}</span>}
      </button>
    </ThinkingStep>
  );
}

// A collapsed run of consecutive same-kind calls (FF "Explored 4 pages"): one
// summary step ("Read 8 files") with a chevron that reveals the individual
// calls as muted, inspectable detail lines. Snap-reveal (not a height tween) —
// row lists must not grid-rows animate.
export function ToolGroupRow({
  items,
  onOpen,
  last,
}: {
  items: ActivityItem[];
  onOpen: (item: ActivityItem) => void;
  last?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const { verb, iconKey } = groupSummary(items);
  const running = items.some((it) => activityItemStatus(it) === "ongoing");
  const errored = items.some((it) => !!it.error);
  const depth = Math.min(items[0].depth ?? 0, MAX_NEST_DEPTH);
  return (
    <ThinkingStep
      node={<StepGlyph iconKey={iconKey} errored={errored} />}
      last={last}
      className="board-trace-row rounded-[var(--r-row)] px-0.5 py-1.5 transition-colors hover:bg-surface-soft/60"
      style={depth > 0 ? { paddingLeft: depth * NEST_PX } : undefined}
    >
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="flex w-full min-w-0 items-center gap-1.5 border-0 bg-transparent p-0 text-left"
      >
        <span
          className={clsx(
            "truncate font-medium leading-tight",
            errored ? "text-bad" : running ? "text-accent" : "text-ink",
          )}
        >
          {verb}
          {running && "…"}
        </span>
        <ChevronDown
          size={ICON.XS}
          className={clsx("shrink-0 text-faint transition-transform duration-trace ease-out", open && "rotate-180")}
          aria-hidden
        />
      </button>
      <AnimatePresence initial={false}>
        {open && (
          <Reveal key="children" className="board-trace-row__details mt-1 flex flex-col gap-0.5">
            {items.map((it, i) => {
              // Prefer the model's per-call title ("Read about.html"); else the
              // arg detail (the path/query). Each line cascades in (FF feel).
              const text = callTitle(it) ?? operationLabel(it).detail ?? it.target ?? it.kind;
              return (
                <motion.button
                  key={it.id}
                  type="button"
                  initial={{ opacity: 0, y: 3, filter: "blur(2px)" }}
                  animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
                  transition={{ duration: MOTION.row, ease: EASE_OUT, delay: STAGGER.traceDetail * i }}
                  onClick={() => onOpen(it)}
                  title={`${it.target || it.kind} — click to inspect`}
                  className="truncate border-0 bg-transparent p-0 text-left text-sm leading-snug text-muted hover:text-ink-soft"
                >
                  {text}
                </motion.button>
              );
            })}
          </Reveal>
        )}
      </AnimatePresence>
    </ThinkingStep>
  );
}

// One row treatment for every agent in the trace — session-backed sub-agents
// (clickable → open the child session) and inline tool-group agents alike. A Bot
// glyph (accent while running, muted when settled) is the timeline node and
// carries a stop-on-hover; the name + a faint inline progress/result line + the
// visible state read as one coherent step.
function AgentRow({
  item,
  depth,
  onOpen,
  last,
}: {
  item: ActivityItem;
  depth: number;
  onOpen: (item: ActivityItem) => void;
  last?: boolean;
}) {
  const run = agentRunFromActivityItem(item);
  const running = isActiveAgentStatus(run.status);
  const childSessionId = run.childSessionId;
  const canStop = item.taskStatus === "running" && !!item.runId && !item.cancelRequested;
  const detail = running ? run.progress : run.resultPreview;
  const terminalBad = run.status === "failed" || run.status === "cancelled";
  return (
    <ThinkingStep
      last={last}
      className="board-trace-row group/agent rounded-[var(--r-row)] px-0.5 py-1.5 transition-colors hover:bg-surface-soft/60"
      style={depth > 0 ? { paddingLeft: depth * NEST_PX } : undefined}
      node={
        <span className="relative grid h-4 w-4 place-items-center">
          <span
            aria-hidden
            className={clsx(
              "grid h-4 w-4 place-items-center rounded-[var(--r-icon)] transition-opacity duration-row ease-out",
              running ? "bg-accent-soft text-accent-strong" : "bg-surface-soft text-faint",
              canStop && "group-hover/agent:opacity-0",
            )}
          >
            <AiChat02 size={ICON.XS} />
          </span>
          {canStop && (
            <Tooltip label="Stop subagent" side="right">
              <button
                type="button"
                aria-label="Stop subagent"
                onClick={(event) => {
                  event.stopPropagation();
                  if (item.runId) void cancelSubagent(item.runId, item.id);
                }}
                className="absolute inset-0 grid place-items-center rounded-[var(--r-icon)] border-0 p-0 m-0 bg-surface-soft text-faint opacity-0 pointer-events-none transition-[opacity,color] duration-row ease-out group-hover/agent:pointer-events-auto group-hover/agent:opacity-100 hover:text-bad focus-visible:pointer-events-auto focus-visible:opacity-100"
              >
                <Stop size={ICON.XS} />
              </button>
            </Tooltip>
          )}
        </span>
      }
    >
      {/* Content column: name line + progress line, gap-1 to match tool rows. */}
      <span className="flex min-w-0 flex-col gap-1">
      {/* Name line: peer of a tool's label, with status/elapsed/cost trailing. */}
      <span className="flex min-w-0 items-center gap-2">
        <button
          type="button"
          onClick={() => {
            if (childSessionId) {
              void switchSession(childSessionId);
              return;
            }
            onOpen(item);
          }}
          title={childSessionId ? "Open agent session" : `${item.kind} — click to inspect`}
          data-child-session-id={childSessionId}
          className="flex min-w-0 items-center gap-1.5 text-left bg-transparent border-0 p-0 m-0"
        >
          <span
            className={clsx(
              "truncate font-medium leading-tight group-hover/agent:text-ink transition-colors duration-row ease-out",
              running ? "text-muted" : terminalBad ? "text-bad" : "text-ink",
            )}
          >
            {run.name}
          </span>
          {childSessionId && (
            <ArrowUpRight
              size={ICON.XS}
              className="shrink-0 text-faint opacity-0 transition-opacity duration-row ease-out group-hover/agent:opacity-100"
              aria-hidden
            />
          )}
        </button>
        <span className={clsx("arden-status", terminalBad && "text-bad")}>
          {agentRunStatusLabel(run.status)}
        </span>
        {run.elapsedLabel && (
          <span className="shrink-0 text-2xs tabular-nums text-faint">{run.elapsedLabel}</span>
        )}
        {item.usage && activityItemStatus(item) === "executed" && !detail && (
          <AgentUsageSuffix tokens={item.usage.total} cost={item.cost} />
        )}
      </span>
      {/* Progress / result line: the agent's "description". */}
      {detail && (
        <span className={clsx("min-w-0 truncate text-sm text-muted leading-snug", running && "italic")}>
          {detail}
        </span>
      )}
      </span>
    </ThinkingStep>
  );
}

/** Compact `· 4.2k · $0.03` suffix that hangs off a finished agent row.
 *  Renders only when the subagent reported usage (i.e. it actually ran
 *  LLM calls). Used by the activity trace and the ToolViewer's AgentBody. */
export function AgentUsageSuffix({ tokens, cost }: { tokens: number; cost?: number }) {
  if (tokens <= 0 && !cost) return null;
  const tk =
    tokens < 1000
      ? `${tokens}`
      : tokens < 10000
        ? `${(tokens / 1000).toFixed(1)}k`
        : `${Math.round(tokens / 1000)}k`;
  const ct = cost
    ? cost < 0.01
      ? `$${cost.toFixed(4)}`
      : `$${cost.toFixed(3)}`
    : null;
  return (
    <span className="text-faint tabular-nums shrink-0" aria-label="Subagent usage">
      · {tk}
      {ct && ` · ${ct}`}
    </span>
  );
}
