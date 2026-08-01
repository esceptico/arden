import { useEffect, useMemo, useRef, useState } from "react";
import { setTodoOverrideApi } from "@/api/chat";
import type { TodoListItem, TodoStatus } from "@/api/types";
import { dismissSessionTodo, resetSessionTodoOverride } from "@/actions/todos";
import { nextTodoStatus, todoSignature } from "@/features/background-agents/lib/todoOverride";
import { useStore, type SessionTodo } from "@/stores";

export interface EditableTodo {
  key: string;
  content: string;
  status: TodoStatus;
}

// Todos carry no server id, so mint a stable key per item — editing text then
// doesn't remount the row (no flash) and deleting animates the right row.
let todoKeySeq = 0;
const withTodoKeys = (items: TodoListItem[]): EditableTodo[] =>
  items.map((item) => ({ key: `todo-${todoKeySeq++}`, content: item.content, status: item.status }));

// Editable todo list over the session's effective state (override-aware,
// hydrated into the store). Manual edits persist server-side as an override
// (so the agent sees them on its next run) until the agent emits a new list,
// which supersedes them. Deleting the last item dismisses the whole list.
export function useEditableTodo(sessionId: string | null, todo: SessionTodo) {
  const config = useStore((s) => s.config);
  const sig = useMemo(() => todoSignature(todo.items), [todo.items]);
  const [items, setItems] = useState<EditableTodo[]>(() => withTodoKeys(todo.items));
  const [edited, setEdited] = useState(todo.edited);
  const lastSig = useRef(sig);

  // The store's copy changed underneath us (agent emitted a new list, or a
  // reset refetched) — adopt it. Our own commits pre-set lastSig so local
  // keys survive the round-trip.
  useEffect(() => {
    if (lastSig.current === sig) return;
    lastSig.current = sig;
    setItems(withTodoKeys(todo.items));
    setEdited(todo.edited);
  }, [sig, todo]);

  const commit = (next: EditableTodo[]) => {
    const plain = next.map(({ content, status }) => ({ content, status }));
    // Deleting the last row means "get rid of the list", not "store an
    // empty override that haunts the sidebar".
    if (plain.length === 0) {
      if (sessionId) void dismissSessionTodo(sessionId);
      return;
    }
    setItems(next);
    setEdited(true);
    lastSig.current = todoSignature(plain);
    if (sessionId) {
      useStore.getState().setSessionTodo(sessionId, {
        items: plain,
        explanation: todo.explanation ?? null,
        edited: true,
      });
      void setTodoOverrideApi(config, sessionId, plain).catch(() => {});
    }
  };

  return {
    items,
    edited,
    add: (content: string) => commit([...items, { key: `todo-${todoKeySeq++}`, content, status: "pending" }]),
    edit: (key: string, content: string) =>
      commit(items.map((item) => (item.key === key ? { ...item, content } : item))),
    remove: (key: string) => commit(items.filter((item) => item.key !== key)),
    cycle: (key: string) =>
      commit(items.map((item) => (item.key === key ? { ...item, status: nextTodoStatus(item.status) } : item))),
    reset: () => {
      if (sessionId) void resetSessionTodoOverride(sessionId);
    },
  };
}
