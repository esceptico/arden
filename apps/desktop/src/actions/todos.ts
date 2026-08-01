import { dismissTodoApi, getSessionTodoApi, resetTodoOverrideApi } from "@/api/chat";
import { getState } from "@/stores";

/** Hydrate the session's effective todo list from the server-level slot.
 *  Null (retired or dismissed) clears the sidebar section. Non-blocking
 *  and failure-tolerant — the sidebar is an affordance, not the chat. */
export async function fetchSessionTodo(sessionId: string): Promise<void> {
  const s = getState();
  try {
    const todo = await getSessionTodoApi(s.config, sessionId);
    getState().setSessionTodo(
      sessionId,
      todo ? { items: todo.items, explanation: todo.explanation, edited: todo.edited } : null,
    );
  } catch {
    /* keep whatever the slot holds */
  }
}

/** Remove the whole list — agent state and manual override both go. The
 *  agent emitting a new list later brings the section back. */
export async function dismissSessionTodo(sessionId: string): Promise<void> {
  getState().setSessionTodo(sessionId, null);
  try {
    await dismissTodoApi(getState().config, sessionId);
  } catch {
    /* the slot is already cleared locally; the agent's next update wins */
  }
}

/** Drop manual edits and fall back to the agent's current list. */
export async function resetSessionTodoOverride(sessionId: string): Promise<void> {
  try {
    await resetTodoOverrideApi(getState().config, sessionId);
  } catch {
    return;
  }
  await fetchSessionTodo(sessionId);
}
