import { cancelQueuedMessageApi, cancelRun, cancelSubagentApi } from "@/api/chat";
import { apiWithConfig } from "@/api/core";
import { getState, setState, type ActivityItem, type ImageBlock } from "@/stores";
import { messagesScroll } from "@/lib/messagesScroll";
import {
  reduceRunCompleted,
  reduceRunFailed,
  reduceRunStarted,
  reduceRunStopCleared,
  reduceRunStopRequested,
} from "@/stores/run-lifecycle";
import { clearCachedStoppingRun } from "@/stores/session-cache";
import { mergeSourceRefs } from "@/stores/sourceRefs";
import { createSession } from "@/actions/sessions";

interface SendMessageOptions {
  meta?: boolean;
}

interface ChatMessageResponse {
  run_id: string;
  session_id?: string;
  status?: "queued" | string;
}

export async function sendMessage(
  text: string,
  images: ImageBlock[] = [],
  options: SendMessageOptions = {},
): Promise<void> {
  const trimmedText = text.trim();
  if (!trimmedText && images.length === 0) return;

  // Home has no current session (the door, not a room — see Slices spec
  // Placement) — the FIRST message from its hero input is what actually
  // provisions the session, lazily, reusing the same createSession path
  // the sidebar's "new session in project" rows use.
  if (!getState().currentSessionId) {
    await createSession();
  }

  const s = getState();
  if (!s.currentSessionId) return;
  const sendSessionId = s.currentSessionId;

  if (s.editingId) {
    // Truncate the *server's* saved message list at the message being
    // edited too — without this, the agent's next run sees both the
    // original message and the edit and the chat snowballs.
    try {
      await apiWithConfig(s.config, "/session/revert", {
        method: "POST",
        body: JSON.stringify({
          session_id: sendSessionId,
          message_id: s.editingId,
        }),
      });
    } catch (error) {
      if (getState().currentSessionId === sendSessionId) {
        s.appendMessage({
          id: crypto.randomUUID(),
          role: "error",
          content: error instanceof Error ? error.message : String(error),
        });
      }
      return;
    }
    if (getState().currentSessionId !== sendSessionId) return;
    s.truncateFrom(s.editingId);
    s.setEditingId(null);
  }

  // Use the same id locally and on the server so /session/revert can match
  // this user message back to its saved row when the user later edits it.
  const userMessageId = crypto.randomUUID();
  if (!options.meta) {
    s.appendMessage({
      id: userMessageId,
      role: "user",
      content: trimmedText,
      turn: { startedAt: Date.now(), endedAt: null, durationMs: null },
      images: images.length > 0 ? images : undefined,
    });
  }
  messagesScroll.scrollToBottom?.("smooth");
  setState((state) =>
    reduceRunStarted(state, { runId: null, sessionId: sendSessionId }),
  );

  try {
    const response = await apiWithConfig<{ run_id: string }>(s.config, "/chat/message", {
      method: "POST",
      body: JSON.stringify({
        message: trimmedText,
        session_id: sendSessionId,
        skip_approvals: s.skipApprovals,
        images: images.length > 0 ? images : undefined,
        client_id: options.meta ? `goal:${Date.now()}` : userMessageId,
      }),
    });
    setState((state) =>
      reduceRunStarted(state, { runId: response.run_id, sessionId: sendSessionId }),
    );
  } catch (error) {
    setState((state) =>
      reduceRunFailed(state, { runId: null, sessionId: sendSessionId }),
    );
    if (getState().currentSessionId === sendSessionId) {
      s.appendMessage({
        id: crypto.randomUUID(),
        role: "error",
        content: error instanceof Error ? error.message : String(error),
      });
    }
  }
}

/** Submit a message while a run is in flight. Server queues it onto the
 *  active run's inject_queue; we render it as a "Queued" bubble above
 *  the composer until `message_ingested` arrives. */
interface EnqueueMessageOptions {
  meta?: boolean;
}

