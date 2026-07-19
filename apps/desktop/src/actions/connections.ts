import { apiWithConfig } from "@/api/core";
import { addGmailAccountApi } from "@/api/settings";
import { updateServerConfig } from "@/actions/server";
import { getState, type PendingConnection } from "@/stores";

async function submit(connection: PendingConnection, approved: boolean): Promise<void> {
  const state = getState();
  await apiWithConfig(state.config, "/connections/result", {
    method: "POST",
    body: JSON.stringify({
      run_id: connection.runId,
      tool_id: connection.toolId,
      approved,
      result: approved ? "connected" : "not now",
    }),
  });
  getState().resolvePendingConnection(connection.toolId);
}

export async function connectAndResume(connection: PendingConnection): Promise<void> {
  const state = getState();
  if (connection.action === "oauth" && connection.connectionId === "google") {
    await addGmailAccountApi(
      state.config,
      connection.integrationId === "calendar" ? "calendar" : "email",
    );
    await updateServerConfig({ integrations: { google: true } });
  } else if (connection.action === "enable" && connection.connectionId === "google") {
    await updateServerConfig({ integrations: { google: true } });
  }
  await submit(connection, true);
}

export async function verifyAndResume(connection: PendingConnection): Promise<void> {
  await submit(connection, true);
}

export async function declineConnection(connection: PendingConnection): Promise<void> {
  await submit(connection, false);
}
