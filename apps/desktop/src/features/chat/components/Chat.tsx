import { useLayoutEffect, useRef } from "react";
import { ArrowLeft02 } from "@/components/icons";
import { useStore } from "@/stores";
import { switchSession } from "@/actions/sessions";
import { Messages } from "@/features/chat/components/Messages";
import { Composer } from "@/features/chat/components/Composer";
import { ApprovalBanner } from "@/features/chat/components/ApprovalBanner";
import { ConnectionBanner } from "@/features/chat/components/ConnectionBanner";
import { TriageChip } from "@/features/chat/components/TriageChip";
import { useChatTriage } from "@/hooks/useChatTriage";
import { Button } from "@/components/ui/Button";
import { ICON } from "@/lib/icons";
import { queueStackExtentPx } from "@/features/chat/lib/queue";

function ChatHeader() {
  const sessionId = useStore((s) => s.currentSessionId);
  const sessions = useStore((s) => s.sessions);
  const session = sessions.find((s) => s.session_id === sessionId);

  const title = session?.name || (sessionId ? "untitled" : "no session");

  // A child agent session gets a breadcrumb back to its parent in the header —
  // the discoverable spot, mirroring the hub's "← parent" chip.
  const parentId = session?.parent_session_id ?? null;
  const isAgent = session?.session_type === "agent" || !!parentId;
  const parentName =
    (parentId ? sessions.find((s) => s.session_id === parentId)?.name : null)?.trim() || "parent session";

  return (
    <header className="board-chat__header board-chat-header">
      <div className="board-chat__header-content chat-head-inner flex min-w-0 flex-1 items-center gap-1.5">
        {isAgent && parentId && (
          <>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => void switchSession(parentId)}
              title={`Back to ${parentName}`}
              className="board-chat__crumb group/back shrink-0 max-w-[180px] -ml-0.5"
            >
              <ArrowLeft02
                size={ICON.SM}
                className="shrink-0 text-faint transition-colors group-hover/back:text-ink"
              />
              <span className="truncate">{parentName}</span>
            </Button>
            <span className="shrink-0 text-faint select-none" aria-hidden>
              /
            </span>
          </>
        )}
        <h1 className="board-chat-title m-0">
          {title}
        </h1>
      </div>
    </header>
  );
}

export function Chat() {
  const sessionId = useStore((s) => s.currentSessionId);
  const queueExtent = useStore((s) => queueStackExtentPx(s.queuedMessages.length));
  useChatTriage();

  // Composer overlays the bottom of the message scroll area. The scroll
  // area needs padding-bottom equal to the bottom stack's actual height plus
  // the queue's absolutely staged extent, so the last message clears both.
  // Height is dynamic
  // (textarea auto-resize, approval banner appears/disappears), so we
  // observe it and write to `--chat-bottom-h` consumed by
  // `.board-chat__scroll` padding-bottom + the jump-to-bottom pill offset.
  const bottomStackRef = useRef<HTMLDivElement>(null);
  // useLayoutEffect (not useEffect) so the measured height is written
  // BEFORE first paint — otherwise the scroll-padding briefly uses the
  // CSS fallback (96px) and the pill uses its inline fallback (96px),
  // causing a one-frame jump when the real composer is taller. Per
  // Codex review.
  useLayoutEffect(() => {
    const el = bottomStackRef.current;
    if (!el) return;
    const apply = (height: number) => {
      document.documentElement.style.setProperty("--chat-bottom-h", `${height + queueExtent}px`);
    };
    apply(el.getBoundingClientRect().height);
    const ro = new ResizeObserver((entries) => {
      const h = entries[0]?.contentRect.height ?? 0;
      apply(h);
    });
    ro.observe(el);
    return () => {
      ro.disconnect();
      document.documentElement.style.removeProperty("--chat-bottom-h");
    };
  }, [queueExtent]);

  return (
    <section className="board-chat" aria-label="Conversation">
      <div className="board-chat__stage relative w-full h-full">
        <Messages key={sessionId ?? "none"} />
        <div
          aria-hidden
          className="board-chat__bottom-fade absolute left-0 right-0 bottom-0 pointer-events-none z-[var(--z-raised)]"
          style={{ height: "calc(var(--chat-bottom-h, 96px) + 24px)" }}
        />
        <div
          className="board-chat__header-layer absolute top-0 left-0 right-0 z-[var(--z-sticky)]"
          data-page-enter-item="chrome"
        >
          <ChatHeader />
        </div>
        <div
          ref={bottomStackRef}
          className="board-chat__bottom-stack absolute bottom-0 left-0 right-0 z-[var(--z-popover)]"
          data-page-enter-item
        >
          <TriageChip />
          <ConnectionBanner />
          <ApprovalBanner />
          <Composer />
        </div>
      </div>
    </section>
  );
}