export async function enqueueMessage(
  text: string,
  images: ImageBlock[] = [],
  options: EnqueueMessageOptions = {},
): Promise<void> {
  const s = getState();
  if (!s.currentSessionId) return;
  const sendSessionId = s.currentSessionId;
  const trimmed = text.trim();
  if (!trimmed && images.length === 0) return;

  const clientId = options.meta ? `goal:${Date.now()}` : crypto.randomUUID();
  if (!options.meta) {
    s.addQueuedMessage({
      clientId,
      text: trimmed,
      images: images.length > 0 ? images : undefined,
      status: "pending",
      enqueuedAt: Date.now(),
    });
  }

  try {
    const response = await apiWithConfig<ChatMessageResponse>(s.config, "/chat/message", {
      method: "POST",
      body: JSON.stringify({
        message: trimmed,
        session_id: sendSessionId,
        skip_approvals: s.skipApprovals,
        images: images.length > 0 ? images : undefined,
        client_id: clientId,
      }),
    });
    setState((state) =>
      reduceRunStarted(state, { runId: response.run_id, sessionId: sendSessionId }),
    );
    if (response.status !== "queued") {
      promoteQueuedSubmitToStartedRun(sendSessionId, clientId, trimmed, images, options.meta);
    }
  } catch (error) {
    if (!options.meta) failQueuedSubmit(sendSessionId, clientId, error);
  }
}

function promoteQueuedSubmitToStartedRun(
  sessionId: string,
  clientId: string,
  text: string,
  images: ImageBlock[],
  isMeta = false,
): void {
  setState((state) => {
    if (state.currentSessionId !== sessionId) {
      const cached = state.sessionCache.get(sessionId);
      if (!cached) return {};
      const sessionCache = new Map(state.sessionCache);
      sessionCache.set(sessionId, {
        ...cached,
        queuedMessages: cached.queuedMessages.filter((message) => message.clientId !== clientId),
      });
      return { sessionCache };
    }

    const queuedMessages = state.queuedMessages.filter((message) => message.clientId !== clientId);
    if (isMeta) return { queuedMessages };

    const messages = new Map(state.messages);
    const alreadyPresent = messages.has(clientId);
    if (!alreadyPresent) {
      messages.set(clientId, {
        id: clientId,
        role: "user",
        content: text,
        turn: { startedAt: Date.now(), endedAt: null, durationMs: null },
        images: images.length > 0 ? images : undefined,
      });
    }
    return {
      queuedMessages,
      messages,
      order: alreadyPresent ? state.order : [...state.order, clientId],
    };
  });
}

function failQueuedSubmit(sessionId: string, clientId: string, error: unknown): void {
  const message = error instanceof Error ? error.message : String(error);
  setState((state) => {
    if (state.currentSessionId !== sessionId) {
      const cached = state.sessionCache.get(sessionId);
      if (!cached) return {};
      const sessionCache = new Map(state.sessionCache);
      sessionCache.set(sessionId, {
        ...cached,
        queuedMessages: cached.queuedMessages.filter((item) => item.clientId !== clientId),
      });
      return { sessionCache };
    }

    const id = crypto.randomUUID();
    const messages = new Map(state.messages);
    messages.set(id, { id, role: "error", content: message });
    return {
      queuedMessages: state.queuedMessages.filter((item) => item.clientId !== clientId),
      messages,
      order: [...state.order, id],
    };
  });
}

/** Cancel a queued (not yet ingested) message. The server returns:
 *    cancelled         — removed; we drop the bubble
 *    already_ingested  — too late, the agent has it; the imminent
 *                        message_ingested event will absorb the bubble
 *    no_run            — no active run, drop the bubble */
export async function cancelQueuedMessage(clientId: string): Promise<void> {
  const s = getState();
  if (!s.currentSessionId) return;
  s.setQueuedMessageStatus(clientId, "cancelling");
  let result;
  try {
    result = await cancelQueuedMessageApi(s.config, s.currentSessionId, clientId);
  } catch {
    s.setQueuedMessageStatus(clientId, "pending");
    return;
  }
  if (result === "cancelled" || result === "no_run") {
    s.removeQueuedMessage(clientId);
  } else {
    s.setQueuedMessageStatus(clientId, "sent");
  }
}

