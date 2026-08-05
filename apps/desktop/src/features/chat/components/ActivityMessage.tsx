import { memo, useState } from "react";
import clsx from "clsx";
import { useStore } from "@/stores";
import { ActivityHeader, ActivityTail, ActivityTrace, liftWorkflows } from "@/features/chat/components/ActivityTrace";
import { useWorkflows } from "@/hooks/useWorkflows";
import { activityTraceStats } from "@/lib/agent";
import {
  SOURCE_FOCUS_CLASS,
  entryAnimation,
  useMessage,
  useSourceFocused,
} from "@/features/chat/lib/messageShared";
import {
  foregroundRunLabel,
  selectForegroundApprovalCount,
  selectForegroundConnectionLabel,
  selectForegroundConnectionPhase,
  workingLabelText,
} from "@/features/chat/lib/foregroundRunStatus";

export const ActivityMessage = memo(function ActivityMessage({
  id,
  hideHeader = false,
}: {
  id: string;
  hideHeader?: boolean;
}) {
  const message = useMessage(id);
  const sourceFocused = useSourceFocused(id);
  const [expanded, setExpanded] = useState(() => !message?.activity?.done);
  // Hooks must run unconditionally — keep them above the early return.
  const currentSessionId = useStore((s) => s.currentSessionId);
  const running = useStore((s) => s.running);
  const activeActivityId = useStore((s) => s.activeActivityId);
  const approvalPending = useStore(selectForegroundApprovalCount) > 0;
  const connectionLabel = useStore(selectForegroundConnectionLabel);
  const connectionPhase = useStore(selectForegroundConnectionPhase);
  const workflows = useWorkflows(currentSessionId);
  if (!message?.activity || message.activity.items.length === 0) return null;
  const { items, done } = message.activity;

  // While the run is producing tools, show the rolling tool tail. Agent
  // parent rows stay visible so parallel research agents do not disappear.
  // After it's done, switch to a static list with all items and let collapse
  // just animate the container height — switching modes mid-collapse caused
  // the items to swap out (43 → 3) before the height finished shrinking,
  // producing a visible flicker.
  const collapsed = hideHeader ? false : !expanded;
  const max = done ? undefined : 3;
  // Count over post-lift rows so the header matches what ActivityTail renders —
  // workflow and html-widget tool calls are lifted into cards, not counted
  // as calls.
  const { workflowRows, htmlWidgetItems, rowItems } = liftWorkflows(items, workflows, currentSessionId);
  const { totalCount, activeCount } = activityTraceStats(rowItems);
  // A turn whose only activity is lifted cards shows just the cards — no
  // "Running/Worked N calls" tool-call header, no row chrome.
  const onlyCards = rowItems.length === 0 && workflowRows.length + htmlWidgetItems.length > 0;
  const liveHeading = running && activeActivityId === id
    ? workingLabelText(foregroundRunLabel({
        activityMessage: message,
        approvalPending,
        connectionLabel,
        connectionPhase,
      }))
    : undefined;

  return (
    <article
      className={clsx(
        "board-activity-message grid grid-cols-[minmax(0,1fr)] transition-[background-color,box-shadow] duration-panel",
        entryAnimation(message, "animate-roll-in"),
        sourceFocused && SOURCE_FOCUS_CLASS,
      )}
      data-id={id}
      data-source-focus={sourceFocused ? "true" : undefined}
      data-source-index={message.sourceIndex}
    >
      <ActivityTrace>
        {!hideHeader && !onlyCards && (
          <ActivityHeader
            done={done}
            label={message.activity.label}
            count={totalCount}
            activeCount={activeCount}
            backgrounded={!!message.activity.backgrounded}
            liveHeading={liveHeading}
            motionDisabled={message.suppressEntryMotion}
            onToggle={() => setExpanded((v) => !v)}
            expanded={expanded}
          />
        )}
        <ActivityTail
          items={items}
          max={max}
          collapsed={collapsed}
          motionDisabled={message.suppressEntryMotion}
        />
      </ActivityTrace>
    </article>
  );
});
