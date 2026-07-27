import { useMemo, type ReactNode } from "react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { useStore, type ActivityItem } from "@/stores";
import {
  MOTION,
  EASE_DECELERATE,
  EASE_OUT,
  TRACE_ROW_ENTER,
  TRACE_ROW_EXIT,
  TRACE_ROW_TRANSITION,
  TRACE_ROW_VISIBLE,
  RISE_IN,
  RISE_SETTLED,
  DISSOLVE_OUT,
} from "@/lib/tokens/motion";
import { useWorkflows } from "@/hooks/useWorkflows";
import { ExpandableWorkflowCard } from "@/components/ui/WorkflowDetail";
import { HtmlWidgetCard } from "@/features/chat/components/HtmlWidgetCard";
import {
  buildRollingList,
  buildStaticTree,
  groupConsecutiveCalls,
  orderedTraceEntries,
} from "@/features/chat/lib/trace";
import { ItemButton, ToolGroupRow } from "@/features/chat/components/ActivityRows";

export type { ActivityItem };
export type { TraceEntry } from "@/features/chat/lib/trace";
export { orderedTraceEntries, liftWorkflows } from "@/features/chat/lib/trace";
export { ActivityHeader } from "@/features/chat/components/ActivityHeader";
export { AgentUsageSuffix } from "@/features/chat/components/ActivityRows";

const TRACE_ROW_REDUCED_TRANSITION = { duration: MOTION.reduced } as const;

export function ActivityTrace({ children }: { children: ReactNode }) {
  return (
    <div className="board-activity-trace font-sans text-sm leading-[1.4] text-muted">{children}</div>
  );
}

