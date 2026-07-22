import { useState, type ReactNode } from "react";
import { motion } from "motion/react";
import clsx from "clsx";
import {
  Bot,
  Brain,
  CornerUpLeft,
  Loader2,
  SendHorizontal,
  Split,
  Square,
  type NtrpIcon,
} from "@/components/icons";
import { ICON } from "@/lib/icons";
import { EASE_OUT, MOTION } from "@/lib/tokens/motion";
import {
  isActiveAgentStatus,
  statusDotClass,
  type AgentRunStatus,
  type AgentRunView,
} from "@/lib/agentRun";
import { IconSwap } from "@/components/ui/IconSwap";
import { StatusDot } from "@/components/ui/StatusDot";
import { Collapse } from "@/components/ui/Collapse";
import { ConfirmDeleteButton } from "@/components/ui/ConfirmDeleteButton";

// <AgentRunRow> — the dense borderless row for the right-sidebar agents hub.
// The activity trace renders its own coherent agent row (trace/ActivityTrace),
// so the two surfaces share the glyph + status-dot + meta/result vocabulary and
// an agent reads as the same object wherever it appears. <AgentRunContent> is
// the body — name + meta + status + affordances + result — reused by the
// automations card so an automation renders the SAME agent body inside its card
// shell.

interface AgentRunRowProps {
  run: AgentRunView;
  /** Open the agent's session. Omit to render a non-interactive card. */
  onOpen?: () => void;
  /** Cancel a running agent. Omit to hide the stop affordance. */
  onStop?: () => void;
  stopping?: boolean;
}

function glyphToneClass(status: AgentRunStatus): string {
  switch (status) {
    case "completed":
      return "bg-ok-soft text-ok";
    case "failed":
      return "bg-bad-soft text-bad";
    case "cancelled":
    case "interrupted":
      return "bg-surface-soft text-faint";
    default:
      return "bg-accent-soft text-accent-strong"; // running, cancel_requested
  }
}

function AgentGlyph({
  status,
  size,
  onStop,
  stopping,
}: {
  status: AgentRunStatus;
  size: number;
  onStop?: () => void;
  stopping?: boolean;
}) {
  const canStop = !!onStop && status === "running";
  const box = { width: size, height: size } as const;
  return (
    <span
      className="relative grid place-items-center shrink-0 self-center"
      style={box}
    >
      <span
        aria-hidden
        className={clsx(
          "grid place-items-center rounded-md transition-opacity duration-row ease-out",
          glyphToneClass(status),
          canStop && "group-hover/run:opacity-0",
        )}
        style={box}
      >
        <Bot size={ICON.XS} strokeWidth={2} />
      </span>
      {canStop && (
        <button
          type="button"
          aria-label="Stop agent"
          title="Stop agent"
          disabled={stopping}
          onClick={(event) => {
            event.stopPropagation();
            onStop?.();
          }}
          className="absolute inset-0 grid place-items-center rounded-md border-0 p-0 m-0 bg-surface-soft text-faint opacity-0 pointer-events-none transition-[opacity,color,scale] duration-row ease-out group-hover/run:pointer-events-auto group-hover/run:opacity-100 hover:text-bad active:scale-[0.97] focus-visible:pointer-events-auto focus-visible:opacity-100 disabled:opacity-[0.6]"
        >
          <Square size={ICON.XS} strokeWidth={2} />
        </button>
      )}
    </span>
  );
}

// The meta line: type · schedule · next-run · detached. Automations fill the
// schedule/nextRun slots; plain agents leave them empty so the line collapses
// to just the type (· detached).
function metaLine(run: AgentRunView): string {
  return [run.type, run.schedule, run.nextRun, run.detached ? "detached" : null]
    .filter(Boolean)
    .join(" · ");
}

/** Handoff actions for a finished agent's result. */
export interface AgentHandoff {
  /** Drop the result into the parent composer to reply with it. */
  onReply?: () => void | Promise<void>;
  /** Pin the result into long-term memory. */
  onPin?: () => void | Promise<void>;
  /** Seed a fresh agent/session with the result. */
  onRoute?: () => void | Promise<void>;
}

/** A generic icon-action for the hover affordance lane. The same render path
 *  serves agent handoffs and automation controls (run / history / delete / …),
 *  so the two surfaces speak one affordance vocabulary. */
export interface AgentRunAction {
  icon: NtrpIcon;
  label: string;
  onClick: () => void | Promise<void>;
  busy?: boolean;
  disabled?: boolean;
  danger?: boolean;
  /** Render as a cancellable countdown ConfirmDeleteButton instead of a plain
   *  icon button, pinning the hover lane open while armed. For destructive
   *  actions (the `icon` is ignored — the countdown button draws its own). */
  confirm?: boolean;
}

