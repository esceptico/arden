import { useRef, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import clsx from "clsx";
import { CheckCircle2, Circle, CircleDot, Plus, X } from "@/components/icons";
import type { TodoStatus } from "@/api/types";
import { EASE_OUT, MOTION } from "@/lib/tokens/motion";
import { ICON } from "@/lib/icons";
import { type TodoListState } from "@/stores";
import { Tooltip } from "@/components/ui/Tooltip";
import { Caption } from "@/components/ui/Caption";
import { useEditableTodo } from "@/features/background-agents/hooks/useEditableTodo";
import { rosterRowMotion } from "@/features/background-agents/lib/rosterMotion";

function todoStatusIcon(status: TodoStatus) {
  if (status === "completed") {
    return <CheckCircle2 size={ICON.XS} strokeWidth={2.2} className="mt-[2px] shrink-0 text-ok" />;
  }
  if (status === "in_progress") {
    return <CircleDot size={ICON.XS} strokeWidth={2.2} className="mt-[2px] shrink-0 text-info" />;
  }
  return <Circle size={ICON.XS} strokeWidth={2} className="mt-[2px] shrink-0 text-faint" />;
}

// Inline single-line editor for a todo (edit existing or add new). Commits on
// Enter/blur, cancels on Escape; the ref guards against the blur-after-unmount
// double-fire (Enter -> commit -> unmount -> blur).
function TodoEditInput({
  initial,
  placeholder,
  onCommit,
  onCancel,
}: {
  initial: string;
  placeholder?: string;
  onCommit: (value: string) => void;
  onCancel: () => void;
}) {
  const [value, setValue] = useState(initial);
  const done = useRef(false);
  const commit = () => {
    if (done.current) return;
    done.current = true;
    onCommit(value);
  };
  const cancel = () => {
    if (done.current) return;
    done.current = true;
    onCancel();
  };
  return (
    <input
      autoFocus
      value={value}
      placeholder={placeholder}
      spellCheck={false}
      aria-label={placeholder ?? "Edit task"}
      onChange={(event) => setValue(event.target.value)}
      onKeyDown={(event) => {
        if (event.key === "Enter") {
          event.preventDefault();
          commit();
        } else if (event.key === "Escape") {
          event.preventDefault();
          cancel();
        }
      }}
      onBlur={commit}
      className="min-w-0 flex-1 bg-transparent border-0 p-0 text-xs leading-[1.35] text-ink-soft placeholder:text-muted outline-none"
    />
  );
}

export function TodoSidebarSection({
  todo,
  sessionId,
  label = "Tasks",
}: {
  todo: TodoListState;
  sessionId: string | null;
  /** Area Activity uses the mockup's concise group label. */
  label?: string;
}) {
  const { items, edited, add, edit, remove, cycle, reset } = useEditableTodo(sessionId, todo);
  const [editingKey, setEditingKey] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const completed = items.filter((item) => item.status === "completed").length;

  return (
    <section>
      <div className="flex items-center justify-between gap-2 px-0.5 pt-0.5 pb-1.5">
        <Caption tone="muted">{label}</Caption>
        <div className="flex items-center gap-1.5">
          {edited && (
            <Tooltip label="Reset to the agent's list">
              <button
                type="button"
                onClick={reset}
                className="text-2xs text-faint hover:text-ink transition-colors duration-row ease-out"
              >
                reset
              </button>
            </Tooltip>
          )}
          <span className="text-2xs tabular-nums text-faint">
            {completed}/{items.length}
          </span>
          <Tooltip label="Add a task">
            <button
              type="button"
              onClick={() => setAdding(true)}
              aria-label="Add a task"
              className="grid place-items-center w-4 h-4 rounded text-faint hover:text-ink hover:bg-surface-soft/70 transition-[color,background-color,scale] duration-check ease-out active:scale-[0.97]"
            >
              <Plus size={ICON.XS} strokeWidth={2} />
            </button>
          </Tooltip>
        </div>
      </div>
      <div className="relative flex flex-col gap-0.5">
        <AnimatePresence initial={false} mode="popLayout">
          {items.map((item) => (
            <motion.div
              key={item.key}
              {...rosterRowMotion}
              className="group/todo flex min-w-0 items-start gap-1.5 rounded px-1 -mx-1 hover:bg-surface-soft/40"
            >
              <Tooltip label="Cycle status">
                <button
                  type="button"
                  onClick={() => cycle(item.key)}
                  aria-label="Cycle status"
                  className="mt-[1px] shrink-0 rounded transition-[scale] duration-check ease-out active:scale-[0.97]"
                >
                  {todoStatusIcon(item.status)}
                </button>
              </Tooltip>
              {editingKey === item.key ? (
                <TodoEditInput
                  initial={item.content}
                  onCommit={(value) => {
                    if (value.trim()) edit(item.key, value.trim());
                    setEditingKey(null);
                  }}
                  onCancel={() => setEditingKey(null)}
                />
              ) : (
                <button
                  type="button"
                  onClick={() => setEditingKey(item.key)}
                  title="Edit"
                  className={clsx(
                    "min-w-0 flex-1 break-words text-left text-xs leading-[1.35] transition-colors duration-row ease-out hover:text-ink",
                    item.status === "completed" && "text-faint line-through",
                    item.status === "in_progress" && "font-medium text-ink-soft",
                    item.status === "pending" && "text-muted",
                  )}
                >
                  {item.content}
                </button>
              )}
              <Tooltip label="Delete task">
                <button
                  type="button"
                  onClick={() => remove(item.key)}
                  aria-label="Delete task"
                  className="mt-[1px] shrink-0 grid place-items-center w-4 h-4 rounded text-faint opacity-0 group-hover/todo:opacity-100 hover:text-bad transition-[opacity,color,scale] duration-row ease-out active:scale-[0.97]"
                >
                  <X size={ICON.XS} strokeWidth={2} />
                </button>
              </Tooltip>
            </motion.div>
          ))}
        </AnimatePresence>
        {adding && (
          <motion.div
            initial={{ opacity: 0, y: -4 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: MOTION.row, ease: EASE_OUT }}
            className="flex min-w-0 items-start gap-1.5 px-1 -mx-1"
          >
            <Circle size={ICON.XS} strokeWidth={2} className="mt-[2px] shrink-0 text-faint" />
            <TodoEditInput
              initial=""
              placeholder="New task…"
              onCommit={(value) => {
                if (value.trim()) add(value.trim());
                setAdding(false);
              }}
              onCancel={() => setAdding(false)}
            />
          </motion.div>
        )}
      </div>
    </section>
  );
}
