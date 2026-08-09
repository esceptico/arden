import type { ActivityItem, BackgroundAgent } from "@/stores";
import type { Automation, AutomationTrigger, BackgroundTaskSummary } from "@/api/types";
import {
  normalizeBackgroundAgentStatus,
  type BackgroundAgentSnapshot,
} from "@/stores/background-agent-domain";
import { isChannelAutomation } from "@/lib/automationFilters";
import { activityItemStatus, extractTask, friendlyAgentLabel } from "@/lib/agent";

/** Map a durable child-agent record (roster fetch) into the sidebar's snapshot
 *  shape. Shared by the roster poll and the reconnect resync so both produce an
 *  identical row. */
export function childAgentTaskToBackgroundSnapshot(
  task: BackgroundTaskSummary,
): BackgroundAgentSnapshot {
  const startedAt = Date.parse(task.started_at ?? task.created_at);
  if (!Number.isFinite(startedAt)) {
    throw new Error(`Invalid child-agent timestamp for ${task.task_id}`);
  }
  return {
    taskId: task.child_run_id ?? task.task_id,
    agentRef: task.agent_ref,
    childSessionId: task.child_session_id ?? undefined,
    command: task.command,
    status: normalizeBackgroundAgentStatus(task.status),
    detail: task.detail ?? undefined,
    resultRef: task.result_ref ?? undefined,
    parentToolCallId: task.parent_tool_call_id ?? undefined,
    agentType: task.agent_type ?? undefined,
    wait: task.wait ?? undefined,
    createdAt: startedAt,
  };
}

// One view-model for "a sub-agent run", shared by every surface that shows
// one: the inline chat card, the right-sidebar agents hub, and the inspector.
// The point is that an agent looks like the same object everywhere — name,
// type, status, elapsed, result — instead of three different cryptic rows.

export type AgentRunStatus = BackgroundAgent["status"];

export interface AgentRunView {
  /** Stable identity + React key. */
  key: string;
  /** Human title — the task, never a raw run id. */
  name: string;
  /** Humanized agent type, e.g. "Research". */
  type: string;
  status: AgentRunStatus;
  /** Compact elapsed/duration label, e.g. "2m". May be empty. */
  elapsedLabel: string;
  /** Child session to open on click. */
  childSessionId?: string;
  /** Run/task id used to cancel a running agent. */
  runId?: string;
  /** Detached (wait === false) vs awaited. undefined when unknown. */
  detached?: boolean;
  /** Live progress / detail line while running. */
  progress?: string;
  /** One-line result preview for terminal runs. */
  resultPreview?: string;

  // ── Automation facets ──────────────────────────────────────────────
  // An automation is the same abstraction as a parent/child agent run, so
  // it flows through this view-model too. These slots are null for plain
  // agents — a general primitive grows optional facets rather than a
  // bespoke parallel type. Paused-ness lives here (not in `status`) so a
  // paused-but-last-completed automation never reads as green/active.
  /** Automation enable state. undefined for plain agents. */
  enabled?: boolean;
  /** Humanized recurrence, e.g. "at 09:00 · weekdays". */
  schedule?: string;
  /** Next-run label, e.g. "next in 2h" / "paused" / "due now". */
  nextRun?: string;
  /** Last few run outcomes (newest-first) for the meta sparkline. */
  recentStatuses?: AgentRunStatus[];
}

const KNOWN_AGENT_TYPES: Record<string, string> = {
  background_research: "Research",
  research: "Research",
  sub_agent: "Agent",
  "sub-agent": "Agent",
};

/** "background_research" → "Research", "code_review_agent" → "Code review". */
export function humanizeAgentType(type: string | undefined): string {
  if (!type) return "Agent";
  const known = KNOWN_AGENT_TYPES[type.toLowerCase()];
  if (known) return known;
  const cleaned = type
    .replace(/[_-]+/g, " ")
    .replace(/\bagent\b/i, "")
    .trim();
  if (!cleaned) return "Agent";
  return cleaned.charAt(0).toUpperCase() + cleaned.slice(1);
}

export function isActiveAgentStatus(status: AgentRunStatus): boolean {
  return status === "running" || status === "cancel_requested";
}