export async function stopRun(): Promise<void> {
  const s = getState();
  const sessionId = s.currentSessionId;
  // currentRunId is null for a backgrounded/automation run the user is
  // viewing (the Stop button is still shown via the `running` flag), so fall
  // back to the session's active run id from the runs poll. Only bail if no
  // active run exists anywhere for this session.
  const runId =
    s.currentRunId ??
    (sessionId
      ? (s.sessions.find((session) => session.session_id === sessionId)?.active_run_id ?? null)
      : null);
  // Bail only if we have nothing to target. With a sessionId the server can
  // resolve the active run even when the client never tracked a run_id.
  if (!runId && !sessionId) return;
  if (runId) setState((state) => reduceRunStopRequested(state, runId));
  const clearStoppedRun = () => {
    if (!runId) return; // no local run to clear; the run_cancelled event/poll will
    setState((state) =>
      reduceRunCompleted(state, {
        runId,
        sessionId,
      }),
    );
  };
  try {
    await cancelRun(s.config, runId, sessionId);
    clearStoppedRun();
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    if (message === "Run not found") {
      clearStoppedRun();
      return;
    }
    if (runId) {
      setState((state) => ({
        ...reduceRunStopCleared(state, runId),
        ...clearCachedStoppingRun(state, sessionId, runId),
      }));
    }
    if (getState().currentSessionId === sessionId) {
      s.appendMessage({
        id: crypto.randomUUID(),
        role: "error",
        content: message,
      });
    }
  }
}

function findActivityItem(itemId: string): ActivityItem | null {
  for (const message of getState().messages.values()) {
    const item = message.activity?.items.find((candidate) => candidate.id === itemId);
    if (item) return item;
  }
  return null;
}

function mergeActivityItemForSession(
  sessionId: string | null,
  itemId: string,
  patch: Partial<ActivityItem>,
): void {
  setState((state) => {
    if (state.currentSessionId === sessionId) {
      return {};
    }
    if (!sessionId) return {};
    const cached = state.sessionCache.get(sessionId);
    if (!cached) return {};
    const messages = new Map(cached.messages);
    let touched = false;
    for (const [messageId, message] of messages) {
      if (!message.activity) continue;
      const itemIndex = message.activity.items.findIndex((item) => item.id === itemId);
      if (itemIndex < 0) continue;
      const items = message.activity.items.slice();
      const existingItem = items[itemIndex];
      const sourceRefs = mergeSourceRefs(existingItem.sourceRefs, patch.sourceRefs);
      items[itemIndex] = {
        ...existingItem,
        ...patch,
        ...(sourceRefs === undefined ? {} : { sourceRefs }),
      };
      messages.set(messageId, { ...message, activity: { ...message.activity, items } });
      touched = true;
      break;
    }
    if (!touched) return {};
    const sessionCache = new Map(state.sessionCache);
    sessionCache.set(sessionId, { ...cached, messages });
    return { sessionCache };
  });
  if (getState().currentSessionId === sessionId) {
    getState().mergeActivityItem(itemId, patch);
  }
}

export async function cancelSubagent(runId: string, toolCallId: string): Promise<void> {
  const s = getState();
  const sessionId = s.currentSessionId;
  if (!runId) return;
  const previous = findActivityItem(toolCallId);
  s.mergeActivityItem(toolCallId, { cancelRequested: true, progress: "cancelling" });
  try {
    await cancelSubagentApi(s.config, runId, toolCallId);
  } catch (error) {
    mergeActivityItemForSession(sessionId, toolCallId, {
      cancelRequested: false,
      progress: previous?.progress,
    });
    if (getState().currentSessionId !== sessionId) return;
    getState().appendMessage({
      id: crypto.randomUUID(),
      role: "error",
      content: error instanceof Error ? error.message : String(error),
    });
  }
}
