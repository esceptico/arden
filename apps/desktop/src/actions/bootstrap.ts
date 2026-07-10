import { checkHealth, loadInitialConfig } from "@/api/core";
import { listPrimarySessionsApi, listAreasApi } from "@/api/sessions";
import { getState } from "@/stores";
import { isAgentSessionId, parentSessionIdOf } from "@/lib/agentRun";
import { fetchAutomations } from "@/actions/automations";
import { refreshChildAgents } from "@/actions/childAgents";
import { fetchGoal } from "@/actions/goals";
import { refreshLoops } from "@/actions/loops";
import { refreshSessions } from "@/actions/sessions";
import { fetchSkills } from "@/actions/skills";
import { fetchServerConfig } from "@/actions/server";

const RESYNC_DEBOUNCE_MS = 800;
const SERVER_CONNECTION_POLL_MS = 3_000;
// Don't resync more than once per window: under reconnect churn (a busy server
// delaying keepalives, rapid session switches) onConnect can fire repeatedly,
// and we must not stampede an already-loaded server with 5-fetch bursts.
const RESYNC_MIN_INTERVAL_MS = 8_000;
let resyncTimer: ReturnType<typeof setTimeout> | null = null;
let lastResyncAt = 0;
let connectionPollTimer: ReturnType<typeof setInterval> | null = null;

/** Re-fetch the collections that have no live SSE delta feed (sessions list,
 *  automations, loops, goal, server config) after a chat-stream (re)connect or
 *  server restart. Live deltas remain the fast path; this is the correctness
 *  backstop so nothing stays stale until a manual reload. Debounced + rate-
 *  limited so flappy reconnects collapse into one resync. */
export function reloadAllCollections(sessionId: string | null): void {
  if (Date.now() - lastResyncAt < RESYNC_MIN_INTERVAL_MS) return;
  if (resyncTimer) clearTimeout(resyncTimer);
  resyncTimer = setTimeout(() => {
    resyncTimer = null;
    lastResyncAt = Date.now();
    // Session-agnostic collections always reflect current truth.
    void refreshSessions();
    void fetchAutomations();
    void fetchServerConfig();
    // Per-session refreshes only if this is STILL the active session — the
    // debounce can outlive a session switch / unmount, and must not write a
    // stale session's loops/goal into the store.
    if (sessionId && getState().currentSessionId === sessionId) {
      void refreshLoops(sessionId);
      void fetchGoal(sessionId).catch(() => {});
      // The background-agent roster has live deltas (BackgroundTaskEvent) but no
      // replay on reconnect, so resync it here for the session whose roster the
      // sidebar shows (the parent when a child agent is open).
      const rosterSessionId = isAgentSessionId(sessionId) ? parentSessionIdOf(sessionId) : sessionId;
      if (rosterSessionId) void refreshChildAgents(rosterSessionId);
    }
  }, RESYNC_DEBOUNCE_MS);
}

export async function refresh(): Promise<void> {
  const s = getState();
  try {
    const health = await checkHealth(s.config);
    if (!health.ok) {
      throw new Error(health.version ? "Invalid API key" : "Could not reach ntrp server");
    }
    s.setConnected(true);
    s.setError(null);

    const [areas, sessions] = await Promise.all([
      listAreasApi(s.config),
      listPrimarySessionsApi(s.config),
    ]);
    s.setAreaRecords(areas);
    s.setSessions(sessions);
    // Deliberately does NOT auto-select the most recent session (that was
    // GET /session's job pre-Areas) — the app opens on Home per the Areas
    // spec ("Placement": Home is the no-session state of the main pane).
    // currentSessionId stays whatever it already was (null on a fresh boot),
    // and App.tsx's showHome derivation renders Home from that. The sidebar
    // still gets its sessions list from the fetch above.
  } catch (error) {
    s.setConnected(false);
    s.setError(error instanceof Error ? error.message : String(error));
  }
}

export async function pollServerConnectionOnce(refreshFn: () => Promise<void> = refresh): Promise<void> {
  if (getState().connected) return;
  await refreshFn();
}

export function startServerConnectionPolling(intervalMs = SERVER_CONNECTION_POLL_MS): () => void {
  if (connectionPollTimer) clearInterval(connectionPollTimer);
  connectionPollTimer = setInterval(() => {
    void pollServerConnectionOnce();
  }, intervalMs);
  return () => {
    if (connectionPollTimer) {
      clearInterval(connectionPollTimer);
      connectionPollTimer = null;
    }
  };
}

export async function bootstrap(): Promise<void> {
  const s = getState();
  try {
    const config = await loadInitialConfig();
    s.setConfig(config);
  } catch (error) {
    s.setError(error instanceof Error ? error.message : String(error));
  }
  await refresh();
  void fetchSkills();
  void fetchServerConfig();
  // Seed the collections that otherwise stay empty/stale until their first
  // poll or an event arrives (the automations card + loop countdowns).
  void fetchAutomations();
  const sessionId = getState().currentSessionId;
  if (sessionId) void refreshLoops(sessionId);
}
