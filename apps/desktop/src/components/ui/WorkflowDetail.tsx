import { useMemo, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import clsx from "clsx";
import { ChevronDown, Code2 } from "@/components/icons";
import { ICON } from "@/lib/icons";
import { DISSOLVE_OUT, EASE_OUT, MOTION, RISE_IN, RISE_SETTLED } from "@/lib/tokens/motion";
import { useTimeTicker, useTimeoutFlag } from "@/lib/hooks";
import { copyText } from "@/lib/clipboard";
import { formatDuration } from "@/lib/agentRun";
import { switchSession } from "@/actions/sessions";
import { useStore } from "@/stores";
import { highlight } from "@/lib/highlight";
import { BlurSwap } from "@/components/ui/BlurSwap";
import { CopyGlyph } from "@/components/ui/CopyGlyph";
import { isActiveWorkflow, type Workflow, type WorkflowAgent, type WorkflowPhase } from "@/stores/workflow-domain";
import { formatTokens, PhaseSparkline, WorkflowProgressCard } from "@/components/ui/WorkflowProgress";

// A workflow card that expands IN PLACE to reveal its phases → agents — used in
// both the chat trace and the sidebar hub, each with its own local expand state.
// Clicking an agent opens its session (live tool calls).
export function ExpandableWorkflowCard({ workflow }: { workflow: Workflow }) {
  const [expanded, setExpanded] = useState(false);
  return (
    <>
      <WorkflowProgressCard
        workflow={workflow}
        expanded={expanded}
        onOpen={() => setExpanded((v) => !v)}
      />
      <AnimatePresence initial={false}>
        {expanded && (
          <motion.div
            key="detail"
            initial={{ ...RISE_IN, y: -4 }}
            animate={RISE_SETTLED}
            exit={{ opacity: 0, scale: 0.97, transition: { duration: MOTION.fast, ease: EASE_OUT } }}
            transition={{ duration: MOTION.row, ease: EASE_OUT }}
          >
            <WorkflowDetail workflow={workflow} onOpenAgent={(sid) => void switchSession(sid)} />
            <WorkflowSource parentToolCallId={workflow.parentToolCallId} />
          </motion.div>
        )}
      </AnimatePresence>
    </>
  );
}

// The Python script the agent authored for this dynamic workflow lives in the
// `workflow` tool call's args — not in the streamed domain events — so we resolve
// it from the matching activity item by the workflow's parentToolCallId. Same
// lookup the ToolViewer uses; works in both the trace and the sidebar since both
// render through ExpandableWorkflowCard. Returns the raw args string (a value-
// stable primitive, so Zustand won't re-render on unrelated updates) for the
// caller to parse.
function useWorkflowArgs(parentToolCallId: string | undefined): string | undefined {
  return useStore((s) => {
    if (!parentToolCallId) return undefined;
    for (const msg of s.messages.values()) {
      if (!msg.activity) continue;
      const found = msg.activity.items.find((it) => it.id === parentToolCallId);
      if (found) return found.args;
    }
    return undefined;
  });
}

function parseScript(args: string | undefined): string | null {
  if (!args) return null;
  try {
    const parsed = JSON.parse(args);
    if (typeof parsed?.script === "string" && parsed.script.trim()) return parsed.script;
  } catch {
    // args still streaming / not yet valid JSON — no source to show yet.
  }
  return null;
}

const SOURCE_PRE_CLASS =
  "hljs mt-1 mx-1.5 mb-0.5 p-2.5 rounded-lg bg-code-bg border border-line-soft text-xs leading-[1.55] " +
  "text-ink-soft font-mono whitespace-pre max-h-[40vh] min-w-0 max-w-full overflow-auto scroll-thin";

// A collapsible "Source" disclosure that reveals the exact Python the agent
// authored for this workflow run, syntax-highlighted. Only mounts while the card
// is expanded, so the store scan in useWorkflowArgs is bounded to open cards.
function WorkflowSource({ parentToolCallId }: { parentToolCallId?: string }) {
  const args = useWorkflowArgs(parentToolCallId);
  const script = useMemo(() => parseScript(args), [args]);
  const html = useMemo(() => (script ? highlight(script, "python") : ""), [script]);
  const [open, setOpen] = useState(false);
  const [copied, flashCopied] = useTimeoutFlag(1200);
  if (!script) return null;

  const onCopy = async () => {
    if (await copyText(script)) flashCopied();
  };

  return (
    <div className="mt-1">
      <div className="flex items-center gap-1.5 px-1.5">
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          className="group/src flex min-w-0 flex-1 items-center gap-1.5 py-0.5 text-left bg-transparent border-0 rounded hover:bg-surface-soft transition-[background-color,scale] duration-row ease-out active:scale-[0.985]"
        >
          <ChevronDown
            size={ICON.XS}
            strokeWidth={2}
            className={clsx("shrink-0 text-faint transition-transform duration-row", !open && "-rotate-90")}
          />
          <Code2 size={ICON.XS} strokeWidth={2} className="shrink-0 text-faint" />
          <span className="shrink-0 text-2xs font-medium text-ink">Source</span>
        </button>
        {open && (
          <motion.button
            type="button"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ duration: MOTION.check, ease: EASE_OUT }}
            onClick={() => void onCopy()}
            aria-label={copied ? "Copied" : "Copy script"}
            className={clsx(
              "shrink-0 inline-flex items-center gap-1 h-5 px-1.5 rounded-md text-2xs font-medium transition-[background-color,color,scale] duration-row ease-out active:scale-[0.97]",
              copied ? "text-accent-strong bg-accent-soft" : "text-faint hover:bg-surface-soft hover:text-ink",
            )}
          >
            <CopyGlyph copied={copied} size={ICON.XS} />
            <BlurSwap swapKey={copied ? "copied" : "copy"} blur={3}>
              {copied ? "Copied" : "Copy"}
            </BlurSwap>
          </motion.button>
        )}
      </div>
      {/* The highlighted pre can be 40vh of code — never height-tween it.
          The layout snaps at mount/after-exit; only the content fades. */}
      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            initial={{ opacity: 0, filter: "blur(2px)" }}
            animate={{ opacity: 1, filter: "blur(0px)" }}
            exit={{ opacity: 0, filter: "blur(2px)", transition: { duration: MOTION.fast, ease: EASE_OUT } }}
            transition={{ duration: MOTION.row, ease: EASE_OUT }}
          >
            {html ? (
              <pre className={SOURCE_PRE_CLASS} dangerouslySetInnerHTML={{ __html: html }} />
            ) : (
              <pre className={SOURCE_PRE_CLASS}>{script}</pre>
            )}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

// The workflow's phases → agents, compact enough for the ~300px sidebar (the
// hub). Replaces the old overlay modal: expand a workflow card in place to see
// its agents; click an agent to open its session and watch its tool calls live.
// Per-agent tokens + elapsed only — the full per-tool detail lives in that
// agent's own session.

export function WorkflowDetail({
  workflow,
  onOpenAgent,
}: {
  workflow: Workflow;
  onOpenAgent: (childSessionId: string) => void;
}) {
  const phases = Object.values(workflow.phasesByName);
  // The why of a settled run (the failure reason, "stopped by user") only
  // travels in the finished event's summary — without this line it's invisible
  // outside the tool result.
  const summary = !isActiveWorkflow(workflow) && workflow.summary ? (
    <div
      className={clsx(
        "px-2 py-1 text-2xs break-words",
        workflow.status === "failed" ? "text-bad" : "text-muted",
      )}
    >
      {workflow.summary}
    </div>
  ) : null;

  if (phases.length === 0) {
    return (
      <div className="py-1.5">
        <div className="px-2 text-2xs text-muted">
          {isActiveWorkflow(workflow) ? "Spinning up agents…" : "No agents ran."}
        </div>
        {summary}
      </div>
    );
  }

  return (
    <div className="mt-0.5 space-y-1">
      {summary}
      {phases.map((phase) => (
        <PhaseGroup key={phase.name} phase={phase} onOpenAgent={onOpenAgent} />
      ))}
    </div>
  );
}

function PhaseGroup({
  phase,
  onOpenAgent,
}: {
  phase: WorkflowPhase;
  onOpenAgent: (childSessionId: string) => void;
}) {
  const agents = Object.values(phase.agentsByTaskId);
  // Default OPEN: the user expanded the card specifically to see the subagents.
  // The phase chevron is then a further collapse, not a gate.
  const [expanded, setExpanded] = useState(true);

  return (
    <div>
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="group/phase flex w-full items-center gap-1.5 px-1.5 py-0.5 text-left bg-transparent border-0 rounded hover:bg-surface-soft transition-[background-color,scale] duration-row ease-out active:scale-[0.985]"
      >
        <ChevronDown
          size={ICON.XS}
          strokeWidth={2}
          className={clsx("shrink-0 text-faint transition-transform duration-row", !expanded && "-rotate-90")}
        />
        <span className="shrink-0 text-2xs font-medium text-ink">{phase.name}</span>
        <PhaseSparkline agents={agents} />
        <span className="flex-1" />
        <span className="shrink-0 text-2xs tabular-nums text-faint">{agents.length}</span>
      </button>
      {/* Unbounded agent list — snap the height, rise the content. */}
      <AnimatePresence initial={false}>
        {expanded && (
          <motion.div
            initial={{ ...RISE_IN, y: -4 }}
            animate={RISE_SETTLED}
            exit={{ ...DISSOLVE_OUT, transition: { duration: MOTION.fast, ease: EASE_OUT } }}
            transition={{ duration: MOTION.row, ease: EASE_OUT }}
          >
            {agents.length === 0 ? (
              <div className="pl-6 py-0.5 text-2xs text-muted">No agents yet.</div>
            ) : (
              agents.map((agent) => (
                <AgentRow key={agent.taskId} agent={agent} onOpenAgent={onOpenAgent} />
              ))
            )}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

function AgentRow({
  agent,
  onOpenAgent,
}: {
  agent: WorkflowAgent;
  onOpenAgent: (childSessionId: string) => void;
}) {
  const running = agent.status === "running" || agent.status === "cancel_requested";
  useTimeTicker(running ? 1000 : 60_000);

  const durationMs = agent.durationMs ?? (running ? Date.now() - agent.startedAt : undefined);
  const elapsedLabel = durationMs != null && durationMs > 0 ? formatDuration(durationMs) : "";
  const tokens = agent.tokens?.total ?? 0;
  const childSessionId = agent.childSessionId;

  const statusLabel =
    agent.status === "cancel_requested"
      ? "stopping"
      : agent.status === "completed"
        ? "done"
        : agent.status;
  const body = (
    <>
      <span
        className={clsx(
          "shrink-0 text-2xs",
          running
            ? "text-accent"
            : agent.status === "failed"
              ? "text-bad"
              : "text-faint",
        )}
      >
        {statusLabel}
      </span>
      <span
        className={clsx(
          "min-w-0 flex-1 truncate",
          running
            ? "text-ink-soft"
            : agent.status === "failed"
              ? "text-bad"
              : agent.status === "completed"
                ? "text-muted"
                : "text-faint", // cancelled / interrupted
        )}
      >
        {agent.name ?? agent.taskId}
      </span>
      {tokens > 0 && <span className="shrink-0 text-2xs tabular-nums text-muted">{formatTokens(tokens)}</span>}
      {elapsedLabel && <span className="shrink-0 text-2xs tabular-nums text-muted">{elapsedLabel}</span>}
    </>
  );

  if (!childSessionId) {
    return <div className="flex items-center gap-2 pl-6 pr-1.5 py-0.5 text-xs">{body}</div>;
  }
  return (
    <button
      type="button"
      onClick={() => onOpenAgent(childSessionId)}
      title={`Open ${agent.name ?? "agent"} — watch its tool calls live`}
      className="group/agent flex w-full items-center gap-2 pl-6 pr-1.5 py-0.5 text-xs text-left bg-transparent border-0 rounded hover:bg-surface-soft transition-[background-color,scale] duration-row ease-out active:scale-[0.985]"
    >
      {body}
    </button>
  );
}
