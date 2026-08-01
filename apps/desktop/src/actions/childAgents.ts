import { cancelChildAgentApi, listChildAgentsApi } from "@/api/agents";
import { childAgentTaskToBackgroundSnapshot } from "@/lib/agentRun";
import { getState, type BackgroundAgent } from "@/stores";

/** Fetch the durable child-agent roster for a session and write it into the
 *  store. Used by the sidebar poll AND the reconnect resync (reloadAllCollections)
 *  so an agent that started/finished while disconnected is reflected immediately
 *  on reconnect instead of waiting for the next poll tick. */
export async function refreshChildAgents(sessionId: string): Promise<void> {
  const s = getState();
  s.backgroundAgentsRefreshStarted();
  try {
    const tasks = await listChildAgentsApi(s.config, sessionId);
    s.setBackgroundAgentsForSession(
      sessionId,
      tasks.map(childAgentTaskToBackgroundSnapshot),
    );
  } catch (error) {
    s.backgroundAgentsRefreshFailed(
      error instanceof Error ? error.message : String(error),
    );
  }
}

/** Cancel a durable child-agent run via its roster row and reflect the
 *  cancel_requested state immediately. Used by the chat trace's agent row;
 *  the sidebar row keeps its own inline variant for per-row spinner state. */
export async function cancelChildAgent(agent: BackgroundAgent): Promise<void> {
  const s = getState();
  try {
    await cancelChildAgentApi(s.config, agent.sessionId, agent.taskId);
    s.upsertBackgroundAgent({ ...agent, status: "cancel_requested", updatedAt: Date.now() });
  } catch {
    /* row keeps its status, so the stop affordance stays available */
  }
}