export function agentRunStatusLabel(status: AgentRunStatus): string {
  switch (status) {
    case "cancel_requested":
      return "stopping";
    case "cancelled":
      return "cancelled";
    case "interrupted":
      return "interrupted";
    case "completed":
      return "completed";
    case "failed":
      return "failed";
    default:
      return "running";
  }
}

// Compact relative-time formatter ("2m", "3h"). Sans suffix — space is
// tight in the sidebar column.
export function formatElapsed(since: number | string): string {
  const started = typeof since === "number" ? since : new Date(since).getTime();
  const seconds = Math.max(0, Math.floor((Date.now() - started) / 1000));
  if (seconds < 60) return `${seconds}s`;
  const mins = Math.floor(seconds / 60);
  if (mins < 60) return `${mins}m`;
  return `${Math.floor(mins / 60)}h`;
}

// Compact ms-duration formatter ("45s", "2m", "3h"). Shared by the agent
// view-models and the workflow surfaces.
export function formatDuration(ms: number): string {
  const seconds = Math.round(ms / 1000);
  if (seconds < 60) return `${seconds}s`;
  const mins = Math.floor(seconds / 60);
  if (mins < 60) return `${mins}m`;
  return `${Math.floor(mins / 60)}h`;
}

/** First meaningful line of a result, stripped of markdown chrome and
 *  truncated — for the one-line preview under a finished agent. */
