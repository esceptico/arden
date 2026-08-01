import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import clsx from "clsx";
import { ChevronDown, Loader2 } from "@/components/icons";
import { useStickToBottom } from "use-stick-to-bottom";
import { useShallow } from "zustand/react/shallow";
import { useStore } from "@/stores";
import { messagesScroll } from "@/lib/messagesScroll";
import { visibleMessageIds } from "@/lib/messageVisibility";
import { messageSegments } from "@/lib/messageSegments";
import { firstMessageIdInSourceFocus } from "@/lib/messageSourceFocus";
import { loadNewerHistory, loadOlderHistory } from "@/actions/history";
import { useChildAgentResults } from "@/hooks/useChildAgentResults";
import { MOTION, EASE_EMPHASIZED, EASE_OUT, SPRING_SCROLL_RIVER } from "@/lib/tokens/motion";
import { BlurSwap } from "@/components/ui/BlurSwap";
import { Marker, MarkerContent, MarkerIcon } from "@/components/ui/Marker";
import { Message } from "@/features/chat/components/Message";
import { CompactionIndicator } from "@/features/chat/components/CompactionIndicator";
import { TurnGroup } from "@/features/chat/components/TurnGroup";
import { ChatRail } from "@/features/chat/components/ChatRail";
import { ScrollBlurTop } from "@/components/ui/ScrollBlur";
import { ICON } from "@/lib/icons";

