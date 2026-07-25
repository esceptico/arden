import { useMemo } from "react";
import { Square } from "@/components/icons";
import { useShallow } from "zustand/react/shallow";
import { useStore, type ActivityItem } from "@/stores";
import { activityItemStatus, friendlyAgentLabel, isAgent } from "@/lib/agent";
import { IconButton } from "@/components/ui/IconButton";
import { CodeWell } from "@/components/ui/CodeWell";
import { SystemSheet } from "@/components/ui/SystemSheet";
import { ICON } from "@/lib/icons";
import { cancelSubagent } from "@/actions/messages";
import { formatMaybeJson } from "@/features/chat/lib/toolViewer";
import { AgentBody } from "@/features/chat/components/AgentBody";
import { ChildRuns } from "@/features/chat/components/ChildRuns";

function ToolLogBlock({
  label,
  body,
  placeholder,
}: {
  label: string;
  body: string;
  placeholder: string;
}) {
  const content = body.trim() ? body : placeholder;
  return <CodeWell aria-label={label}>{content}</CodeWell>;
}

export function ToolViewer() {
  const item = useStore((s) => s.viewingTool);
  const close = useStore((s) => s.setViewingTool);
  const stackedAboveReview = useStore((s) => !!s.reviewingApprovalToolId);

  // Re-read the live item from the store so a streaming result patches in
  // while the viewer is open. The selector returns a stable reference for
  // the matching activity item — Zustand's default reference equality is
  // fine here.
  const live = useStore((s) => {
    if (!item) return null;
    for (const msg of s.messages.values()) {
      if (!msg.activity) continue;
      const found = msg.activity.items.find((it) => it.id === item.id);
      if (found) return found;
    }
    return item;
  });

  // All activity items reachable from this tool through `parentToolId`. We
  // need the full descendant set so the agent inspector can render a tree
  // of nested tool calls; the regular tool inspector only shows direct
  // children. Wrapped in useShallow so reference equality stays stable
  // across unrelated store updates.
  const descendants = useStore(
    useShallow((s) => {
      if (!item) return [] as ActivityItem[];
      const childrenByParent = new Map<string, ActivityItem[]>();
      for (const msg of s.messages.values()) {
        if (!msg.activity) continue;
        for (const it of msg.activity.items) {
          if (!it.parentToolId) continue;
          const arr = childrenByParent.get(it.parentToolId) ?? [];
          arr.push(it);
          childrenByParent.set(it.parentToolId, arr);
        }
      }
      const out: ActivityItem[] = [];
      const seen = new Set<string>();
      const visit = (parentId: string) => {
        const kids = childrenByParent.get(parentId);
        if (!kids) return;
        for (const k of kids) {
          if (seen.has(k.id)) continue;
          seen.add(k.id);
          out.push(k);
          visit(k.id);
        }
      };
      visit(item.id);
      return out;
    }),
  );

  // Direct children only — what the regular tool inspector shows.
  const directChildren = useMemo(
    () => descendants.filter((it) => it.parentToolId === item?.id),
    [descendants, item?.id],
  );

  const input = useMemo(() => formatMaybeJson(live?.args), [live?.args]);
  const output = useMemo(() => formatMaybeJson(live?.result), [live?.result]);

  const open = !!(item && live);
  const canStopSubagent =
    !!live && isAgent(live) && live.taskStatus === "running" && !!live.runId && !live.cancelRequested;
  const toolState = viewerState(live);

  return (
    <SystemSheet
      open={open}
      onClose={() => close(null)}
      title="Tool call"
      ariaLabel="Tool call"
      status={toolState.label}
      statusTone={toolState.tone}
      closeLabel="Close tool viewer"
      elevated={stackedAboveReview}
      headerAction={canStopSubagent && live ? (
        <IconButton
          onClick={() => {
            if (live.runId) void cancelSubagent(live.runId, live.id);
          }}
          aria-label="Stop subagent"
          title="Stop subagent"
        >
          <Square size={ICON.SM} />
        </IconButton>
      ) : undefined}
      bodyClassName="grid grid-cols-[minmax(0,1fr)] gap-3"
    >
      {live && (
        <>
          <div className="min-w-0">
            <strong className="block truncate text-base font-semibold text-ink">
              {isAgent(live) ? live.displayName ?? friendlyAgentLabel(live.kind) : live.kind}
            </strong>
            <p className="mt-1 text-base text-muted">
              {isAgent(live)
                ? "Activity and result stay inspectable without competing with the answer."
                : "Raw request and result stay inspectable without competing with the answer."}
            </p>
          </div>
          {isAgent(live) ? (
            <AgentBody item={live} descendants={descendants} />
          ) : (
            <>
            <ToolLogBlock
              label="Tool input"
              body={input.body}
              placeholder="No input arguments."
            />
            <ToolLogBlock
              label="Tool output"
              body={output.body}
              placeholder={live && activityItemStatus(live) === "ongoing" ? "Waiting for result…" : "Empty result."}
            />
            {directChildren.length > 0 && <ChildRuns items={directChildren} />}
            </>
          )}
        </>
      )}
    </SystemSheet>
  );
}

function viewerState(item: ActivityItem | null): {
  label: "Running" | "Completed" | "Failed" | "Cancelled";
  tone: "neutral" | "success" | "warning" | "danger";
} {
  if (!item || activityItemStatus(item) === "ongoing") return { label: "Running", tone: "neutral" };
  if (item.taskStatus === "failed") return { label: "Failed", tone: "danger" };
  if (item.taskStatus === "cancelled") return { label: "Cancelled", tone: "warning" };
  return { label: "Completed", tone: "success" };
}
