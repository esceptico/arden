import { useEffect } from "react";
import { getChildAgentResultApi } from "@/api/agents";
import { isActiveAgentStatus, resultSnippet } from "@/lib/agentRun";
import { getState, useStore, type BackgroundAgent } from "@/stores";

export function childAgentResultKey(agent: Pick<BackgroundAgent, "sessionId" | "taskId">): string {
  return `${agent.sessionId}:${agent.taskId}`;
}

// Fetches shared across every mount (sidebar hub + chat trace), so two
// surfaces watching the same agent issue one request between them.
const inflight = new Set<string>();

// Lazily fetch a one-line result preview for each terminal agent, once, into
// the store's childAgentResultSnippets cache (keyed session:task) — the chat
// trace rows read the same entries. Running agents have no durable result
// yet, so they're skipped; failed/interrupted agents are fetched too since
// their result_text carries the failure reason.
export function useChildAgentResults(
  scopeKey: string,
  agents: BackgroundAgent[],
): Record<string, string> {
  const config = useStore((s) => s.config);
  const snippets = useStore((s) => s.childAgentResultSnippets);

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
      if (getState().childAgentResultSnippets[key] !== undefined || inflight.has(key)) continue;
      inflight.add(key);
      void getChildAgentResultApi(config, agent.sessionId, agent.taskId)
        .then((result) => {
          const snippet = resultSnippet(result.result ?? undefined);
          // Keyed + idempotent, so it's safe to apply even if the roster
          // changed mid-flight. Only cache once we actually have a preview,
          // so a result written just after the agent goes terminal still
          // resolves on a later poll instead of staying blank forever.
          if (snippet) getState().setChildAgentResultSnippet(key, snippet);
        })
        .catch(() => {})
        .finally(() => {
          inflight.delete(key);
        });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scopeKey, config, terminalKeys]);

  return snippets;
}
