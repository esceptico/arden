import { useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { ChevronDown } from "@/components/icons";
import clsx from "clsx";
import { useShallow } from "zustand/react/shallow";
import { useStore } from "@/stores";
import { Message } from "@/features/chat/components/Message";
import { turnLayout } from "@/features/chat/lib/turnLayout";
import { turnHeaderLabel } from "@/features/chat/lib/turnHeader";
import { turnHasActiveChildAgent } from "@/features/chat/lib/turnActiveAgents";
import { SPRING_ROW_ENTRY } from "@/lib/tokens/motion";
import { Collapse } from "@/components/ui/Collapse";
import { Reveal } from "@/components/ui/Reveal";
import { Marker, MarkerContent } from "@/components/ui/Marker";
import { ICON } from "@/lib/icons";
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
        return `${message?.role ?? ""}\t${message?.activity?.label ?? ""}`;
      }),
    ),
  );
  const childSummaries = useMemo(
    () =>
      childSummaryKeys.map((key) => {
        const [role, activityLabel] = key.split("\t");
        return {
          role: role || null,
          activityLabel: activityLabel || null,
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
  // Live runs have a real durationMs; historic ones don't (we don't persist
  // turn timing). Show the time when we have it, plain "Worked" otherwise.
  const wasStopped = childSummaries.some((child) => child.activityLabel === "Stopped");
  const headerLabel = turnHeaderLabel(turn?.durationMs, wasStopped);

  const showInterim = !isDone || expanded;
  // Stagger sibling reveals per Rauno's Depth essay ("Stagger sibling
  // fades — Synchronous fade hides quantity; stagger by ~30–60ms").
  //
  // Variant `animate` is gated on `showInterim` so the stagger plays
  // when the parent's collapse opens (Path A: user clicks "Worked for
  // Xs" on a finished turn), not silently under a height:0 mask on
  // initial mount. For live turns `showInterim` is true from the start,
  // so the initial paint also plays the stagger if multiple workIds
  // mount in the same render.
  //
  // Streaming arrivals (workIds added one-by-one over time) don't get
  // motion stagger — parent is already at "visible" so new children
  // inherit the steady state directly. Hydrated/replayed turns disable
  // this initial stagger; live rows keep their per-article entry motion.
  const interimList = (
    <motion.div
      initial={motionDisabled ? false : "hidden"}
      animate={showInterim ? "visible" : "hidden"}
      variants={{
        hidden: {},
        visible: { transition: { staggerChildren: 0.045 } },
      }}
      className={clsx("flex flex-col gap-3.5", isDone && "pt-1.5")}
    >
      {layout.workIds.map((id) => (
        <motion.div
          key={id}
          variants={
            motionDisabled
              ? undefined
              : {
                  hidden: { opacity: 0, y: 4 },
                  visible: { opacity: 1, y: 0 },
                }
          }
          transition={motionDisabled ? { duration: 0 } : SPRING_ROW_ENTRY}
        >
          <Message id={id} isFinal={false} />
        </motion.div>
      ))}
    </motion.div>
  );
  const workBlock = hasWork ? (
    <div className="flex flex-col">
      <Collapse open={isDone}>
        <Marker
          as="button"
          variant="border"
          aria-expanded={expanded}
          onClick={() => {
            onManualResize?.();
            setExpanded((v) => !v);
          }}
          className="transition-colors hover:text-ink-soft"
        >
          <MarkerContent>{headerLabel}</MarkerContent>
          <ChevronDown
            size={ICON.XS}
            strokeWidth={2}
            className={clsx("shrink-0 transition-transform duration-trace", expanded && "rotate-180")}
          />
        </Marker>
      </Collapse>

      {/* No height tween here — the interim subtree is the heaviest in the
          app, so the layout snaps once at the presence boundary and only the
          content rises/dissolves on GPU props. */}
      {isDone ? (
        <AnimatePresence initial={false}>
          {showInterim && (
            <Reveal key="interim">
              {interimList}
            </Reveal>
          )}
        </AnimatePresence>
      ) : (
        interimList
      )}
    </div>
  ) : null;

  return (
    <div className="flex flex-col gap-1.5" data-turn-id={turnId}>
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
    </div>
  );
}

function lastAssistantId(children: { id: string; role: string | null }[]): string | null {
  for (let i = children.length - 1; i >= 0; i--) {
    if (children[i].role === "assistant") return children[i].id;
  }
  return null;
}
