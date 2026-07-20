import { apiWithConfig } from "@/api/core";
import { connectGoogleServiceApi, type GoogleIntegrationId } from "@/api/settings";
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
  const googleIntegration = (["gmail", "calendar", "google_drive"] as const).find(
    (id) => id === connection.integrationId,
  ) as GoogleIntegrationId | undefined;
  if (connection.action === "oauth" && googleIntegration) {
    await connectGoogleServiceApi(state.config, googleIntegration);
    await updateServerConfig({ integrations: { [googleIntegration]: true } });
  } else if (connection.action === "enable" && googleIntegration) {
    await updateServerConfig({ integrations: { [googleIntegration]: true } });
  }
  await submit(connection, true);
}

export async function verifyAndResume(connection: PendingConnection): Promise<void> {
  await submit(connection, true);
}

export async function declineConnection(connection: PendingConnection): Promise<void> {
  await submit(connection, false);
}
