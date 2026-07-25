import { memo, useEffect, useRef, useState } from "react";
import { AnimatePresence, motion, useAnimationControls } from "motion/react";
import { ArrowTurnForward, ChevronDown, PencilEdit02, X } from "@/components/icons";
import {
  cancelQueuedMessage,
  moveQueuedMessage,
  promoteQueuedMessageToSteer,
} from "@/actions/messages";
import { useStore, type QueuedMessage } from "@/stores";
import { ICON } from "@/lib/icons";
import {
  QUEUE_ITEM_HEIGHT_PX,
  QUEUE_SPRING,
  QUEUE_STACK_GAP_PX,
  QUEUE_STACK_MAX_PEEKS,
  QUEUE_STACK_PEEK_PX,
  QUEUE_STACK_SCALE_STEP,
} from "@/features/chat/lib/queue";

interface QueueCardProps {
  onEdit: (message: QueuedMessage) => void;
}

/** Direct port of Fluid Functionalism's queued-chat stack controller. */
export const QueueCard = memo(function QueueCard({ onEdit }: QueueCardProps) {
  const queued = useStore((state) => state.queuedMessages);
  const stackCount = queued.length;
  const collapsedStackHeight =
    QUEUE_ITEM_HEIGHT_PX
    + Math.min(Math.max(stackCount - 1, 0), QUEUE_STACK_MAX_PEEKS) * QUEUE_STACK_PEEK_PX;
  const expandedStackHeight =
    stackCount * QUEUE_ITEM_HEIGHT_PX
    + Math.max(stackCount - 1, 0) * QUEUE_STACK_GAP_PX;
  const stackRef = useRef<HTMLDivElement>(null);
  const [stackHovered, setStackHovered] = useState(false);
  const [pointerDownId, setPointerDownId] = useState<string | null>(null);
  const [draggingId, setDraggingId] = useState<string | null>(null);
  const [dragY, setDragY] = useState(0);
  const dragStartYRef = useRef(0);
  const queueLengthRef = useRef(stackCount);
  queueLengthRef.current = stackCount;

  const [isTouch, setIsTouch] = useState(false);
  const [tapExpanded, setTapExpanded] = useState(false);
  useEffect(() => {
    const media = window.matchMedia?.("(hover: none)");
    if (!media) return;
    const update = () => setIsTouch(media.matches);
    update();
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);
  useEffect(() => {
    if (stackCount === 0) setTapExpanded(false);
  }, [stackCount]);

  const stackExpanded =
    stackHovered || pointerDownId !== null || draggingId !== null || tapExpanded;
  const slotY = (index: number) =>
    -index * (QUEUE_ITEM_HEIGHT_PX + QUEUE_STACK_GAP_PX);

  const stackBump = useAnimationControls();
  const previousCountRef = useRef(stackCount);
  useEffect(() => {
    const previous = previousCountRef.current;
    previousCountRef.current = stackCount;
    if (stackCount > previous && previous > 0 && !stackExpanded) {
      stackBump.set({ y: -7 });
      void stackBump.start({
        y: 0,
        transition: { type: "spring", duration: 0.42, bounce: 0.5 },
      });
    }
  }, [stackBump, stackCount, stackExpanded]);

  useEffect(() => {
    if (!pointerDownId) return;
    let started = false;
    const onMove = (event: PointerEvent) => {
      const element = stackRef.current;
      if (!element) return;
      if (!started) {
        if (Math.abs(event.clientY - dragStartYRef.current) < 4) return;
        started = true;
        setDraggingId(pointerDownId);
      }
      const rect = element.getBoundingClientRect();
      const fromBottom = rect.bottom - event.clientY;
      const slot = Math.max(
        0,
        Math.min(
          queueLengthRef.current - 1,
          Math.floor(fromBottom / (QUEUE_ITEM_HEIGHT_PX + QUEUE_STACK_GAP_PX)),
        ),
      );
      moveQueuedMessage(pointerDownId, slot);
      const minY =
        -(queueLengthRef.current - 1) * (QUEUE_ITEM_HEIGHT_PX + QUEUE_STACK_GAP_PX);
      setDragY(
        Math.max(
          minY,
          Math.min(0, event.clientY - rect.bottom + QUEUE_ITEM_HEIGHT_PX / 2),
        ),
      );
    };
    const onUp = () => {
      setPointerDownId(null);
      setDraggingId(null);
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    window.addEventListener("pointercancel", onUp);
    return () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("pointercancel", onUp);
    };
  }, [pointerDownId]);

  return (
    <AnimatePresence>
      {stackCount > 0 && (
        <motion.div
          ref={stackRef}
          className="board-queue-stack"
          role="list"
          aria-label="Queued messages"
          initial={{ opacity: 0 }}
          animate={{
            opacity: 1,
            height: stackExpanded ? expandedStackHeight : collapsedStackHeight,
          }}
          exit={{ opacity: 0 }}
          transition={QUEUE_SPRING}
          onMouseEnter={() => setStackHovered(true)}
          onMouseLeave={() => setStackHovered(false)}
        >
          <motion.div animate={stackBump} className="board-queue-stack__deck">
            {isTouch && stackExpanded && (
              <button
                type="button"
                className="board-queue-stack__gutter board-queue-stack__collapse"
                onPointerDown={(event) => event.stopPropagation()}
                onClick={(event) => {
                  event.stopPropagation();
                  setTapExpanded(false);
                }}
                aria-label="Collapse queued messages"
                title="Collapse queued messages"
              >
                <ChevronDown size={ICON.MD} strokeWidth={2} />
              </button>
            )}

            <AnimatePresence initial={false}>
              {queued.map((message, index) => {
                const peek = Math.min(index, QUEUE_STACK_MAX_PEEKS);
                const isDragging = draggingId === message.clientId;
                const target = stackExpanded
                  ? {
                      y: isDragging ? dragY : slotY(index),
                      scale: isDragging ? 1.03 : 1,
                      opacity: 1,
                    }
                  : {
                      y: -peek * QUEUE_STACK_PEEK_PX,
                      scale: 1 - peek * QUEUE_STACK_SCALE_STEP,
                      opacity: index <= QUEUE_STACK_MAX_PEEKS ? 1 : 0,
                    };
                const content =
                  message.text
                  || (message.images?.length
                    ? `${message.images.length} attachment${message.images.length === 1 ? "" : "s"}`
                    : "");

                return (
                  <motion.article
                    key={message.clientId}
                    role="listitem"
                    className="board-queue-stack__item"
                    data-status={message.status}
                    onDoubleClick={() => onEdit(message)}
                    onClick={() => {
                      if (isTouch && !stackExpanded) setTapExpanded(true);
                    }}
                    onPointerDown={(event) => {
                      if (!stackExpanded || event.button !== 0) return;
                      dragStartYRef.current = event.clientY;
                      setDragY(slotY(index));
                      setPointerDownId(message.clientId);
                    }}
                    initial={{ opacity: 0, y: 14, scale: 0.96 }}
                    animate={target}
                    exit={{
                      opacity: 0,
                      scale: 0.9,
                      transition: { duration: 0.12 },
                    }}
                    transition={isDragging ? { duration: 0 } : QUEUE_SPRING}
                    style={{
                      zIndex: isDragging ? 200 : 100 - index,
                      cursor: stackExpanded ? "grab" : "default",
                      touchAction: stackExpanded ? "none" : undefined,
                      pointerEvents:
                        stackExpanded || index <= QUEUE_STACK_MAX_PEEKS ? "auto" : "none",
                    }}
                    aria-label={`Queued message: ${content}`}
                  >
                    {message.images && message.images.length > 0 && (
                      <span className="board-queue-stack__thumbnails" aria-hidden>
                        {message.images.slice(0, 3).map((image, imageIndex) => (
                          <img
                            key={`${image.media_type}-${imageIndex}`}
                            src={`data:${image.media_type};base64,${image.data}`}
                            alt=""
                          />
                        ))}
                        {message.images.length > 3 && (
                          <span>+{message.images.length - 3}</span>
                        )}
                      </span>
                    )}
                    <strong className={message.status === "failed" ? "is-failed" : undefined}>
                      {content}
                    </strong>
                    <span className="board-queue-stack__actions">
                      <button
                        type="button"
                        onPointerDown={(event) => event.stopPropagation()}
                        onClick={(event) => {
                          event.stopPropagation();
                          void promoteQueuedMessageToSteer(message.clientId);
                        }}
                        disabled={message.status === "sending"}
                        aria-label="Send queued message now"
                        title="Send now"
                      >
                        <ArrowTurnForward size={ICON.SM} strokeWidth={2} />
                      </button>
                      <button
                        type="button"
                        onPointerDown={(event) => event.stopPropagation()}
                        onClick={(event) => {
                          event.stopPropagation();
                          onEdit(message);
                        }}
                        disabled={message.status === "sending"}
                        aria-label="Edit queued message"
                        title="Edit queued message"
                      >
                        <PencilEdit02 size={ICON.SM} />
                      </button>
                      <button
                        type="button"
                        onPointerDown={(event) => event.stopPropagation()}
                        onClick={(event) => {
                          event.stopPropagation();
                          cancelQueuedMessage(message.clientId);
                        }}
                        disabled={message.status === "sending"}
                        aria-label="Remove queued message"
                        title="Remove from queue"
                      >
                        <X size={ICON.SM} strokeWidth={2.5} />
                      </button>
                    </span>
                  </motion.article>
                );
              })}
            </AnimatePresence>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
});
