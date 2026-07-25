import { useEffect, useRef, useState } from "react";
import { getChildAgentResultApi } from "@/api/agents";
import { isActiveAgentStatus, resultSnippet } from "@/lib/agentRun";
import { useStore, type BackgroundAgent } from "@/stores";

export function childAgentResultKey(agent: Pick<BackgroundAgent, "sessionId" | "taskId">): string {
  return `${agent.sessionId}:${agent.taskId}`;
}

// Lazily fetch a one-line result preview for each terminal agent, once.
// Running agents have no durable result yet, so they're skipped.
export function useChildAgentResults(
  scopeKey: string,
  agents: BackgroundAgent[],
): Record<string, string> {
  const config = useStore((s) => s.config);
  const [snippets, setSnippets] = useState<Record<string, string>>({});
  const done = useRef<Set<string>>(new Set());
  const inflight = useRef<Set<string>>(new Set());

  // The panel mounts once and is reused across navigation, so reset the
  // cache when the hub's session scope changes.
  useEffect(() => {
    done.current = new Set();
    inflight.current = new Set();
    setSnippets({});
  }, [scopeKey]);

  // Include resultRef so the effect re-fires when a durable result lands
  // after the agent went terminal (otherwise an empty first fetch never retries).
  const terminalKeys = agents
    .filter((agent) => !isActiveAgentStatus(agent.status))
    .map((agent) => `${childAgentResultKey(agent)}:${agent.resultRef ?? ""}`)
    .join(",");

  useEffect(() => {
    if (!scopeKey) return;
    for (const agent of agents) {
      if (isActiveAgentStatus(agent.status)) continue;
      const key = childAgentResultKey(agent);
      if (done.current.has(key) || inflight.current.has(key)) continue;
      inflight.current.add(key);
      void getChildAgentResultApi(config, agent.sessionId, agent.taskId)
        .then((result) => {
          const snippet = resultSnippet(result.result ?? undefined);
          // Keyed + idempotent, so it's safe to apply even if the roster
          // changed mid-flight. Only mark done once we actually have a
          // preview, so a result written just after the agent goes terminal
          // still resolves on a later poll instead of staying blank forever.
          if (snippet) {
            done.current.add(key);
            setSnippets((prev) => ({ ...prev, [key]: snippet }));
          }
        })
        .catch(() => {})
        .finally(() => {
          inflight.current.delete(key);
        });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scopeKey, config, terminalKeys]);

  return snippets;
}
