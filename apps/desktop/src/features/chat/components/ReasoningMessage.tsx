import { memo, useState } from "react";
import { AnimatePresence } from "motion/react";
import { Brain01, ChevronDown } from "@/components/icons";
import clsx from "clsx";
import { useStore } from "@/stores";
import { Markdown } from "@/components/ui/Markdown";
import { Reveal } from "@/components/ui/Reveal";
import { ICON } from "@/lib/icons";
import { useSmoothStreamedContent } from "@/features/chat/hooks/useSmoothStream";
import {
  SOURCE_FOCUS_CLASS,
  entryAnimation,
  useIsLast,
  useMessage,
  useSourceFocused,
} from "@/features/chat/lib/messageShared";

export const ReasoningMessage = memo(function ReasoningMessage({ id }: { id: string }) {
  const message = useMessage(id);
  const isLast = useIsLast(id);
  const running = useStore((s) => s.running);
  const sourceFocused = useSourceFocused(id);
  const [expanded, setExpanded] = useState(false);
  const isStreaming = isLast && running;
  // Only run the rAF loop when the user can actually see it (expanded).
  const smoothContent = useSmoothStreamedContent(message?.content ?? "", isStreaming && expanded);
  if (!message) return null;

  return (
    <article
      className={clsx(
        "board-reasoning grid grid-cols-[minmax(0,1fr)] min-w-0 transition-[background-color,box-shadow] duration-panel",
        entryAnimation(message, "animate-roll-in"),
        sourceFocused && SOURCE_FOCUS_CLASS,
      )}
      data-id={id}
      data-source-focus={sourceFocused ? "true" : undefined}
      data-source-index={message.sourceIndex}
    >
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className={clsx("board-reasoning__head self-start inline-flex items-center gap-1.5 text-xs leading-[1.45] font-medium text-muted hover:text-ink-soft transition-colors select-none", isStreaming && "is-streaming")}
      >
        <Brain01 size={ICON.XS} />
        <span>{message.title || "Reasoning"}</span>
        <ChevronDown
          size={ICON.XS}
          className={clsx("transition-transform duration-trace", expanded && "rotate-180")}
        />
      </button>

      {/* No height tween — the reasoning body can be long markdown, so the
          layout snaps at the presence boundary and only the content
          rises/dissolves on GPU props. */}
      <AnimatePresence initial={false}>
        {expanded && (
          <Reveal key="body" className="min-w-0">
            <Markdown
              content={smoothContent}
              className="board-reasoning__body typeset-compact mt-2 text-muted italic break-words"
            />
          </Reveal>
        )}
      </AnimatePresence>
    </article>
  );
});
