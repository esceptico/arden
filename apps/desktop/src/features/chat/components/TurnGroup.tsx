import { useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { useShallow } from "zustand/react/shallow";
import { useStore } from "@/stores";
import { Message } from "@/features/chat/components/Message";
import { ActivityHeader } from "@/features/chat/components/ActivityHeader";
import { turnLayout } from "@/features/chat/lib/turnLayout";
import { turnHeaderLabel } from "@/features/chat/lib/turnHeader";
import { turnHasActiveChildAgent } from "@/features/chat/lib/turnActiveAgents";
import {
  BLUR,
  DISTANCE,
  EASE_ACCELERATE,
  EASE_DISSOLVE,
  MOTION,
} from "@/lib/tokens/motion";
import { sourceRefsForTurn } from "@/stores/sourceRefs";

export function TurnGroup({
  turnId,
  userId,
  childIds,
  onManualResize,
}: {
  turnId: string;
  userId: string | null;
  childIds: string[];
  onManualResize?: () => void;
}) {
  const turn = useStore((s) => s.messages.get(turnId)?.turn);
  const motionDisabled = useStore((s) =>
    Boolean(s.messages.get(turnId)?.suppressEntryMotion || s.streamReplaying),
  );

  const childSummaryKeys = useStore(
    useShallow((s) =>
      childIds.map((id) => {
        const message = s.messages.get(id);
        return `${message?.role ?? ""}\t${message?.activity?.label ?? ""}\t${message?.activity?.items.length ?? 0}`;
      }),
    ),
  );
  const childSummaries = useMemo(
    () =>
      childSummaryKeys.map((key) => {
        const [role, activityLabel, activityCount] = key.split("\t");
        return {
          role: role || null,
          activityLabel: activityLabel || null,
          activityCount: Number(activityCount) || 0,
        };
      }),
    [childSummaryKeys],
  );
  const children = useMemo(
    () => childIds.map((id, index) => ({ id, role: childSummaries[index]?.role ?? null })),
    [childIds, childSummaries],
  );

  // Only group into a "Worked Xs" block when the turn actually invoked
  // tools. A turn with just reasoning + a final reply has no work to
  // collapse — render its children inline instead.
  const hasTools = children.some((child) => child.role === "activity");
  const sourceRefsRevision = useStore((s) => s.sourceRefsRevision);
  const sourceCount = useMemo(
    () => sourceRefsForTurn(useStore.getState().messages, childIds).length,
    [childIds, sourceRefsRevision],
  );

  const hasActiveChildAgent = useStore((s) =>
    turnHasActiveChildAgent({
      childIds,
      messages: s.messages,
      backgroundAgents: s.backgroundAgents.rows,
      sessionId: s.currentSessionId,
    }),
  );
  const isDone = turn?.endedAt != null && !hasActiveChildAgent;
  // Default historic turns to collapsed; default in-progress turns to expanded.
  const [expanded, setExpanded] = useState(!isDone);

  // Auto-collapse the moment the run finishes.
  const wasDone = useRef(isDone);
  useEffect(() => {
    if (!wasDone.current && isDone) setExpanded(false);
    wasDone.current = isDone;
  }, [isDone]);

  const layout = hasTools
    ? turnLayout({ children, isDone })
    : {
        workIds: [],
        afterWorkIds: childIds,
        finalAssistantId: lastAssistantId(children),
      };
  const hasWork = layout.workIds.length > 0;
  // Live runs measure durationMs directly; history derives it from message
  // stamps. Falls back to plain "Worked" when neither yields a time.
  const wasStopped = childSummaries.some((child) => child.activityLabel === "Stopped");
  const headerLabel = turnHeaderLabel(turn?.durationMs, wasStopped);
  const activityCount = childSummaries.reduce(
    (total, child) => total + (child.role === "activity" ? child.activityCount : 0),
    0,
  );

  const showInterim = !isDone || expanded;
  const interimList = (
    <div className="board-trace__list">
      {layout.workIds.map((id) => (
        <Message key={id} id={id} isFinal={false} hideActivityHeader />
      ))}
    </div>
  );
  const workBlock = hasWork ? (
    <section className="board-trace" aria-label={headerLabel}>
      <ActivityHeader
        done
        label={wasStopped ? "Stopped" : undefined}
        count={activityCount}
        durationMs={turn?.durationMs}
        motionDisabled={motionDisabled}
        expanded={expanded}
        railAnchor
        railLabel={headerLabel}
        onToggle={() => {
          onManualResize?.();
          setExpanded((value) => !value);
        }}
      />

      {isDone ? (
        <AnimatePresence initial={false}>
          {showInterim && (
            <motion.div
              key="interim"
              className="board-trace__panel"
              initial={motionDisabled
                ? false
                : { opacity: 0, filter: `blur(${BLUR.dissolve}px)`, y: -DISTANCE.dissolve }}
              animate={{
                opacity: 1,
                filter: "blur(0px)",
                y: 0,
                transition: {
                  duration: motionDisabled ? 0 : MOTION.dissolve,
                  ease: EASE_DISSOLVE,
                },
              }}
              exit={{
                opacity: 0,
                filter: `blur(${BLUR.dissolve}px)`,
                y: -DISTANCE.dissolve,
                transition: {
                  duration: motionDisabled ? 0 : MOTION.dissolve * 0.46,
                  ease: EASE_ACCELERATE,
                },
              }}
            >
              {interimList}
            </motion.div>
          )}
        </AnimatePresence>
      ) : (
        interimList
      )}
    </section>
  ) : null;

  return (
    <section className="board-turn flex flex-col gap-1.5" data-turn-id={turnId}>
      {userId && <Message id={userId} />}

      {isDone && workBlock}

      {layout.afterWorkIds.map((id) => {
        const isFinal = isDone && id === layout.finalAssistantId;
        return (
          <Message
            key={id}
            id={id}
            isFinal={isFinal}
            {...(isFinal ? { sourceTurnId: turnId, sourceCount } : {})}
          />
        );
      })}

      {!isDone && workBlock}
    </section>
  );
}

function lastAssistantId(children: { id: string; role: string | null }[]): string | null {
  for (let i = children.length - 1; i >= 0; i--) {
    if (children[i].role === "assistant") return children[i].id;
  }
  return null;
}
