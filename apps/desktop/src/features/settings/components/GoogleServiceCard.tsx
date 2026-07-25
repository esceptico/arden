import { UserAdd01 } from "@/components/icons";
import type { GoogleAccountSummary, GoogleIntegrationId } from "@/api/settings";
import {
  IntegrationItem,
  type IntegrationDetailRow,
} from "@/features/settings/components/IntegrationItem";

const META: Record<GoogleIntegrationId, { label: string; subtitle: string; toolCount: number }> = {
  gmail: {
    label: "Gmail",
    subtitle: "Read email; sending requires approval.",
    toolCount: 4,
  },
  calendar: {
    label: "Google Calendar",
    subtitle: "Read events; changes require approval.",
    toolCount: 4,
  },
  google_drive: {
    label: "Google Drive",
    subtitle: "Docs, Sheets, and files; changes require approval.",
    toolCount: 8,
  },
};

export function GoogleServiceCard({
  integrationId,
  enabled,
  accounts,
  pendingId,
  onToggle,
  onConnect,
  onDisconnect,
}: {
  integrationId: GoogleIntegrationId;
  enabled: boolean;
  accounts: GoogleAccountSummary[];
  pendingId: string | null;
  onToggle: (integrationId: GoogleIntegrationId, enabled: boolean) => Promise<void>;
  onConnect: (integrationId: GoogleIntegrationId) => Promise<void>;
  onDisconnect: (integrationId: GoogleIntegrationId, account: GoogleAccountSummary) => Promise<void>;
}) {
  const meta = META[integrationId];
  const bound = accounts.filter((account) => account.services.includes(integrationId));
  const failing = bound.filter((account) => account.error);
  const connectPending = pendingId === `${integrationId}:connect`;

  const rows: IntegrationDetailRow[] = bound.map((account) => ({
    id: account.id,
    title: account.email ?? "Unknown Google account",
    meta: account.error ? "needs attention" : "ready",
    actionLabel: "Disconnect",
    onAction: () => void onDisconnect(integrationId, account),
    actionDisabled: pendingId === `${integrationId}:${account.id}`,
  }));

  return (
    <IntegrationItem
      title={meta.label}
      subtitle={meta.subtitle}
      stateTitle={
        bound.length === 0
          ? "Not connected"
          : bound.length === 1
            ? bound[0].email ?? "1 account ready"
            : `${bound.length} accounts ready`
      }
      stateDetail={
        failing.length > 0
          ? "needs attention"
          : bound.length > 0
            ? `${meta.toolCount} tools available`
            : "Account detected at sign-in"
      }
      enabled={enabled}
      onToggle={(next) => void onToggle(integrationId, next)}
      action={
        bound.length > 0
          ? {
              label: "Add account",
              Icon: UserAdd01,
              onClick: () => void onConnect(integrationId),
              disabled: connectPending,
            }
          : {
              label: connectPending ? "Connecting…" : "Connect",
              onClick: () => void onConnect(integrationId),
              disabled: connectPending,
            }
      }
      rows={rows}
      expandedByDefault={bound.length > 0}
    />
  );
}
