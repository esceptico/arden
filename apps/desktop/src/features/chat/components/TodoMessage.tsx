import { memo } from "react";
import { CheckCircle2, Circle, CircleDot, ListChecks } from "@/components/icons";
import clsx from "clsx";
import type { TodoStatus } from "@/api/types";
import { BlurSwap } from "@/components/ui/BlurSwap";
import { ICON } from "@/lib/icons";
import {
  SOURCE_FOCUS_CLASS,
  entryAnimation,
  useMessage,
  useSourceFocused,
} from "@/features/chat/lib/messageShared";

export const TodoMessage = memo(function TodoMessage({ id }: { id: string }) {
  const message = useMessage(id);
  const sourceFocused = useSourceFocused(id);
  if (!message?.todo || message.todo.items.length === 0) return null;

  const items = message.todo.items;
  const completed = items.filter((item) => item.status === "completed").length;

  return (
    <article
      className={clsx(
        "grid grid-cols-[minmax(0,1fr)] transition-[background-color,box-shadow] duration-panel",
        entryAnimation(message, "animate-roll-in"),
        sourceFocused && SOURCE_FOCUS_CLASS,
      )}
      data-id={id}
      data-source-focus={sourceFocused ? "true" : undefined}
      data-source-index={message.sourceIndex}
    >
      <div className="surface-panel surface-radius-lg max-w-[560px] px-3.5 py-3 border border-line-soft">
        <div className="flex items-center justify-between gap-3">
          <div className="inline-flex items-center gap-2 min-w-0">
            <ListChecks size={ICON.SM} strokeWidth={2} className="text-muted shrink-0" />
            <span className="text-sm font-medium leading-[var(--leading-label)] text-ink">Tasks</span>
          </div>
          <span className="shrink-0 text-xs tabular-nums text-muted">{completed}/{items.length}</span>
        </div>
        {message.todo.explanation && (
          <div className="mt-1.5 text-xs leading-[var(--leading-body)] text-muted break-words">
            {message.todo.explanation}
          </div>
        )}
        <ul className="mt-2.5 flex flex-col gap-1.5">
          {items.map((item, index) => (
            <li key={`${index}-${item.content}`} className="flex items-start gap-2 min-w-0">
              <BlurSwap swapKey={item.status} blur={3} className="mt-[1px] shrink-0">
                <TodoIcon status={item.status} />
              </BlurSwap>
              <span
                className={clsx(
                  "min-w-0 flex-1 text-sm leading-[var(--leading-body)] break-words transition-colors duration-trace ease-out",
                  item.status === "completed" && "text-faint line-through",
                  item.status === "in_progress" && "text-ink font-medium",
                  item.status === "pending" && "text-muted",
                )}
              >
                {item.content}
              </span>
            </li>
          ))}
        </ul>
      </div>
    </article>
  );
});

function TodoIcon({ status }: { status: TodoStatus }) {
  if (status === "completed") {
    return <CheckCircle2 size={ICON.SM} strokeWidth={2.2} className="text-ok" />;
  }
  if (status === "in_progress") {
    return <CircleDot size={ICON.SM} strokeWidth={2.2} className="text-info" />;
  }
  return <Circle size={ICON.SM} strokeWidth={2} className="text-faint" />;
}
