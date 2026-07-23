export interface MessageSourceFocus {
  sessionId: string;
  messageStart?: number;
  messageEnd?: number;
  messageStartId?: string;
  messageEndId?: string;
  nonce: number;
}

export interface SourceIndexedMessage {
  sourceIndex?: number;
  sourceMessageId?: string;
}

export function messageInSourceFocus(
  message: SourceIndexedMessage | null | undefined,
  focus: MessageSourceFocus | null | undefined,
  currentSessionId: string | null | undefined,
): boolean {
  if (!message || !focus || currentSessionId !== focus.sessionId) return false;
  if (focus.messageStartId && message.sourceMessageId) {
    if (message.sourceMessageId === focus.messageStartId) return true;
    if (focus.messageEndId && message.sourceMessageId === focus.messageEndId) return true;
  }
  if (focus.messageStart === undefined || focus.messageEnd === undefined) return false;
  if (message.sourceIndex === undefined) return false;
  return message.sourceIndex >= focus.messageStart && message.sourceIndex < focus.messageEnd;
}

export function firstMessageIdInSourceFocus<T extends SourceIndexedMessage>(
  order: string[],
  messages: Map<string, T>,
  focus: MessageSourceFocus | null | undefined,
  currentSessionId: string | null | undefined,
): string | null {
  if (!focus || currentSessionId !== focus.sessionId) return null;
  if (focus.messageStartId) {
    for (const id of order) {
      if (messages.get(id)?.sourceMessageId === focus.messageStartId) return id;
    }
  }
  for (const id of order) {
    if (messageInSourceFocus(messages.get(id), focus, currentSessionId)) return id;
  }
  return null;
}
