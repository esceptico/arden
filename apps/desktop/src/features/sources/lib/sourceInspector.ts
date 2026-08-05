import type { ActivityItem, SourceRef, UiMessage } from "@/stores";
import { sourceRefsForTurn } from "@/stores/sourceRefs";
import { messageSegments, type MessageSegment } from "@/lib/messageSegments";
import { visibleMessageIds } from "@/lib/messageVisibility";

export interface InspectedSource {
  source: SourceRef;
  toolCall?: ActivityItem;
}

export interface SourceInspectorSelection {
  turnId: string | null;
  sources: InspectedSource[];
}

export function sourceInspectorSelection({
  messages,
  order,
  sourceTurnId,
  showReasoning,
}: {
  messages: ReadonlyMap<string, UiMessage>;
  order: string[];
  sourceTurnId: string | null;
  showReasoning?: boolean;
}): SourceInspectorSelection {
  const segments = transcriptSegments(messages, order, showReasoning);
  const segment = sourceTurnId === null
    ? latestSourceBearingTurn(messages, segments)
    : segments.find((candidate) => candidate.turnId === sourceTurnId);

  if (!segment?.turnId) {
    return { turnId: sourceTurnId, sources: [] };
  }

  const toolCalls = activityItemsById(messages, segment.childIds);
  return {
    turnId: segment.turnId,
    sources: sourceRefsForTurn(messages, segment.childIds).map((source) => {
      const toolCall = source.toolCallId ? toolCalls.get(source.toolCallId) : undefined;
      return toolCall ? { source, toolCall } : { source };
    }),
  };
}

function transcriptSegments(
  messages: ReadonlyMap<string, UiMessage>,
  order: string[],
  showReasoning?: boolean,
): MessageSegment[] {
  const roles = order.map((id) => messages.get(id)?.role ?? null);
  const metaFlags = order.map((id) => Boolean(messages.get(id)?.isMeta));
  const contents = order.map((id) => {
    const message = messages.get(id);
    return message?.role === "assistant" && message.content.trim() ? "x" : "";
  });
  const visibleIds = visibleMessageIds({ ids: order, roles, metaFlags, contents, showReasoning });
  return messageSegments({ ids: order, roles, metaFlags, visibleIds });
}

function latestSourceBearingTurn(
  messages: ReadonlyMap<string, UiMessage>,
  segments: MessageSegment[],
): MessageSegment | undefined {
  for (let index = segments.length - 1; index >= 0; index -= 1) {
    const segment = segments[index];
    if (segment.turnId && sourceRefsForTurn(messages, segment.childIds).length > 0) {
      return segment;
    }
  }
  return undefined;
}

function activityItemsById(
  messages: ReadonlyMap<string, UiMessage>,
  childIds: string[],
): Map<string, ActivityItem> {
  const items = new Map<string, ActivityItem>();
  for (const childId of childIds) {
    for (const item of messages.get(childId)?.activity?.items ?? []) {
      items.set(item.id, item);
    }
  }
  return items;
}
