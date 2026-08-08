import type { Role } from "@/stores/types";

type MessageVisibilityInput = {
  role: Role | null;
  content?: string;
  isMeta?: boolean;
  showReasoning?: boolean;
};

type MessageDisplayPolicy = {
  hiddenInTranscript: boolean;
  breaksTurn: boolean;
  breaksActivity: boolean;
};

export function messageDisplayPolicy(message: MessageVisibilityInput): MessageDisplayPolicy {
  const isReasoning = message.role === "reasoning";
  const isEmptyAssistant =
    message.role === "assistant" && (message.content ?? "").trim().length === 0;
  const isMetaUser = message.role === "user" && message.isMeta === true;
  const isTodoState = message.role === "todo";
  const isHiddenContinuation = isReasoning || isEmptyAssistant || isTodoState;
  const keepsActivityOpen = isEmptyAssistant || isTodoState;
  // Opting reasoning into the transcript changes only what is drawn. It still
  // belongs to the same turn, but it separates tool groups chronologically.
  const isRevealedReasoning = isReasoning && message.showReasoning === true;

  return {
    hiddenInTranscript: (isHiddenContinuation && !isRevealedReasoning) || isMetaUser,
    breaksTurn: isMetaUser,
    breaksActivity: isMetaUser || !keepsActivityOpen,
  };
}

export function isActivityContinuationMessage(message: MessageVisibilityInput): boolean {
  return !messageDisplayPolicy(message).breaksActivity;
}

export function isHiddenTranscriptMessage(message: MessageVisibilityInput): boolean {
  return messageDisplayPolicy(message).hiddenInTranscript;
}

export function isHiddenTurnBoundary(message: MessageVisibilityInput): boolean {
  return messageDisplayPolicy(message).breaksTurn;
}

export function visibleMessageIds({
  ids,
  roles,
  metaFlags,
  contents,
  showReasoning,
}: {
  ids: string[];
  roles: (Role | null)[];
  metaFlags?: boolean[];
  contents?: string[];
  showReasoning?: boolean;
}): string[] {
  return ids.filter((_, index) => {
    const role = roles[index];
    if (role === null) return false;
    const content = contents ? contents[index] ?? "" : role === "assistant" ? "visible" : "";
    return !isHiddenTranscriptMessage({
      role,
      content,
      isMeta: metaFlags?.[index] ?? false,
      showReasoning,
    });
  });
}