export function ActivityTail({
  items,
  max,
  collapsed = false,
  motionDisabled,
}: {
  items: ActivityItem[];
  max?: number;
  collapsed?: boolean;
  motionDisabled?: boolean;
}) {
  // Two render modes:
  //   - "rolling" (max set): used live during a run. Agent parent rows stay
  //     visible so parallel research agents do not disappear; ordinary tool
  //     rows still keep a short tail at each level. Deeper descendants of a
  //     finished parent are hidden so the tail stays short.
  //   - "static"  (max unset): post-run, expanded list. Flat top-level only —
  //     children are reachable via the inspector.
  const rolling = max != null;
  const setViewingTool = useStore((s) => s.setViewingTool);
  const streamReplaying = useStore((s) => s.streamReplaying);
  const reducedMotion = useReducedMotion() ?? false;
  const suppressMotion = Boolean(motionDisabled || streamReplaying || reducedMotion);

  const sessionId = useStore((s) => s.currentSessionId);
  const workflows = useWorkflows(sessionId);

  // Chronological entries: rows segments interleaved with workflow cards at
  // the position their tool call holds in the trace.
  const entries = useMemo(
    () => orderedTraceEntries(items, workflows, sessionId),
    [items, workflows, sessionId],
  );

  // Rolling (live) mode: do NOT animate the container's height. The chat's
  // scroll container above us uses `useStickToBottom` whose own resize-spring
  // would chase a height-spring's intermediate values over many frames —
  // visible as the "odd animation above the chat". Instead let the container
  // resize instantly as rows mount/unmount (one reflow per tool, not 30) and
  // animate only paint-layer properties plus sibling FLIP transforms. Every
  // `layout="position"` below deliberately excludes size, so no container
  // height animation competes with the scroll river.
  //
  // Entry keys: cards key by workflowId; row segments key by their first item
  // so inserting a card between segments preserves the existing FLIP peers.
  if (rolling) {
    return (
      <div className="board-trace__rolling relative">
        <AnimatePresence initial={false} mode="popLayout">
          {entries.map((entry) =>
            entry.kind === "workflow" ? (
              <motion.div
                key={`wf:${entry.workflow.workflowId}`}
                layout={suppressMotion ? false : "position"}
                className="board-trace__artifact mt-1 space-y-1"
                initial={suppressMotion ? false : TRACE_ROW_ENTER}
                animate={TRACE_ROW_VISIBLE}
                exit={suppressMotion ? { opacity: 0 } : TRACE_ROW_EXIT}
                transition={suppressMotion ? TRACE_ROW_REDUCED_TRANSITION : TRACE_ROW_TRANSITION}
              >
                <ExpandableWorkflowCard workflow={entry.workflow} />
              </motion.div>
            ) : entry.kind === "html_widget" ? (
              <motion.div
                key={`hw:${entry.item.id}`}
                layout={suppressMotion ? false : "position"}
                className="board-trace__artifact mt-1 space-y-1"
                initial={suppressMotion ? false : TRACE_ROW_ENTER}
                animate={TRACE_ROW_VISIBLE}
                exit={suppressMotion ? { opacity: 0 } : TRACE_ROW_EXIT}
                transition={suppressMotion ? TRACE_ROW_REDUCED_TRANSITION : TRACE_ROW_TRANSITION}
              >
                <HtmlWidgetCard item={entry.item} />
              </motion.div>
            ) : (
              <motion.div
                key={`rows:${entry.items[0]?.id ?? "empty"}`}
                layout={suppressMotion ? false : "position"}
                className="board-trace__rows relative overflow-hidden mt-0.5"
                transition={suppressMotion ? TRACE_ROW_REDUCED_TRANSITION : TRACE_ROW_TRANSITION}
              >
                <AnimatePresence initial={false} mode="popLayout">
                  {buildRollingList(entry.items, max as number).map((item, idx, arr) => (
                    <motion.div
                      key={item.id}
                      layout={suppressMotion ? false : "position"}
                      initial={suppressMotion ? false : TRACE_ROW_ENTER}
                      animate={TRACE_ROW_VISIBLE}
                      exit={suppressMotion ? { opacity: 0 } : TRACE_ROW_EXIT}
                      transition={suppressMotion ? TRACE_ROW_REDUCED_TRANSITION : TRACE_ROW_TRANSITION}
                      className="board-trace__row min-w-0"
                    >
                      <ItemButton
                        item={item}
                        onOpen={setViewingTool}
                        last={idx === arr.length - 1}
                      />
                    </motion.div>
                  ))}
                </AnimatePresence>
              </motion.div>
            ),
          )}
        </AnimatePresence>
      </div>
    );
  }

  // Static (post-run) mode: no height tween on the collapse — the rows block
  // is unbounded, so the layout snaps at the presence boundary and only the
  // content rises/dissolves on GPU props.
  //
  // Workflow cards are the turn's primary artifact, NOT collapsible tool rows —
  // they stay visible after the run finishes (a finished `onlyWorkflows` turn
  // has no header toggle, so hiding them would leave no way back). The header
  // chevron collapses only the rows segments around them.
  return (
    <>
      {entries.map((entry, i) => {
        if (entry.kind === "workflow") {
          return (
            <div key={`wf:${entry.workflow.workflowId}`} className="board-trace__artifact mt-1 space-y-1">
              <ExpandableWorkflowCard workflow={entry.workflow} />
            </div>
          );
        }
        if (entry.kind === "html_widget") {
          return (
            <div key={`hw:${entry.item.id}`} className="board-trace__artifact mt-1 space-y-1">
              <HtmlWidgetCard item={entry.item} />
            </div>
          );
        }
        const rows = groupConsecutiveCalls(buildStaticTree(entry.items));
        return (
          <div key={`rows:${i}`} className="board-trace__rows mt-0.5">
            <AnimatePresence initial={false}>
              {!collapsed && (
                <motion.div
                  key="rows"
                  initial={suppressMotion ? false : RISE_IN}
                  animate={RISE_SETTLED}
                  exit={
                    suppressMotion
                      ? { opacity: 0, transition: { duration: 0 } }
                      : { ...DISSOLVE_OUT, transition: { duration: MOTION.row, ease: EASE_OUT } }
                  }
                  transition={
                    suppressMotion
                      ? { duration: 0 }
                      : { duration: MOTION.panel, ease: EASE_DECELERATE }
                  }
                >
                  {rows.map((row, idx) => (
                    <div
                      key={row.type === "group" ? `g:${row.key}` : row.item.id}
                      className="board-trace__row min-w-0"
                    >
                      {row.type === "group" ? (
                        <ToolGroupRow
                          items={row.items}
                          onOpen={setViewingTool}
                          last={idx === rows.length - 1}
                        />
                      ) : (
                        <ItemButton
                          item={row.item}
                          onOpen={setViewingTool}
                          last={idx === rows.length - 1}
                        />
                      )}
                    </div>
                  ))}
                </motion.div>
              )}
            </AnimatePresence>
          </div>
        );
      })}
    </>
  );
}