// One icon-action button. Owns its own async-busy state so a fire-and-forget
// onClick still spins; an externally-supplied `busy` flag overrides it (the
// caller may track the action across re-renders).
function ActionButton({ action }: { action: AgentRunAction }) {
  const [localBusy, setLocalBusy] = useState(false);
  const busy = action.busy || localBusy;
  return (
    <button
      type="button"
      aria-label={action.label}
      title={action.label}
      disabled={busy || action.disabled}
      onClick={async (e) => {
        e.stopPropagation();
        if (busy || action.disabled) return;
        setLocalBusy(true);
        try {
          await action.onClick();
        } finally {
          setLocalBusy(false);
        }
      }}
      className={clsx(
        "grid place-items-center w-4 h-4 rounded text-faint transition-[color,scale] duration-check ease-out active:scale-[0.97] disabled:opacity-50",
        action.danger ? "hover:text-bad" : "hover:text-ink",
      )}
    >
      <IconSwap
        state={busy ? "b" : "a"}
        iconA={<action.icon size={ICON.XS} strokeWidth={2} />}
        iconB={<Loader2 size={ICON.XS} strokeWidth={2} className="animate-spin" />}
      />
    </button>
  );
}

// 4 micro-pips before the StatusDot, colored by the same statusDotClass the
// dot uses — recent run outcomes at a glance, no separate color fn.
function StatusSparkline({ statuses }: { statuses: AgentRunStatus[] }) {
  if (statuses.length === 0) return null;
  return (
    <span className="inline-flex items-center gap-0.5">
      {statuses.slice(0, 4).map((s, i) => (
        <span
          key={i}
          aria-hidden
          className={clsx("w-1 h-1 rounded-[1px]", statusDotClass(s).split(" ")[0])}
        />
      ))}
    </span>
  );
}

/** The shared agent body: leading glyph + name + meta line + the hover
 *  affordance lane (handoffs/actions) + status dot + elapsed + result line.
 *  Rendered borderless by AgentRunRow (sidebar hub) and inside a card shell by
 *  the automations modal — one body, two frames. */
