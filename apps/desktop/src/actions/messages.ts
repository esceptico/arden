import { cancelRun, cancelSubagentApi } from "@/api/chat";
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
import { provisionSession } from "@/actions/sessions";

interface SendMessageOptions {
  meta?: boolean;
  clientId?: string;
  fromQueue?: boolean;
}

interface ChatMessageResponse {
  run_id: string;
  session_id?: string;
  status?: "queued" | string;
}

const pendingHomeSessionCreations = new Map<number, Promise<string>>();

async function resolveHomeMessageSession(): Promise<string | null> {
  const initial = getState();
  const draftId = initial.pendingNewChatDraftId;
  let creation = pendingHomeSessionCreations.get(draftId);
  if (!creation) {
    creation = provisionSession(initial.pendingNewChatAreaId).then((session) => session.session_id);
    pendingHomeSessionCreations.set(draftId, creation);
  }
  try {
    const sessionId = await creation;
    const latest = getState();
    const stillOwnsDraft =
      latest.pendingNewChatDraftId === draftId
      && (latest.currentSessionId === null || latest.currentSessionId === sessionId)
      && latest.areas.openAreaKey === null
      && !latest.memoryOpen
      && !latest.automationsOpen
      && !latest.settingsOpen;
    if (stillOwnsDraft && latest.currentSessionId === null) {
      latest.setCurrentSession(sessionId);
      getState().setHistory([]);
    }
    return sessionId;
  } catch (error) {
    getState().setError(error instanceof Error ? error.message : String(error));
    return null;
  } finally {
    if (pendingHomeSessionCreations.get(draftId) === creation) {
      pendingHomeSessionCreations.delete(draftId);
    }
  }
}

function markMessageRunStarted(sessionId: string, runId: string | null): void {
  setState((state) => {
    if (state.currentSessionId !== null) {
      return reduceRunStarted(state, { runId, sessionId });
    }
    const activeRunSessionIds = new Set(state.activeRunSessionIds);
    activeRunSessionIds.add(sessionId);
    return { activeRunSessionIds };
  });
}

function markMessageRunFailed(sessionId: string): void {
  setState((state) => {
    if (state.currentSessionId !== null) {
      return reduceRunFailed(state, { runId: null, sessionId });
    }
    const activeRunSessionIds = new Set(state.activeRunSessionIds);
    activeRunSessionIds.delete(sessionId);
    return { activeRunSessionIds };
  });
}

export async function sendMessage(
  text: string,
  images: ImageBlock[] = [],
  options: SendMessageOptions = {},
): Promise<boolean> {
  const trimmedText = text.trim();
  if (!trimmedText && images.length === 0) return false;

  // Home has no current session (the door, not a room — see Areas spec
  // Placement) — the FIRST message from its hero input is what actually
  // provisions the pending Inbox/Area draft.
  // Do not introduce an async boundary for an already-selected session: an
  // edit must begin its revert before a following navigation can switch the
  // transcript underneath it.
  const currentSessionId = getState().currentSessionId;
  const sendSessionId = currentSessionId ?? await resolveHomeMessageSession();
  if (!sendSessionId) return false;

  const s = getState();
  const isForeground = s.currentSessionId === sendSessionId;

  if (isForeground && s.editingId) {
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
      return false;
    }
    if (getState().currentSessionId !== sendSessionId) return false;
    s.truncateFrom(s.editingId);
    s.setEditingId(null);
  }

  // Use the same id locally and on the server so /session/revert can match
  // this user message back to its saved row when the user later edits it.
  const userMessageId = options.clientId ?? crypto.randomUUID();
  if (isForeground && !options.meta) {
    s.appendMessage({
      id: userMessageId,
      role: "user",
      content: trimmedText,
      turn: { startedAt: Date.now(), endedAt: null, durationMs: null },
      images: images.length > 0 ? images : undefined,
    });
  }
  if (isForeground) messagesScroll.scrollToBottom?.("smooth");
  markMessageRunStarted(sendSessionId, null);

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
    markMessageRunStarted(sendSessionId, response.run_id);
    return true;
  } catch (error) {
    markMessageRunFailed(sendSessionId);
    if (getState().currentSessionId === sendSessionId) {
      if (options.fromQueue && !options.meta) {
        setState((state) => {
          const messages = new Map(state.messages);
          messages.delete(userMessageId);
          return {
            messages,
            order: state.order.filter((id) => id !== userMessageId),
          };
        });
      }
      s.appendMessage({
        id: crypto.randomUUID(),
        role: "error",
        content: error instanceof Error ? error.message : String(error),
      });
    }
    return false;
  }
}

