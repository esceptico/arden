import { MOTION } from "@/lib/tokens/motion";
import type { AppDestination } from "@/api/navigation";
import type { BackgroundAgent } from "@/stores/background-agent-domain";

export const TOAST_LIFETIME_MS = MOTION.toastLifetime * 1_000;

/** Hard ceiling on a card's life. Holds (a blocking overlay, a re-arm) may
 *  push a toast past its lifetime but never past this. */
export const TOAST_CEILING_MS = TOAST_LIFETIME_MS * 3;

/** What is currently keeping a toast on screen. `hovered` is read from the DOM
 *  rather than from mouseenter/mouseleave bookkeeping: a card that spawns under
 *  the cursor, or slides under it when a sibling leaves, never gets the
 *  matching leave and would hold forever. */
export interface ToastHold {
  hovered: boolean;
  expired: boolean;
  blocked: boolean;
}

/** ms until the card should go, or null while something legitimately holds it. */
export function toastDismissDelay(hold: ToastHold): number | null {
  if (hold.hovered) return null;
  // The ceiling already elapsed behind a hold — go as soon as the hold ends,
  // without granting another full lifetime.
  if (hold.expired) return 0;
  // A blocking overlay makes the card inert, so a lifetime spent behind one
  // would drop the toast unread — hold it until the overlay closes.
  if (hold.blocked) return null;
  return TOAST_LIFETIME_MS;
}

/** Terminal states of a run. `isTerminalStatus` narrows to exactly these. */
export type ToastTerminalStatus = "completed" | "failed" | "cancelled";
/** `info` = an agent-raised offer (e.g. "Open the Ops area") — no run behind it. */
export type ToastStatus = ToastTerminalStatus | "info";

export type ToastTarget =
  | { kind: "session"; sessionId: string }
  | { kind: "automation"; taskId?: string }
  | { kind: "destination"; destination: AppDestination };

export interface Toast {
  id: string;
  title: string;
  detail?: string;
  status: ToastStatus;
  target: ToastTarget;
}

/** Terminal states that warrant a toast. Deliberately excludes "interrupted"
 *  and "cancel_requested" — those are mid-cancel, not user-facing completions. */
export function isTerminalStatus(status: string): status is ToastTerminalStatus {
  return status === "completed" || status === "failed" || status === "cancelled";
}

/** Toast for a background agent that reached a terminal state. Returns null
 *  when it is not terminal, or when the user is already looking at its session
 *  (suppress redundant noise). */
export function backgroundAgentToast(
  agent: BackgroundAgent,
  currentSessionId: string | null,
): Toast | null {
  if (!isTerminalStatus(agent.status)) return null;
  if (agent.sessionId === currentSessionId) return null;
  return {
    id: `bg:${agent.sessionId}:${agent.taskId}`,
    title: agent.command,
    detail: agent.detail,
    status: agent.status,
    target: { kind: "session", sessionId: agent.sessionId },
  };
}

/** Toast for a finished scheduled automation. Returns null when the automations
 *  modal is open (the user is already looking at it). */
export function automationToast(args: {
  taskId: string;
  name: string | null;
  result: string | null;
  automationsOpen: boolean;
}): Toast | null {
  if (args.automationsOpen) return null;
  return {
    id: `auto:${args.taskId}`,
    title: args.name ?? "Scheduled task",
    detail: args.result ?? undefined,
    status: "completed",
    target: { kind: "automation", taskId: args.taskId },
  };
}
