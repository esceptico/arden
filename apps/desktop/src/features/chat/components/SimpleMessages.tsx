import { memo } from "react";
import { Terminal } from "@/components/icons";
import clsx from "clsx";
import { ICON } from "@/lib/icons";
import { Marker, MarkerContent } from "@/components/ui/Marker";
import {
  SOURCE_FOCUS_CLASS,
  entryAnimation,
  useMessage,
  useSourceFocused,
} from "@/features/chat/lib/messageShared";

export const ToolMessage = memo(function ToolMessage({ id }: { id: string }) {
  const message = useMessage(id);
  const sourceFocused = useSourceFocused(id);
  if (!message) return null;
  const isRunning = !message.content;

  return (
    <article
      className={clsx(
        "grid grid-cols-[minmax(0,1fr)] gap-1.5 min-w-0 font-mono text-xs leading-[1.45] transition-[background-color,box-shadow] duration-panel",
        entryAnimation(message, "animate-roll-in"),
        sourceFocused && SOURCE_FOCUS_CLASS,
      )}
      data-id={id}
      data-source-focus={sourceFocused ? "true" : undefined}
      data-source-index={message.sourceIndex}
    >
      <div className="tool-line flex items-baseline gap-2 min-w-0" data-state={isRunning ? "running" : "done"}>
        <span className="text-faint shrink-0">↗</span>
        <Terminal size={ICON.XS} strokeWidth={2} className="text-muted shrink-0 self-center" />
        <span className="text-ink-soft font-medium shrink-0">{message.title || "tool"}</span>
        <span className="text-muted truncate min-w-0 flex-1">{message.subtitle || ""}</span>
      </div>
      {!isRunning && (
        <pre
          className={clsx(
            "m-0 mt-[3px] ml-[18px] text-faint font-mono text-sm leading-[1.45] whitespace-pre-wrap max-h-[80px] overflow-hidden [mask-image:linear-gradient(180deg,#000_60%,transparent)]",
            entryAnimation(message, "animate-fade-in"),
          )}
        >
          {message.content}
        </pre>
      )}
    </article>
  );
});

export const StatusMessage = memo(function StatusMessage({ id }: { id: string }) {
  const message = useMessage(id);
  const sourceFocused = useSourceFocused(id);
  if (!message) return null;
  const text = message.title ? `${message.title} · ${message.content}` : message.content;
  return (
    <article
      className={clsx(
        "grid grid-cols-[minmax(0,1fr)] transition-[background-color,box-shadow] duration-panel",
        entryAnimation(message, "animate-fade-in"),
        sourceFocused && SOURCE_FOCUS_CLASS,
      )}
      data-id={id}
      data-source-focus={sourceFocused ? "true" : undefined}
      data-source-index={message.sourceIndex}
    >
      <Marker variant="separator">
        <MarkerContent>{text}</MarkerContent>
      </Marker>
    </article>
  );
});

export const ErrorMessage = memo(function ErrorMessage({ id }: { id: string }) {
  const message = useMessage(id);
  const sourceFocused = useSourceFocused(id);
  if (!message) return null;
  return (
    <article
      className={clsx(
        "grid grid-cols-[minmax(0,1fr)] transition-[background-color,box-shadow] duration-panel",
        entryAnimation(message, "animate-fade-in"),
        sourceFocused && SOURCE_FOCUS_CLASS,
      )}
      data-id={id}
      data-source-focus={sourceFocused ? "true" : undefined}
      data-source-index={message.sourceIndex}
    >
      <div role="alert" className="px-3.5 py-2.5 rounded-[10px] bg-bad-soft border border-bad/15 text-bad text-base leading-[1.45] whitespace-pre-wrap break-words">
        {message.content}
      </div>
    </article>
  );
});