/** Queue a fresh turn behind the active run. */
interface EnqueueMessageOptions {
  meta?: boolean;
}

export async function enqueueMessage(
  text: string,
  images: ImageBlock[] = [],
  options: EnqueueMessageOptions = {},
): Promise<void> {
  const s = getState();
  const trimmed = text.trim();
  if ((!trimmed && images.length === 0) || s.queuedMessages.length >= 10) return;
  s.addQueuedMessage({
    clientId: crypto.randomUUID(),
    text: trimmed,
    images: images.length > 0 ? images : undefined,
    meta: options.meta,
    status: "pending",
    enqueuedAt: Date.now(),
  });
}

/** Inject an instruction into the active run at its next model step. */
export async function steerMessage(
  text: string,
  images: ImageBlock[] = [],
  options: EnqueueMessageOptions = {},
): Promise<void> {
  const s = getState();
  if (!s.currentSessionId) return;
  const trimmed = text.trim();
  if (!trimmed && images.length === 0) return;
  const clientId = options.meta ? `goal:${Date.now()}` : crypto.randomUUID();
  if (!options.meta) {
    s.appendMessage({
      id: clientId,
      role: "user",
      content: trimmed,
      turn: { startedAt: Date.now(), endedAt: null, durationMs: null },
      images: images.length > 0 ? images : undefined,
    });
    messagesScroll.scrollToBottom?.("smooth");
  }
  try {
    await apiWithConfig<ChatMessageResponse>(s.config, "/chat/message", {
      method: "POST",
      body: JSON.stringify({
        message: trimmed,
        session_id: s.currentSessionId,
        skip_approvals: s.skipApprovals,
        images: images.length > 0 ? images : undefined,
        client_id: clientId,
      }),
    });
  } catch (error) {
    s.appendMessage({
      id: crypto.randomUUID(),
      role: "error",
      content: error instanceof Error ? error.message : String(error),
    });
  }
}

/** Dispatch exactly one local FIFO item as a fresh turn. */
export async function dispatchQueuedHead(sessionId: string): Promise<void> {
  const s = getState();
  if (s.currentSessionId !== sessionId || s.running) return;
  const head = s.queuedMessages[0];
  if (!head || head.status === "sending") return;
  s.setQueuedMessageStatus(head.clientId, "sending");
  const accepted = await sendMessage(head.text, head.images ?? [], {
    meta: head.meta,
    clientId: head.clientId,
    fromQueue: true,
  });
  const latest = getState();
  if (accepted) latest.removeQueuedMessage(head.clientId);
  else latest.setQueuedMessageStatus(head.clientId, "failed");
}

/** Cancel a desktop-owned queued turn immediately. */
export function cancelQueuedMessage(clientId: string): void {
  getState().removeQueuedMessage(clientId);
}

/** Reorder the desktop-local queue without involving the server. */
export function moveQueuedMessage(clientId: string, toIndex: number): void {
  const queued = getState().queuedMessages;
  const fromIndex = queued.findIndex((message) => message.clientId === clientId);
  if (fromIndex < 0 || toIndex < 0 || toIndex >= queued.length || fromIndex === toIndex) return;
  const next = [...queued];
  const [moved] = next.splice(fromIndex, 1);
  next.splice(toIndex, 0, moved);
  setState({ queuedMessages: next });
}

/** Remove a queued turn locally and inject it into the active run now. */
export async function promoteQueuedMessageToSteer(clientId: string): Promise<void> {
  const state = getState();
  const message = state.queuedMessages.find((item) => item.clientId === clientId);
  if (!message || !state.currentSessionId || message.status === "sending") return;
  state.removeQueuedMessage(clientId);
  await steerMessage(message.text, message.images ?? [], { meta: message.meta });
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
