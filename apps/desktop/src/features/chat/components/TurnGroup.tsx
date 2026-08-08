import { useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { useShallow } from "zustand/react/shallow";
import { useStore, type ActivityItem } from "@/stores";
import { isAgent } from "@/lib/agent";
import { Message } from "@/features/chat/components/Message";
import { ActivityHeader } from "@/features/chat/components/ActivityHeader";
import { ItemButton } from "@/features/chat/components/ActivityRows";
import { parseReasoningSteps } from "@/features/chat/lib/reasoningSteps";
import { rollingWorkWindow, turnLayout } from "@/features/chat/lib/turnLayout";
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
        const reasoningRows = message?.role === "reasoning"
          ? Math.max(1, parseReasoningSteps(message.content).length)
          : 0;
        return `${message?.role ?? ""}\t${message?.activity?.label ?? ""}\t${message?.activity?.items.length ?? 0}\t${reasoningRows}`;
      }),
    ),
  );
  const childSummaries = useMemo(
    () =>
      childSummaryKeys.map((key) => {
        const [role, activityLabel, activityCount, reasoningRows] = key.split("\t");
        return {
          role: role || null,
          activityLabel: activityLabel || null,
          activityCount: Number(activityCount) || 0,
          reasoningRows: Number(reasoningRows) || 0,
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
  const workRows = useMemo(() => {
    if (isDone) return layout.workIds.map((id) => ({ id, rowLimit: undefined }));
    const countById = new Map(
      childIds.map((id, index) => {
        const child = childSummaries[index];
        const rowCount = child?.role === "activity"
          ? child.activityCount
          : child?.reasoningRows ?? 0;
        return [id, rowCount] as const;
      }),
    );
    return rollingWorkWindow(
      layout.workIds.map((id) => ({ id, rowCount: countById.get(id) ?? 0 })),
      3,
    );
  }, [childIds, childSummaries, isDone, layout.workIds]);
  // Agents are deliverables, not plumbing: when the settled turn collapses
  // into "Worked", their rows stay visible below the header instead of
  // vanishing with the tool calls. Only when collapsed — expanded shows them
  // in place inside the trace. Signature selector (stable primitive) + a
  // getState() memo, same pattern as sourceCount above.
  const setViewingTool = useStore((s) => s.setViewingTool);
  const agentItemsSig = useStore((s) => {
    if (showInterim) return "";
    let sig = "";
    for (const id of childIds) {
      for (const item of s.messages.get(id)?.activity?.items ?? []) {
        if (isAgent(item)) sig += `${item.id}:${item.taskStatus ?? ""};`;
      }
    }
    return sig;
  });
  const agentItems = useMemo(() => {
    if (!agentItemsSig) return [];
    const items: ActivityItem[] = [];
    for (const id of childIds) {
      for (const item of useStore.getState().messages.get(id)?.activity?.items ?? []) {
        if (isAgent(item)) items.push(item);
      }
    }
    return items;
  }, [agentItemsSig, childIds]);
  const interimList = (
    <div className="board-trace__list">
      {workRows.map(({ id, rowLimit }) => (
        <Message
          key={id}
          id={id}
          isFinal={false}
          hideActivityHeader
          traceRowLimit={rowLimit}
        />
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
        onToggle={() => {
          onManualResize?.();
          setExpanded((value) => !value);
        }}
      />

      {agentItems.length > 0 && (
        <div className="board-trace__list mt-1">
          {agentItems.map((item, i) => (
            <ItemButton
              key={item.id}
              item={item}
              onOpen={setViewingTool}
              last={i === agentItems.length - 1}
            />
          ))}
        </div>
      )}

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
