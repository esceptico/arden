import { useEffect, useMemo, useState } from "react";
import { motion } from "motion/react";
import { useStore, type ActivityItem } from "@/stores";
import { getChildAgentResultApi, type ChildAgentResult } from "@/api/agents";
import { activityItemStatus, extractTask } from "@/lib/agent";
import { humanizeAgentType } from "@/lib/agentRun";
import { Markdown } from "@/components/ui/Markdown";
import { Caption } from "@/components/ui/Caption";
import { EASE_DECELERATE, MOTION } from "@/lib/tokens/motion";
import { buildStats, formatAgentUsage } from "@/features/chat/lib/toolViewer";
import { CopyButton } from "@/features/chat/components/CopyButton";
import { ActivityTree } from "@/features/chat/components/ActivityTree";
import { findBackgroundAgentForActivityItem } from "@/stores/background-agent-domain";

export function AgentBody({
  item,
  descendants,
}: {
  item: ActivityItem;
  descendants: ActivityItem[];
}) {
  const config = useStore((s) => s.config);
  const sessionId = useStore((s) => s.currentSessionId);
  const task = useMemo(() => extractTask(item.args) ?? item.target, [item.args, item.target]);
  const stats = useMemo(() => buildStats(descendants), [descendants]);
  const running = activityItemStatus(item) === "ongoing";
  const roster = useStore((s) =>
    findBackgroundAgentForActivityItem(s.backgroundAgents.rows, item, s.currentSessionId),
  );
  const childRunId = roster?.taskId ?? item.childAgent?.childRunId;
  const [childResult, setChildResult] = useState<ChildAgentResult | null>(null);
  const [childResultError, setChildResultError] = useState<string | null>(null);
  const [childResultLoading, setChildResultLoading] = useState(false);
  const shouldFetchChildResult = item.childAgent?.wait === false && !!childRunId && !!sessionId;

  useEffect(() => {
    if (!shouldFetchChildResult || !childRunId || !sessionId) {
      setChildResult(null);
      setChildResultError(null);
      setChildResultLoading(false);
      return;
    }
    let cancelled = false;
    setChildResult(null);
    setChildResultLoading(true);
    setChildResultError(null);
    void getChildAgentResultApi(config, sessionId, childRunId)
      .then((result) => {
        if (!cancelled) setChildResult(result);
      })
      .catch((error) => {
        if (!cancelled) setChildResultError(error instanceof Error ? error.message : String(error));
      })
      .finally(() => {
        if (!cancelled) setChildResultLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [childRunId, config, sessionId, shouldFetchChildResult]);

  const matchingChildResult = childResult?.child_run_id === childRunId ? childResult : null;
  const durableResult = matchingChildResult?.result?.trim() ? matchingChildResult.result : "";
  const localResult = item.childAgent && item.childAgent.wait === false ? "" : (item.result ?? "");
  const result = durableResult || localResult;
  const childRunning =
    matchingChildResult?.status === "running" ||
    matchingChildResult?.status === "activity" ||
    matchingChildResult?.status === "cancel_requested";
  const resultState = childResultError
    ? "error"
    : childResultLoading && result.trim().length === 0
      ? "loading"
      : running || childRunning
        ? "working"
        : result.trim().length === 0
          ? "empty"
          : "result";

  return (
    <>
      <section className="grid gap-1.5">
        <div className="flex items-baseline justify-between gap-3">
          <Caption>Task</Caption>
          {item.usage && item.usage.total > 0 && (
            <span className="text-xs text-faint tabular-nums" title="Subagent's own LLM spend (already rolled into the parent's total cost)">
              {formatAgentUsage(item.usage.total, item.cost)}
            </span>
          )}
        </div>
        <p className="m-0 text-base leading-relaxed text-ink whitespace-pre-wrap">
          {task || "(no task provided)"}
        </p>
      </section>

      <section className="grid gap-1.5">
        <div className="flex items-center gap-2">
          <Caption>Result</Caption>
          {item.childAgent && (
            <span className="text-xs text-faint" title={item.childAgent.agentRef}>
              {humanizeAgentType(item.childAgent.agentType)} · {item.childAgent.wait ? "awaited" : "detached"}
            </span>
          )}
          {result.length > 0 && (
            <CopyButton getValue={() => result} />
          )}
        </div>
        {/* Keyed by state so the arriving content gets a one-shot blur-in
            instead of hard-cutting (e.g. "Working…" → result). */}
        <motion.div
          key={resultState}
          initial={{ opacity: 0, filter: "blur(2px)" }}
          animate={{ opacity: 1, filter: "blur(0px)" }}
          transition={{ duration: MOTION.trace, ease: EASE_DECELERATE }}
          className="min-w-0"
        >
          {resultState === "error" ? (
            <div role="alert" className="px-3 py-2.5 rounded-[10px] bg-bad-soft border border-bad/15 text-sm text-bad">
              {childResultError}
            </div>
          ) : resultState === "loading" ? (
            <div className="px-3 py-2.5 rounded-[10px] bg-surface-soft text-sm text-faint italic">
              Loading result…
            </div>
          ) : resultState === "working" ? (
            <div className="px-3 py-2.5 rounded-[10px] bg-surface-soft text-sm text-faint italic">
              Working…
            </div>
          ) : resultState === "empty" ? (
            <div className="px-3 py-2.5 rounded-[10px] bg-surface-soft text-sm text-faint italic">
              Empty result.
            </div>
          ) : (
            <div className="rounded-[10px] border border-line-soft bg-bg-main px-3 py-2.5 max-h-[40vh] overflow-y-auto scroll-thin min-w-0">
              <Markdown content={result} />
            </div>
          )}
        </motion.div>
      </section>

      {descendants.length > 0 && (
        <section className="grid gap-1.5 min-w-0">
          <div className="flex items-center gap-2">
            <Caption>Activity</Caption>
            <span className="text-xs text-faint tabular-nums">
              {stats.total} {stats.total === 1 ? "call" : "calls"}
              {stats.agents > 0 && ` · ${stats.agents} sub-agent${stats.agents === 1 ? "" : "s"}`}
            </span>
          </div>
          <ActivityTree
            descendants={descendants}
            rootId={item.id}
            rootDepth={item.depth ?? 0}
          />
        </section>
      )}
    </>
  );
}
