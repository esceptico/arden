import { useEffect } from "react";
import { refreshChildAgents } from "@/actions/childAgents";

export function useChildAgentsPoll(sessionIds: readonly string[] | string | null): void {
  // Live BackgroundTaskEvents already keep the roster current for the session
  // being viewed; this poll is the fallback for missed terminal updates. Area
  // Hub supplies its AreaDetail session set, so it never polls the unrelated
  // current chat. Reconnect resync lives in reloadAllCollections.
  const sessionKey = typeof sessionIds === "string"
    ? sessionIds
    : sessionIds?.join("\u0000") ?? "";

  useEffect(() => {
    const ids = sessionKey ? sessionKey.split("\u0000") : [];
    if (ids.length === 0) return;
    const tick = () => {
      if (document.visibilityState === "visible") {
        for (const sessionId of ids) void refreshChildAgents(sessionId);
      }
    };
    for (const sessionId of ids) void refreshChildAgents(sessionId);
    const id = window.setInterval(tick, 5000);
    document.addEventListener("visibilitychange", tick);
    return () => {
      window.clearInterval(id);
      document.removeEventListener("visibilitychange", tick);
    };
  }, [sessionKey]);
}
