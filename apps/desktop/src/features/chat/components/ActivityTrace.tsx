import { useMemo, type ReactNode } from "react";
import { AnimatePresence, motion } from "motion/react";
import { useStore, type ActivityItem } from "@/stores";
import {
  MOTION,
  EASE_DECELERATE,
  EASE_OUT,
  SPRING_TRACE_ROW,
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

export function ActivityTrace({ children }: { children: ReactNode }) {
  return (
    <div className="font-sans text-sm leading-[1.4] text-muted">{children}</div>
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
  const suppressMotion = motionDisabled ?? streamReplaying;

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
  // animate only per-row enter/exit + sibling reflow via FLIP transforms.
  //
  // `position: relative` is critical: `mode="popLayout"` sets exiting items
  // to `position: absolute`. Without a positioned ancestor they snap to the
  // scroll viewport at (0, 0) and pile up as ghosts at the top of the chat.
  // `overflow: hidden` clips the exit slide so it doesn't leak above the row.
  //
  // Entry keys: cards key by workflowId; rows segments by position. Segments
  // only shift when a new workflow card lands between them, so the remount
  // that index-keying implies happens exactly at that boundary and nowhere
  // else (appending rows to the last segment keeps its index).
  if (rolling) {
    return (
      <>
        {entries.map((entry, i) =>
          entry.kind === "workflow" ? (
            <motion.div
              key={`wf:${entry.workflow.workflowId}`}
              className="mt-1 space-y-1"
              initial={suppressMotion ? false : { opacity: 0, y: 8, filter: "blur(2px)" }}
              animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
              transition={suppressMotion ? { duration: 0 } : SPRING_TRACE_ROW}
            >
              <ExpandableWorkflowCard workflow={entry.workflow} />
            </motion.div>
          ) : entry.kind === "html_widget" ? (
            <motion.div
              key={`hw:${entry.item.id}`}
              className="mt-1 space-y-1"
              initial={suppressMotion ? false : { opacity: 0, y: 8, filter: "blur(2px)" }}
              animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
              transition={suppressMotion ? { duration: 0 } : SPRING_TRACE_ROW}
            >
              <HtmlWidgetCard item={entry.item} />
            </motion.div>
          ) : (
            <div key={`rows:${i}`} className="relative overflow-hidden pl-1 mt-0.5">
              <AnimatePresence mode="popLayout" initial={false}>
                {buildRollingList(entry.items, max as number).map((item, idx, arr) => (
                  <motion.div
                    key={item.id}
                    data-activity-motion-row="true"
                    data-motion-suppressed={suppressMotion ? "true" : "false"}
                    layout={suppressMotion ? false : "position"}
                    initial={suppressMotion ? false : { opacity: 0, y: 8 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={suppressMotion ? { opacity: 1, y: 0 } : { opacity: 0, y: -8 }}
                    transition={suppressMotion ? { duration: 0 } : SPRING_TRACE_ROW}
                    className="min-w-0"
                  >
                    <ItemButton
                      item={item}
                      onOpen={setViewingTool}
                      last={idx === arr.length - 1}
                    />
                  </motion.div>
                ))}
              </AnimatePresence>
            </div>
          ),
        )}
      </>
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
            <div key={`wf:${entry.workflow.workflowId}`} className="mt-1 space-y-1">
              <ExpandableWorkflowCard workflow={entry.workflow} />
            </div>
          );
        }
        if (entry.kind === "html_widget") {
          return (
            <div key={`hw:${entry.item.id}`} className="mt-1 space-y-1">
              <HtmlWidgetCard item={entry.item} />
            </div>
          );
        }
        const rows = groupConsecutiveCalls(buildStaticTree(entry.items));
        return (
          <div key={`rows:${i}`} className="pl-1 mt-0.5">
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
                    <div key={row.type === "group" ? `g:${row.key}` : row.item.id} className="min-w-0">
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
