import { ApiError, apiWithConfig } from "@/api/core";
import { connectGoogleServiceApi, type GoogleIntegrationId } from "@/api/settings";
import { updateServerConfig } from "@/actions/server";
import { getState, type PendingConnection } from "@/stores";

function integrationEnabled(integrationId: GoogleIntegrationId): boolean {
  const enabled = getState().serverConfig?.integrations[integrationId]?.enabled;
  if (typeof enabled !== "boolean") {
    throw new Error(`Cannot connect ${integrationId}: server integration state is unavailable.`);
  }
  return enabled;
}

async function submit(connection: PendingConnection, approved: boolean, accountRef?: string): Promise<void> {
  const state = getState();
  await apiWithConfig(state.config, "/connections/result", {
    method: "POST",
    body: JSON.stringify({
      run_id: connection.runId,
      tool_id: connection.toolId,
      approved,
      result: approved ? "connected" : "not now",
      ...(accountRef ? { account_ref: accountRef } : {}),
    }),
  });
  getState().resolvePendingConnection(connection.toolId);
}

export async function connectAndResume(connection: PendingConnection): Promise<void> {
  const state = getState();
  const googleIntegration = (["gmail", "calendar", "google_drive"] as const).find(
    (id) => id === connection.integrationId,
  ) as GoogleIntegrationId | undefined;
  const changesGoogleConfig = googleIntegration
    && (connection.action === "oauth" || connection.action === "enable");
  const shouldEnable = changesGoogleConfig ? !integrationEnabled(googleIntegration) : false;
  let accountRef = googleIntegration ? connection.accountRef : undefined;
  if (connection.action === "oauth" && googleIntegration) {
    const connected = await connectGoogleServiceApi(
      state.config,
      googleIntegration,
      undefined,
      accountRef,
    );
    if (googleIntegration === "gmail") {
      if (!connected.account.email) throw new Error("Gmail connection did not return an account email.");
      accountRef = connected.account.email;
    }
  }
  if (googleIntegration && shouldEnable) {
    await updateServerConfig({ integrations: { [googleIntegration]: true } });
  }
  try {
    await submit(connection, true, accountRef);
  } catch (verificationError) {
    const verificationRejected = verificationError instanceof ApiError && verificationError.status === 424;
    if (googleIntegration && shouldEnable && verificationRejected) {
      try {
        await updateServerConfig({ integrations: { [googleIntegration]: false } });
      } catch (rollbackError) {
        throw new AggregateError(
          [verificationError, rollbackError],
          `Connection verification failed and ${googleIntegration} could not be disabled again.`,
        );
      }
    }
    throw verificationError;
  }
}

export async function verifyAndResume(connection: PendingConnection): Promise<void> {
  await submit(connection, true);
}

export async function declineConnection(connection: PendingConnection): Promise<void> {
  await submit(connection, false);
}
