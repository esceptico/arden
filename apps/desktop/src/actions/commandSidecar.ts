import { startCommandRunApi, type AppDestination } from "@/api/commands";
import { getState } from "@/stores";

function currentDestination(): AppDestination {
  const state = getState();
  if (state.memoryOpen) return { kind: "memory" };
  if (state.areas.openAreaKey) return { kind: "area", area_id: state.areas.openAreaKey };
  if (state.currentSessionId) return { kind: "session", session_id: state.currentSessionId };
  return { kind: "home" };
}

export async function runCommandSidecar(query: string): Promise<void> {
  const trimmed = query.trim();
  if (!trimmed) return;
  const clientId = `command:${crypto.randomUUID()}`;
  const state = getState();
  state.beginCommandSidecar(trimmed, clientId);
  try {
    const run = await startCommandRunApi(state.config, {
      query: trimmed,
      client_id: clientId,
      current_destination: currentDestination(),
    });
    getState().attachCommandRun(clientId, run.run_id, run.session_id);
  } catch (error) {
    getState().failCommandSidecar(clientId, error instanceof Error ? error.message : String(error));
  }
}
