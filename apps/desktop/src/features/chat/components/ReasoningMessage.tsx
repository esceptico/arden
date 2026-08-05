import { memo, useMemo, useState } from "react";
import { Brain01, ChevronDown } from "@/components/icons";
import clsx from "clsx";
import { useStore } from "@/stores";
import { Markdown } from "@/components/ui/Markdown";
import { Collapse } from "@/components/ui/Collapse";
import { ThinkingStep } from "@/components/ui/ThinkingStep";
import { ICON } from "@/lib/icons";
import { useSmoothStreamedContent } from "@/features/chat/hooks/useSmoothStream";
import { parseReasoningSteps } from "@/features/chat/lib/reasoningSteps";
import {
  SOURCE_FOCUS_CLASS,
  entryAnimation,
  useIsLast,
  useMessage,
  useSourceFocused,
} from "@/features/chat/lib/messageShared";

/** A reasoning step is a peer of the tool calls it sits between, not a tree
 *  above them: same `ThinkingStep` row, same glyph gutter, same text axis.
 *
 *  Fluid Functionalism's thinking-steps groups many steps under one "Thinking"
 *  disclosure — but a reasoning message here carries the summary of a single
 *  model round, usually one part, so that wrapper spent two levels of indent
 *  to show one line. The step's own label is the row; its body is the detail
 *  line beneath, exactly like a tool call's. */
export const ReasoningMessage = memo(function ReasoningMessage({ id }: { id: string }) {
  const message = useMessage(id);
  const isLast = useIsLast(id);
  const running = useStore((s) => s.running);
  const sourceFocused = useSourceFocused(id);
  const [expandedStep, setExpandedStep] = useState<number | null>(null);
  const isStreaming = isLast && running;
  // Only run the rAF loop when the user can actually see it.
  const smoothContent = useSmoothStreamedContent(message?.content ?? "", isStreaming);
  const steps = useMemo(() => parseReasoningSteps(smoothContent), [smoothContent]);
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
      {steps.map((step, index) => {
        const active = isStreaming && index === steps.length - 1;
        const open = expandedStep === index;
        return (
          <ThinkingStep
            key={index}
            node={<Brain01 size={14} />}
            last={index === steps.length - 1}
            className="board-trace-row rounded-[var(--r-row)] px-0.5 py-1.5 transition-colors hover:bg-surface-soft/60"
          >
            <button
              type="button"
              onClick={() => setExpandedStep(open ? null : index)}
              aria-expanded={step.body ? open : undefined}
              disabled={!step.body}
              className="flex w-full min-w-0 items-center gap-1.5 border-0 bg-transparent p-0 text-left disabled:cursor-default"
            >
              <span
                className={clsx(
                  "board-reasoning__step-label truncate",
                  active && "dp-running-text",
                )}
              >
                {step.label ?? "Thinking"}
                {active && "…"}
              </span>
              {step.body && (
                <ChevronDown
                  size={ICON.XS}
                  className={clsx(
                    "shrink-0 text-faint transition-transform duration-trace",
                    open && "rotate-180",
                  )}
                />
              )}
            </button>
            {step.body && (
              <Collapse open={open} mode="height">
                <Markdown
                  content={step.body}
                  className="board-reasoning__step-body typeset-compact break-words"
                />
              </Collapse>
            )}
          </ThinkingStep>
        );
      })}
    </article>
  );
});
