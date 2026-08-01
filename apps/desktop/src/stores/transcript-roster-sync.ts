import { SEMANTIC_KIND_AGENT } from "@/lib/agent";
import { findBackgroundAgentForActivityItem } from "@/stores/background-agent-domain";
import type { ActivityItem, BackgroundAgent, UiMessage } from "@/stores/types";

type TerminalTaskStatus = "completed" | "failed" | "cancelled" | "interrupted";

function terminalTaskStatus(status: BackgroundAgent["status"]): TerminalTaskStatus | null {
  return status === "completed" ||
    status === "failed" ||
    status === "cancelled" ||
    status === "interrupted"
    ? status
    : null;
}

/** Settle transcript agent items whose durable roster row already reached a
 *  terminal status. The 5s roster poll only feeds the sidebar rows; without
 *  this the in-chat agent row keeps saying "running" until a history reload —
 *  the split-brain the two surfaces must never show. Returns a new messages
 *  map, or null when every matched item is already settled. */
export function syncTranscriptAgentsFromRoster(
  messages: ReadonlyMap<string, UiMessage>,
  rows: Record<string, BackgroundAgent>,
  sessionId: string | null,
): Map<string, UiMessage> | null {
  if (!sessionId) return null;

  let next: Map<string, UiMessage> | null = null;
  for (const [messageId, message] of messages) {
    if (!message.activity) continue;
    let items: ActivityItem[] | null = null;
    for (const [index, item] of message.activity.items.entries()) {
      if (item.semanticKind !== SEMANTIC_KIND_AGENT && !item.childAgent) continue;
      const agent = findBackgroundAgentForActivityItem(rows, item, sessionId);
      if (!agent) continue;
      const terminal = terminalTaskStatus(agent.status);
      if (!terminal || item.taskStatus === terminal) continue;
      // Never regress an item the transcript already settled harder — a fresh
      // TOOL_CALL_RESULT / task_finished outranks a possibly stale poll row.
      if (item.taskStatus && item.taskStatus !== "running" && terminal === "interrupted") continue;
      items = items ?? message.activity.items.slice();
      items[index] = {
        ...item,
        taskStatus: terminal,
        status: "executed",
        cancelRequested: false,
      };
    }
    if (!items) continue;
    next = next ?? new Map(messages);
    next.set(messageId, {
      ...message,
      activity: { ...message.activity, items },
    });
  }
  return next;
}
