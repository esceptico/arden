import { memo } from "react";
import clsx from "clsx";
import { Library } from "lucide-react";
import { useStore } from "@/stores";
import { Markdown } from "@/components/ui/Markdown";
import { useSmoothStreamedContent } from "@/features/chat/hooks/useSmoothStream";
import { MessageActions } from "@/features/chat/components/MessageActions";
import { ProofSummary } from "@/features/context/components/ProofSummary";
import {
  SOURCE_FOCUS_CLASS,
  entryAnimation,
  useMessage,
  useSourceFocused,
} from "@/features/chat/lib/messageShared";
import { ICON } from "@/lib/icons";

export const AssistantMessage = memo(function AssistantMessage({
  id,
  isFinal = true,
  sourceTurnId,
  sourceCount = 0,
}: {
  id: string;
  isFinal?: boolean;
  sourceTurnId?: string;
  sourceCount?: number;
}) {
  const message = useMessage(id);
  const sourceFocused = useSourceFocused(id);
  const running = useStore((s) => s.running);
  const openSourcesForTurn = useStore((s) => s.openSourcesForTurn);
  const isStreaming = Boolean(message && running && message.turn?.endedAt === null);
  // Hook order rule: call before any conditional return below.
  const smoothContent = useSmoothStreamedContent(message?.content ?? "", isStreaming);
  if (!message) return null;
  // Drop intermediate assistant messages that finished empty — the model
  // opens TEXT_MESSAGE_START before deciding to tool-call instead, leaving
  // a zero-content article that would otherwise stack ~30px of phantom
  // padding inside the work block.
  if (!isFinal && !isStreaming && !message.content.trim()) return null;
  return (
    <article
      className={clsx(
        "assistant-message group grid grid-cols-[minmax(0,1fr)] gap-1.5 min-w-0 transition-[background-color,box-shadow] duration-panel",
        entryAnimation(message, "animate-fade-in"),
        sourceFocused && SOURCE_FOCUS_CLASS,
      )}
      data-streaming={isStreaming ? "true" : undefined}
      data-id={id}
      data-source-focus={sourceFocused ? "true" : undefined}
      data-source-index={message.sourceIndex}
    >
      <Markdown
        content={smoothContent}
        streaming={isStreaming}
        className="text-base leading-[1.5] text-ink break-words"
      />
      {isFinal && sourceTurnId && <ProofSummary turnId={sourceTurnId} />}
      {isFinal && sourceTurnId && sourceCount > 0 && (
        <button
          type="button"
          onClick={() => openSourcesForTurn(sourceTurnId)}
          aria-label={`Open ${sourceCount} ${sourceCount === 1 ? "source" : "sources"} for this turn`}
          data-source-footer="true"
          className="flex w-fit items-center gap-1.5 rounded-md px-1.5 py-1 text-xs font-medium text-muted transition-colors hover:bg-surface-soft hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
        >
          <Library aria-hidden size={ICON.XS} strokeWidth={1.75} />
          {sourceCount} {sourceCount === 1 ? "source" : "sources"}
        </button>
      )}
      {isFinal && <MessageActions id={id} role="assistant" />}
    </article>
  );
});