export function resultSnippet(
  text: string | null | undefined,
  max = 140,
): string | undefined {
  if (!text) return undefined;
  let inFence = false;
  for (const raw of text.split(/\r?\n/)) {
    let line = raw.trim();
    if (line.startsWith("```")) {
      inFence = !inFence;
      continue;
    }
    // Fenced content (code, an area agent's json ask nomination) is never a
    // useful one-line preview — skip the whole block, not just its markers.
    if (!line || inFence) continue;
    line = line
      .replace(/^#{1,6}\s+/, "")
      .replace(/^[-*+]\s+/, "")
      .replace(/^>\s+/, "")
      .replace(/^\d+\.\s+/, "")
      .replace(/\*\*/g, "")
      .replace(/[*_`]/g, "")
      .trim();
    if (!line) continue;
    return line.length > max ? `${line.slice(0, max - 1).trimEnd()}…` : line;
  }
  return undefined;
}

// A child agent session id is minted as `${parentSessionId}::${hex}` by the
// spawner; nested agents stack suffixes (`root::a::b`), so the immediate
// parent is everything before the LAST "::". Server-authoritative
// `parent_session_id` is preferred where available; this is the fallback.
export function parentSessionIdOf(sessionId: string | null | undefined): string | null {
  if (!sessionId) return null;
  const idx = sessionId.lastIndexOf("::");
  return idx > 0 ? sessionId.slice(0, idx) : null;
}

export function isAgentSessionId(sessionId: string | null | undefined): boolean {
  return !!sessionId && sessionId.includes("::");
}

const TERMINAL_AGENT_STATUSES: ReadonlySet<string> = new Set([
  "completed",
  "failed",
  "cancelled",
  "interrupted",
]);

/** Whether the transcript itself (task events / tool result data) says the
 *  agent finished — the evidence that outranks a possibly stale roster poll. */
function activityItemIsTerminal(item: ActivityItem): boolean {
  return (
    (item.taskStatus != null && TERMINAL_AGENT_STATUSES.has(item.taskStatus)) ||
    (item.childAgent != null && TERMINAL_AGENT_STATUSES.has(item.childAgent.status))
  );
}

function resolveActivityStatus(item: ActivityItem): AgentRunStatus {
  if (item.cancelRequested) return "cancel_requested";
  if (item.taskStatus === "running") return "running";
  if (item.taskStatus != null && TERMINAL_AGENT_STATUSES.has(item.taskStatus)) {
    return item.taskStatus;
  }
  const childStatus = item.childAgent?.status;
  if (childStatus != null && TERMINAL_AGENT_STATUSES.has(childStatus)) {
    return childStatus as AgentRunStatus;
  }
  // An unrecognized child status must not read "running" forever — settle it
  // as interrupted, matching normalizeBackgroundAgentStatus.
  if (childStatus && childStatus !== "running" && childStatus !== "activity" && childStatus !== "started") {
    return "interrupted";
  }
  return activityItemStatus(item) === "ongoing" ? "running" : "completed";
}

export interface ActivityItemRunContext {
  /** Durable roster row backing this item (same rows the sidebar renders). */
  roster?: BackgroundAgent;
  /** Fetched one-line result preview (shared childAgentResultSnippets cache). */
  resultSnippet?: string;
}

/** Build a view from an inline activity-trace agent item. When the durable
 *  roster row is supplied, running-ness and the terminal status come from the
 *  same record the sidebar shows — the transcript's own terminal evidence
 *  still outranks a stale poll row. */
export function agentRunFromActivityItem(
  item: ActivityItem,
  context: ActivityItemRunContext = {},
): AgentRunView {
  const child = item.childAgent;
  let status = resolveActivityStatus(item);
  const roster = context.roster;
  if (roster && !item.cancelRequested && !activityItemIsTerminal(item)) {
    // No terminal transcript evidence → the roster row is the truth. This is
    // what keeps a detached agent (whose parent item.result is just the spawn
    // ack) reading "running" in chat exactly as long as the sidebar does.
    status = roster.status;
  }
  // Prefer the server's concise display name; fall back to the task (better
  // than the raw `Background(task: "…")` tool target) then the target itself.
  const rosterName = roster?.command.trim();
  const name =
    (rosterName && rosterName !== "Agent" ? rosterName : undefined) ??
    item.displayName ??
    extractTask(item.args) ??
    item.target ??
    friendlyAgentLabel(item.kind);
  const startedAt = item.startedAt ?? roster?.createdAt;
  return {
    key: item.id,
    name: name?.trim() || "Agent",
    type: humanizeAgentType(child?.agentType ?? item.kind),
    status,
    elapsedLabel:
      item.durationMs != null
        ? formatDuration(item.durationMs)
        : isActiveAgentStatus(status) && startedAt != null
          ? formatElapsed(startedAt)
          : "",
    childSessionId: roster?.childSessionId ?? child?.childSessionId,
    runId: item.runId,
    detached: child ? child.wait === false : roster ? roster.wait === false : undefined,
    progress: item.progress,
    // For a detached agent the parent's `item.result` is only the spawn ack,
    // not the real output (that lives in the child session) — so never preview
    // it inline; the fetched durable snippet stands in once it lands. Awaited
    // agents carry their real result (including failure text) here.
    resultPreview: isActiveAgentStatus(status)
      ? undefined
      : context.resultSnippet ??
        (child?.wait === false ? undefined : resultSnippet(item.result)),
  };
}

/** Build a view from a polled background-agent row (sidebar hub). */
export function agentRunFromBackgroundAgent(
  agent: BackgroundAgent,
  resultPreview?: string,
): AgentRunView {
  const active = isActiveAgentStatus(agent.status);
  return {
    key: agent.taskId,
    // Never surface a raw run id; a blank command degrades to the type label.
    name: agent.command?.trim() || humanizeAgentType(agent.agentType),
    type: humanizeAgentType(agent.agentType),
    status: agent.status,
    // Active rows use the server's durable start time. Terminal rows keep the
    // recorded duration/result instead of a growing relative-time label.
    elapsedLabel: active ? formatElapsed(agent.createdAt) : "",
    childSessionId: agent.childSessionId,
    runId: agent.taskId,
    detached: agent.wait === false,
    progress: active ? agent.detail : undefined,
    resultPreview: active ? undefined : resultPreview ?? resultSnippet(agent.detail),
  };
}

// ── Automation → AgentRunView ─────────────────────────────────────────
// "A child is the same abstraction as a parent agent." An automation is a
// scheduled agent run, so it maps onto the very same view-model and renders
// through the same agent body — the only difference is the optional facets
// (enable state, schedule, next-run, recent sparkline).

/** Map an automation's bimodal (enabled, running_since, last_status) state
 *  onto ONE AgentRunStatus.
 *
 *  Crucially, paused-ness is NOT folded into the status — a paused
 *  automation that last completed must not read green/active. So:
 *    - running_since set → "running"
 *    - last run failed   → "failed"
 *    - last run ok       → "completed"
 *    - never run         → "interrupted" (muted/idle tone)
 *  The pause is carried separately on the `enabled` facet; renderers state
 *  pausedness independently from this run lifecycle. */
export function resolveAutomationStatus(automation: Automation): AgentRunStatus {
  if (automation.running_since != null) return "running";
  if (automation.last_status === "failed") return "failed";
  if (automation.last_status === "completed") return "completed";
  return "interrupted";
}

const TRIGGER_KIND_LABEL: Record<AutomationTrigger["type"], string> = {
  time: "Schedule",
  event: "On event",
  idle: "Idle",
  count: "Loop",
  message: "Channel",
};

function automationTypeLabel(automation: Automation): string {
  if (isChannelAutomation(automation)) return "Channel";
  const first = automation.triggers[0];
  return first ? TRIGGER_KIND_LABEL[first.type] : "Automation";
}

function mapRunStatus(status: string): AgentRunStatus {
  if (status === "completed") return "completed";
  if (status === "failed") return "failed";
  if (status === "running") return "running";
  return "interrupted";
}

/** Build an AgentRunView from an automation. Automation runs have NO
 *  openable session id (only the bound channel is openable), so
 *  `childSessionId` is intentionally left unset — the card wires its own
 *  open handler. */
export function agentRunFromAutomation(automation: Automation): AgentRunView {
  const running = automation.running_since != null;
  const status = resolveAutomationStatus(automation);
  // Running → live elapsed since the run started. Otherwise the last-run
  // relative time (never a misleading "0s" on a finished run).
  const elapsedLabel = running
    ? formatElapsed(automation.running_since as string)
    : automation.last_run_at
      ? formatRelative(automation.last_run_at)
      : "";
  return {
    key: automation.task_id,
    name: automation.name?.trim() || "Untitled",
    type: automationTypeLabel(automation),
    status,
    elapsedLabel,
    // Automation runs aren't openable sessions; the card opens its channel.
    childSessionId: undefined,
    // No live per-step detail is surfaced here, so the running state is conveyed
    // by the pulsing status dot + the "running" badge — NOT a redundant "running"
    // progress line (which duplicated the badge).
    progress: undefined,
    resultPreview: running ? undefined : resultSnippet(automation.last_result),
    enabled: automation.enabled,
    schedule: automation.triggers.map(formatTrigger).join(" · ") || undefined,
    nextRun: formatNext(automation) ?? undefined,
    recentStatuses: automation.recent_statuses?.map(mapRunStatus),
  };
}

// ── Automation formatters (moved from AutomationsModal so the builder is
//    self-contained; AutomationsModal re-imports them). ─────────────────

export function formatTrigger(t: AutomationTrigger): string {
  if (t.type === "time") {
    if (t.every) {
      const win = t.start && t.end ? ` ${t.start}–${t.end}` : "";
      const days = t.days ? ` · ${t.days}` : "";
      return `every ${t.every}${win}${days}`;
    }
    if (t.at) {
      const days = t.days ? ` · ${t.days}` : "";
      return `at ${t.at}${days}`;
    }
    return "time";
  }
  if (t.type === "event") {
    const lead = t.lead_minutes != null ? ` (${t.lead_minutes}m)` : "";
    return `on:${t.event_type ?? "?"}${lead}`;
  }
  if (t.type === "idle") return `idle ${t.idle_minutes}m`;
  if (t.type === "count") return `every ${t.every_n ?? t.threshold ?? "?"} turns`;
  return t.type;
}

/** Human label for the "next run" slot. Skips the slot for paused
 *  automations (returns "paused") and avoids the "next 36d ago" oddity when
 *  the scheduler hasn't recomputed a next-run timestamp. */
export function formatNext(automation: Automation): string | null {
  if (!automation.enabled) return "paused";
  if (!automation.next_run_at) return null;
  const t = new Date(automation.next_run_at).getTime();
  if (!Number.isFinite(t)) return null;
  if (t <= Date.now()) return "due now";
  return `next ${formatRelative(automation.next_run_at)}`;
}

export function formatRelative(iso: string): string {
  const then = new Date(iso).getTime();
  if (!Number.isFinite(then)) return iso;
  const delta = then - Date.now();
  const abs = Math.abs(delta);
  const min = Math.round(abs / 60_000);
  if (min < 1) return delta > 0 ? "<1m" : "now";
  if (min < 60) return delta > 0 ? `in ${min}m` : `${min}m ago`;
  const h = Math.round(min / 60);
  if (h < 24) return delta > 0 ? `in ${h}h` : `${h}h ago`;
  const d = Math.round(h / 24);
  return delta > 0 ? `in ${d}d` : `${d}d ago`;
}
