import { useState } from "react";
import { cancelChildAgentApi, getChildAgentResultApi } from "@/api/agents";
import { pinToMemoryApi, sendToChildAgentApi } from "@/api/chat";
import { agentRunFromBackgroundAgent, isActiveAgentStatus } from "@/lib/agentRun";
import { createSession, switchSession } from "@/actions/sessions";
import { sendMessage } from "@/actions/messages";
import { getState, useStore, type BackgroundAgent } from "@/stores";
import { AgentRunRow } from "@/components/ui/AgentRunRow";

// One agent row in the hub: the unified AgentRunRow, plus the per-row
// cancel state and open-session wiring.
export function SidebarAgentRow({
  agent,
  resultPreview,
  active,
}: {
  agent: BackgroundAgent;
  resultPreview?: string;
  active?: boolean;
}) {
  const config = useStore((s) => s.config);
  const upsertBackgroundAgent = useStore((s) => s.upsertBackgroundAgent);
  const setDraft = useStore((s) => s.setDraft);
  const pushToast = useStore((s) => s.pushToast);
  // The server's `command` is a generic "Agent" placeholder until an async
  // labeler runs; the child session's own name (the task) is the better title.
  const childName = useStore((s) =>
    agent.childSessionId
      ? s.sessions.find((session) => session.session_id === agent.childSessionId)?.name ?? null
      : null,
  );
  const [cancelling, setCancelling] = useState(false);

  const stop = async () => {
    if (agent.status !== "running" || cancelling) return;
    setCancelling(true);
    try {
      await cancelChildAgentApi(config, agent.sessionId, agent.taskId);
      upsertBackgroundAgent({ ...agent, status: "cancel_requested", updatedAt: Date.now() });
    } catch {
      setCancelling(false);
    }
  };

  const open = agent.childSessionId
    ? () => void switchSession(agent.childSessionId as string)
    : undefined;

  // Return the promise (don't `void` it) so the composer can await delivery
  // and restore the draft if the agent finished between render and send.
  const send =
    agent.status === "running"
      ? (message: string) => sendToChildAgentApi(config, agent.sessionId, agent.taskId, message)
      : undefined;

  // Finished agents get handoff actions: drop the full result into the
  // parent composer (reply), or seed a fresh session with it (route). The
  // full result is fetched on demand — the row only caches a one-line preview.
  const fetchResult = async (): Promise<string> => {
    const r = await getChildAgentResultApi(config, agent.sessionId, agent.taskId);
    return (r.result ?? "").trim();
  };
  const handoff = !isActiveAgentStatus(agent.status)
    ? {
        onReply: async () => {
          const text = await fetchResult();
          if (!text) return;
          const prev = getState().draft;
          setDraft(prev.trim() ? `${prev}\n\n${text}` : text);
        },
        onPin: async () => {
          const text = await fetchResult();
          if (!text) return;
          try {
            const outcome = await pinToMemoryApi(config, text);
            pushToast({
              id: crypto.randomUUID(),
              title: outcome.written ? "Pinned to memory" : "Already in memory",
              status: "completed",
              target: { kind: "session", sessionId: agent.sessionId },
            });
          } catch (error) {
            pushToast({
              id: crypto.randomUUID(),
              title: "Couldn't pin to memory",
              detail: error instanceof Error ? error.message : String(error),
              status: "failed",
              target: { kind: "session", sessionId: agent.sessionId },
            });
          }
        },
        onRoute: async () => {
          const text = await fetchResult();
          if (!text) return;
          await createSession();
          await sendMessage(text);
        },
      }
    : undefined;

  const run = agentRunFromBackgroundAgent(agent, resultPreview);
  const named = childName?.trim() ? { ...run, name: childName.trim() } : run;

  return (
    <AgentRunRow
      run={named}
      onOpen={open}
      onStop={agent.status === "running" ? stop : undefined}
      stopping={cancelling}
      active={active}
      onSend={send}
      handoff={handoff}
    />
  );
}
