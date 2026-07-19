import { messageSegments, type MessageSegment } from "@/lib/messageSegments";
import { visibleMessageIds } from "@/lib/messageVisibility";
import type { ActivityItem, UiMessage } from "@/stores";

const EXPANDED_ROW_LIMIT = 5;

export interface ProofAction {
  toolCallId: string;
  toolLabel: string;
  operation: string;
  target: string;
  beforeRef?: string;
  afterRef?: string;
}

export interface ProofCheck {
  toolCallId: string;
  toolLabel: string;
  postcondition: string;
  observed: string;
  confidence?: number;
}

export interface ProofReceipt {
  toolCallId: string;
  toolLabel: string;
  receipt: string;
}

export interface ProofLimitation {
  toolCallId: string;
  toolLabel: string;
  status: "failed" | "denied" | "uncertain";
  code: string;
  recoveryAction?: string;
}

export interface TurnProofSummary {
  tone: "recorded" | "attention";
  actionCount: number;
  checkCount: number;
  receiptCount: number;
  limitationCount: number;
  actions: ProofAction[];
  checks: ProofCheck[];
  receipts: ProofReceipt[];
  limitations: ProofLimitation[];
}

export function turnProofSummary(
  messages: ReadonlyMap<string, UiMessage>,
  order: string[],
  turnId: string,
): TurnProofSummary | null {
  const segment = transcriptSegments(messages, order).find((candidate) => candidate.turnId === turnId);
  if (!segment) return null;

  const actions: ProofAction[] = [];
  const checks: ProofCheck[] = [];
  const receipts: ProofReceipt[] = [];
  const limitations: ProofLimitation[] = [];

  for (const item of activityItems(messages, segment)) {
    const outcome = item.outcome;
    if (!outcome) continue;
    const toolLabel = item.displayName ?? item.noun ?? item.kind;
    if (outcome.effect) {
      actions.push({
        toolCallId: item.id,
        toolLabel,
        operation: outcome.effect.operation,
        target: outcome.effect.target,
        ...(outcome.effect.before_ref ? { beforeRef: outcome.effect.before_ref } : {}),
        ...(outcome.effect.after_ref ? { afterRef: outcome.effect.after_ref } : {}),
      });
    }
    if (outcome.verification) {
      checks.push({
        toolCallId: item.id,
        toolLabel,
        postcondition: outcome.verification.postcondition,
        observed: outcome.verification.observed,
        ...(outcome.verification.confidence === undefined
          ? {}
          : { confidence: outcome.verification.confidence }),
      });
    }
    if (outcome.receipt) {
      receipts.push({ toolCallId: item.id, toolLabel, receipt: outcome.receipt });
    }
    if (outcome.status !== "succeeded") {
      limitations.push({
        toolCallId: item.id,
        toolLabel,
        status: outcome.status,
        code: outcome.error?.code ?? outcome.status,
        ...(outcome.error?.recovery_action ? { recoveryAction: outcome.error.recovery_action } : {}),
      });
    }
  }

  const meaningful = actions.length + checks.length + receipts.length + limitations.length;
  if (meaningful === 0) return null;
  return {
    tone: limitations.length > 0 ? "attention" : "recorded",
    actionCount: actions.length,
    checkCount: checks.length,
    receiptCount: receipts.length,
    limitationCount: limitations.length,
    actions: actions.slice(0, EXPANDED_ROW_LIMIT),
    checks: checks.slice(0, EXPANDED_ROW_LIMIT),
    receipts: receipts.slice(0, EXPANDED_ROW_LIMIT),
    limitations: limitations.slice(0, EXPANDED_ROW_LIMIT),
  };
}

export function latestInspectableTurnId(
  messages: ReadonlyMap<string, UiMessage>,
  order: string[],
): string | null {
  const segments = transcriptSegments(messages, order);
  for (let index = segments.length - 1; index >= 0; index -= 1) {
    if (segments[index].turnId && segments[index].childIds.length > 0) return segments[index].turnId;
  }
  return null;
}

function transcriptSegments(messages: ReadonlyMap<string, UiMessage>, order: string[]): MessageSegment[] {
  const roles = order.map((id) => messages.get(id)?.role ?? null);
  const metaFlags = order.map((id) => Boolean(messages.get(id)?.isMeta));
  const contents = order.map((id) => {
    const message = messages.get(id);
    return message?.role === "assistant" && message.content.trim() ? "x" : "";
  });
  const visibleIds = visibleMessageIds({ ids: order, roles, metaFlags, contents });
  return messageSegments({ ids: order, roles, metaFlags, visibleIds });
}

function activityItems(
  messages: ReadonlyMap<string, UiMessage>,
  segment: MessageSegment,
): ActivityItem[] {
  return segment.childIds.flatMap((id) => messages.get(id)?.activity?.items ?? []);
}