// Parent (Chat) remounts this component on session change via key={sessionId}
// so each session starts fresh — no carryover scroll state.
export function Messages() {
  const order = useStore((s) => s.order);
  const currentSessionId = useStore((s) => s.currentSessionId);
  const historyLoadedFor = useStore((s) => s.sessionView.historyLoadedFor);
  const sourceFocus = useStore((s) => s.sourceFocus);
  const historyPaging = useStore(
    useShallow((s) => ({
      hasMoreBefore: s.sessionView.historyHasMoreBefore,
      hasMoreAfter: s.sessionView.historyHasMoreAfter,
      loadingBefore: s.sessionView.historyLoadingBefore,
      loadingAfter: s.sessionView.historyLoadingAfter,
    })),
  );
  const sessionReady = currentSessionId === null || historyLoadedFor === currentSessionId;
  // Terminal agents' durable result previews feed the trace's agent rows (a
  // detached agent's inline item.result is only the spawn ack). Same shared
  // cache the sidebar hub fills — mounted once here, not per row.
  const sessionRosterAgents = useStore(
    useShallow((s) =>
      Object.values(s.backgroundAgents.rows).filter(
        (agent) => agent.sessionId === s.currentSessionId,
      ),
    ),
  );
  useChildAgentResults(currentSessionId ?? "", sessionRosterAgents);
  const firstSourceFocusId = useStore((s) =>
    firstMessageIdInSourceFocus(s.order, s.messages, s.sourceFocus, s.currentSessionId),
  );

  const { scrollRef, contentRef, scrollToBottom, stopScroll, isNearBottom } = useStickToBottom({
    initial: "instant",
    resize: SPRING_SCROLL_RIVER,
  });
  const topAnchorRef = useRef<{ height: number; top: number } | null>(null);

  const handleScroll = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    if (el.scrollTop < 120 && historyPaging.hasMoreBefore && !historyPaging.loadingBefore) {
      topAnchorRef.current = { height: el.scrollHeight, top: el.scrollTop };
      void loadOlderHistory();
    }

    const bottomGap = el.scrollHeight - el.clientHeight - el.scrollTop;
    if (bottomGap < 120 && historyPaging.hasMoreAfter && !historyPaging.loadingAfter) {
      void loadNewerHistory();
    }
  }, [historyPaging.hasMoreAfter, historyPaging.hasMoreBefore, historyPaging.loadingAfter, historyPaging.loadingBefore, scrollRef]);

  // First time content lands after mount (loadHistory fills `order`),
  // snap instantly. Without this, the library treats the empty→full
  // growth as a resize and smooth-scrolls from 0 to bottom — visible
  // as a "scroll from the top" on session switch.
  const seenContentRef = useRef(false);
  useLayoutEffect(() => {
    if (seenContentRef.current || !sessionReady || order.length === 0) return;
    seenContentRef.current = true;
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
    scrollToBottom({ animation: "instant" });
  }, [order.length, scrollRef, scrollToBottom, sessionReady]);

  useLayoutEffect(() => {
    const anchor = topAnchorRef.current;
    const el = scrollRef.current;
    if (!anchor || !el) return;
    el.scrollTop = el.scrollHeight - anchor.height + anchor.top;
    topAnchorRef.current = null;
  }, [order.length, scrollRef]);

  // Expose for actions.sendMessage to force-scroll on user send.
  useEffect(() => {
    messagesScroll.scrollToBottom = (behavior) =>
      scrollToBottom({ animation: behavior === "instant" ? "instant" : "smooth" });
    return () => {
      messagesScroll.scrollToBottom = null;
    };
  }, [scrollToBottom]);

  useEffect(() => {
    if (!sourceFocus || sourceFocus.sessionId !== currentSessionId || !firstSourceFocusId) return;
    const frame = requestAnimationFrame(() => {
      const target = scrollRef.current?.querySelector<HTMLElement>('[data-source-focus="true"]');
      target?.scrollIntoView({ block: "center", behavior: "smooth" });
    });
    return () => cancelAnimationFrame(frame);
  }, [currentSessionId, firstSourceFocusId, scrollRef, sourceFocus]);

  // Intent-aware "new messages while detached" counter. While the user
  // is near-bottom, advance `seenLastIdRef` to the tail — that's the
  // marker for "everything up to here has been seen." When they scroll
  // up, the marker freezes; the count is the number of ids appended
  // after it. History paging prepends to `order`, so counting by tail
  // index (not raw length) is critical — older-history loads grow the
  // array but don't add unread material.
  const seenLastIdRef = useRef<string | null>(null);
  const [unreadCount, setUnreadCount] = useState(0);
  useEffect(() => {
    const lastId = order[order.length - 1] ?? null;
    if (isNearBottom) {
      seenLastIdRef.current = lastId;
      if (unreadCount !== 0) setUnreadCount(0);
      return;
    }
    // Detached. Anchor the marker on first transition into this state
    // so we count only what arrives going forward.
    if (!seenLastIdRef.current) {
      seenLastIdRef.current = lastId;
      return;
    }
    const seenIdx = order.indexOf(seenLastIdRef.current);
    if (seenIdx === -1) {
      // Marker fell out of `order` (history replay / session reload).
      // Re-anchor and stop counting from now.
      seenLastIdRef.current = lastId;
      if (unreadCount !== 0) setUnreadCount(0);
      return;
    }
    const tail = order.length - 1 - seenIdx;
    if (tail !== unreadCount) setUnreadCount(tail);
  }, [order, isNearBottom, unreadCount]);

  const onPillClick = useCallback(() => {
    setUnreadCount(0);
    scrollToBottom({ animation: "smooth" });
  }, [scrollToBottom]);

  const roles = useStore(
    useShallow((s) => order.map((id) => s.messages.get(id)?.role ?? null)),
  );
  const metaFlags = useStore(
    useShallow((s) => order.map((id) => Boolean(s.messages.get(id)?.isMeta))),
  );
  // visibleMessageIds only reads `content` to detect an EMPTY assistant message
  // (isHiddenTranscriptMessage), so collapse it to an emptiness-stable marker
  // instead of the full string. This keeps the array shallow-equal as a
  // streaming assistant message grows token-by-token — without it, the live
  // message's content changed every tick, invalidating visibleOrder + segments
  // and re-deriving over the entire (49k-message) order on every token. Marker
  // is "" vs "x" so the empty/non-empty decision is identical.
  const contentFlags = useStore(
    useShallow((s) =>
      order.map((id) => {
        const message = s.messages.get(id);
        if (message?.role !== "assistant") return "";
        return (message.content ?? "").trim().length > 0 ? "x" : "";
      }),
    ),
  );
  const visibleOrder = useMemo(
    () => visibleMessageIds({ ids: order, roles, metaFlags, contents: contentFlags }),
    [order, roles, metaFlags, contentFlags],
  );

  const segments = useMemo(
    () => messageSegments({ ids: order, roles, metaFlags, visibleIds: visibleOrder }),
    [order, roles, metaFlags, visibleOrder],
  );

  const turnIds = useMemo(
    () => segments.flatMap((seg) => (seg.userId ? [seg.userId] : [])),
    [segments],
  );

  return (
    <div className="board-chat__messages absolute inset-0 @container">
      <div
        ref={scrollRef}
        onScroll={handleScroll}
        data-page-enter-item
        data-page-skeleton
        className="board-chat__scroll absolute inset-0 overflow-y-auto overflow-x-hidden px-0"
      >
        <div ref={contentRef} className="board-chat__lane mx-auto max-w-[760px] min-w-0 px-7 flex flex-col gap-2">
          {/* Older-history paging feedback: the scroll-anchor restore keeps the
              viewport still while pages prepend, so without this row a top-of-
              scroll load is invisible until content pops in. */}
          {sessionReady && visibleOrder.length > 0 && historyPaging.loadingBefore && (
            <Marker variant="separator" role="status" className="my-2">
              <MarkerIcon>
                <Loader2 strokeWidth={2} className="animate-spin" />
              </MarkerIcon>
              <MarkerContent>Loading earlier messages…</MarkerContent>
            </Marker>
          )}
          {/* App.tsx renders <Home /> in place of <Chat /> (and therefore
              this component) when visibleOrder is empty and nothing is
              running — see the seam note there. Messages still renders
              nothing extra for that state as a defensive fallback (e.g. a
              run starts on an empty session — the app layer's `running`
              guard un-shows Home before this line would matter). */}
          {!sessionReady
            ? null
            : segments.map((seg, index) =>
                seg.userId
                  ? <TurnGroup key={seg.turnId} turnId={seg.turnId!} userId={seg.userId} childIds={seg.childIds} onManualResize={stopScroll} />
                  : seg.turnId
                    ? <TurnGroup key={seg.turnId} turnId={seg.turnId} userId={null} childIds={seg.childIds} onManualResize={stopScroll} />
                  : <div key={`preamble-${index}`} className="contents">{seg.childIds.map((id) => <Message key={id} id={id} />)}</div>
              )}
          <CompactionIndicator />
        </div>
      </div>
      {sessionReady && <ChatRail turnIds={turnIds} scrollRef={scrollRef} />}
      <ScrollBlurTop scrollerRef={scrollRef} />
      <AnimatePresence>
        {!isNearBottom && order.length > 0 && (
          // Visibility and content are decoupled: AnimatePresence shows/hides
          // one persistent shell (opacity/scale/y — pure compositor work)
          // while the chevron ↔ "{n} new" content crossfades in place via
          // BlurSwap. Shell colors transition in CSS; width snaps under the
          // blur bridge instead of tweening.
          <motion.button
            key="jump-to-latest"
            type="button"
            onClick={onPillClick}
            initial={{ opacity: 0, y: 6, scale: 0.95 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{
              opacity: 0,
              y: 6,
              scale: 0.95,
              transition: { duration: MOTION.fast, ease: EASE_OUT },
            }}
            transition={{ duration: MOTION.row, ease: EASE_EMPHASIZED }}
            aria-label={unreadCount > 0 ? `${unreadCount} new message${unreadCount === 1 ? "" : "s"} — jump to latest` : "Scroll to bottom"}
            style={{ bottom: "calc(var(--chat-bottom-h, 96px) + 12px)" }}
            className={clsx(
              "board-chat__jump absolute left-1/2 -translate-x-1/2 z-[var(--z-sticky)] inline-flex items-center justify-center h-8 overflow-hidden transition-[background-color,color,scale] duration-check ease-out active:scale-[0.97]",
              unreadCount > 0
                ? "pl-2.5 pr-3 bg-ink text-on-ink hover:bg-ink-soft"
                : "w-8 bg-surface text-muted hover:text-ink hover:bg-surface-soft",
            )}
          >
            <BlurSwap swapKey={unreadCount > 0 ? "unread" : "chevron"} blur={3}>
              {unreadCount > 0 ? (
                <span className="inline-flex items-center gap-1.5 text-sm font-medium">
                  <ChevronDown size={ICON.MD} />
                  <span className="tabular-nums">{unreadCount} new</span>
                </span>
              ) : (
                <ChevronDown size={ICON.MD} />
              )}
            </BlurSwap>
          </motion.button>
        )}
      </AnimatePresence>
    </div>
  );
}