export function AgentRunContent({
  run,
  onOpen,
  onStop,
  stopping,
  active,
  onSend,
  handoff,
  actions,
  leading,
  badges,
}: AgentRunRowProps & {
  active?: boolean;
  onSend?: (message: string) => void | Promise<void>;
  handoff?: AgentHandoff;
  /** Per-instance icon-actions for the hover lane. */
  actions?: AgentRunAction[];
  /** Override the leading glyph footprint (e.g. an automation's enable
   *  toggle). Defaults to the status-toned Bot glyph. */
  leading?: ReactNode;
  /** Optional status badges (running / channel / trust) under the name. */
  badges?: ReactNode;
}) {
  const running = isActiveAgentStatus(run.status);
  const meta = metaLine(run);
  const third = running ? run.progress : run.resultPreview;
  const canSend = running && !!onSend;
  const [composing, setComposing] = useState(false);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  // A confirm-action (countdown delete) pins the lane open while armed so the
  // countdown can't fade out from under the cursor and fire unseen.
  const [confirmArmed, setConfirmArmed] = useState(false);

  // A paused automation must not read green/active: force its dot muted even
  // when the last run completed, and suppress the recent-runs sparkline (whose
  // green "completed" pips would otherwise contradict the muted dot). Pause-ness
  // lives on the `enabled` facet, not the status. (undefined enabled = plain
  // agent → never forced.)
  const paused = run.enabled === false && !running;
  const dotStatus: AgentRunStatus = paused ? "interrupted" : run.status;

  const handoffActions = !running && handoff
    ? ([
        handoff.onReply && {
          icon: CornerUpLeft,
          label: "Reply with result",
          onClick: handoff.onReply,
        },
        handoff.onPin && { icon: Brain, label: "Pin to memory", onClick: handoff.onPin },
        handoff.onRoute && {
          icon: Split,
          label: "Route to a new agent",
          onClick: handoff.onRoute,
        },
      ].filter(Boolean) as AgentRunAction[])
    : [];
  const laneActions = [...(actions ?? []), ...handoffActions];
  const hasLane = laneActions.length > 0 || canSend;

  // Await the send so a failure (e.g. the agent finished between render and
  // send → 404) keeps the draft + composer instead of silently eating it.
  const submit = async () => {
    const text = draft.trim();
    if (!text || sending) return;
    setSending(true);
    try {
      await onSend?.(text);
      setDraft("");
      setComposing(false);
    } catch {
      /* keep draft + composer open so the message isn't lost */
    } finally {
      setSending(false);
    }
  };

  return (
    <>
      <div className="flex items-center gap-2 min-w-0">
        {leading ?? (
          <AgentGlyph status={run.status} size={ICON.MD} onStop={onStop} stopping={stopping} />
        )}
        {onOpen ? (
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              onOpen();
            }}
            title="Open agent session"
            data-child-session-id={run.childSessionId}
            className={clsx(
              "min-w-0 flex-1 truncate border-0 bg-transparent p-0 text-left text-sm tracking-[-0.005em]",
              active ? "text-ink" : "text-ink-soft hover:text-ink",
            )}
          >
            {run.name}
          </button>
        ) : (
          <span className="min-w-0 flex-1 truncate text-sm text-ink-soft tracking-[-0.005em]">
            {run.name}
          </span>
        )}
        {/* Right cluster: the status read-out and the hover affordance lane
            crossfade in the same footprint. The lane is absolute so it
            reserves ZERO width — the title/name stays full-width and nothing
            ever sits underneath the actions (the status cluster is gone when
            they appear). The send composer toggle, when pinned open, keeps the
            cluster faded so the lane stays interactive. */}
        <span className="relative shrink-0 flex items-center self-center">
          <span
            className={clsx(
              "flex items-center gap-2 transition-opacity duration-row",
              hasLane && "group-hover/run:opacity-0 group-focus-within/run:opacity-0",
              // composing (send) and confirmArmed (countdown delete) pin the
              // lane open, so the status cluster must stay hidden to match.
              (composing || confirmArmed) && "opacity-0",
            )}
          >
            {run.recentStatuses && !paused && <StatusSparkline statuses={run.recentStatuses} />}
            <StatusDot status={dotStatus} pulse={running} />
            {run.elapsedLabel && (
              <span className="text-2xs tabular-nums text-muted">{run.elapsedLabel}</span>
            )}
          </span>
          {hasLane && (
            <span
              className={clsx(
                "absolute inset-y-0 right-0 flex items-center gap-0.5 transition-opacity duration-row ease-out",
                composing || confirmArmed
                  ? "opacity-100"
                  : "opacity-0 pointer-events-none group-hover/run:opacity-100 group-hover/run:pointer-events-auto group-focus-within/run:opacity-100 group-focus-within/run:pointer-events-auto",
              )}
            >
              {canSend && (
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    setComposing((v) => !v);
                  }}
                  aria-label="Send a message to this agent"
                  title="Send a message to this agent"
                  className={clsx(
                    "grid place-items-center w-4 h-4 rounded text-faint transition-[color,scale] duration-check ease-out active:scale-[0.97] hover:text-ink",
                    composing && "text-ink",
                  )}
                >
                  <SendHorizontal size={ICON.XS} strokeWidth={2} />
                </button>
              )}
              {laneActions.map((action) =>
                action.confirm ? (
                  // Wrapper swallows the click so arming/cancelling the
                  // countdown doesn't also fire the row's open handler.
                  <span
                    key={action.label}
                    onClick={(e) => e.stopPropagation()}
                    onMouseDown={(e) => e.stopPropagation()}
                    className="contents"
                  >
                    <ConfirmDeleteButton
                      size="xs"
                      label={action.label}
                      busy={action.busy}
                      onConfirm={action.onClick}
                      onActiveChange={setConfirmArmed}
                    />
                  </span>
                ) : (
                  <ActionButton key={action.label} action={action} />
                ),
              )}
            </span>
          )}
        </span>
      </div>
      {badges && (
        <div className="pl-[24px] flex flex-wrap items-center gap-1.5 min-w-0">{badges}</div>
      )}
      {(meta || third) && (
        <div className="pl-[24px] min-w-0">
          {meta && <div className="truncate text-2xs text-faint">{meta}</div>}
          {third &&
            (running ? (
              <div className="truncate text-2xs text-muted italic">{third}</div>
            ) : (
              <motion.div
                key={third}
                initial={{ opacity: 0, y: 2 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: MOTION.row, ease: EASE_OUT }}
                className="truncate text-2xs text-muted"
              >
                {third}
              </motion.div>
            ))}
        </div>
      )}
      <Collapse open={composing}>
        <div className="mt-1.5 pl-[24px]">
          <input
            autoFocus
            value={draft}
            disabled={sending}
            aria-label="Message this agent"
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                event.preventDefault();
                void submit();
              } else if (event.key === "Escape") {
                event.preventDefault();
                setComposing(false);
                setDraft("");
              }
            }}
            onBlur={() => {
              if (!draft.trim()) setComposing(false);
            }}
            placeholder="Message this agent…"
            spellCheck={false}
            className="w-full h-7 px-2 rounded-md bg-surface-soft focus:bg-surface-sunken text-xs text-ink-soft placeholder:text-muted outline-none border border-transparent focus:border-line transition-colors duration-check disabled:opacity-60"
          />
        </div>
      </Collapse>
    </>
  );
}

/** Dense sidebar row — same object, no card chrome (the panel is the surface).
 *  A thin borderless wrapper around <AgentRunContent>. `active` marks the
 *  agent whose session you're currently viewing. */
export function AgentRunRow({
  run,
  onOpen,
  onStop,
  stopping,
  active,
  onSend,
  handoff,
  actions,
}: AgentRunRowProps & {
  active?: boolean;
  onSend?: (message: string) => void | Promise<void>;
  handoff?: AgentHandoff;
  actions?: AgentRunAction[];
}) {
  return (
    <div
      data-active={active || undefined}
      className={clsx(
        "group/run py-1",
        active && "app-row rounded-lg -mx-1.5 px-1.5",
      )}
    >
      <AgentRunContent
        run={run}
        onOpen={onOpen}
        onStop={onStop}
        stopping={stopping}
        active={active}
        onSend={onSend}
        handoff={handoff}
        actions={actions}
      />
    </div>
  );
}
