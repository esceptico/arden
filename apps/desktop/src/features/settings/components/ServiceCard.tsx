import { Plus } from "@/components/icons";
import type { ServiceConnection } from "@/api/settings";
import {
  IntegrationItem,
  type IntegrationDetailRow,
} from "@/features/settings/components/IntegrationItem";

export function ServiceCard({
  services,
  pendingId,
  onToggle,
  onAssistant,
  onManage,
}: {
  services: ServiceConnection[];
  pendingId: string | null;
  onToggle: (enabled: boolean) => Promise<void>;
  onAssistant: () => void;
  onManage: (service: ServiceConnection) => void;
}) {
  const connected = services.filter((service) => service.connected);
  const rows: IntegrationDetailRow[] = connected.map((service) => ({
    id: service.id,
    title: service.name,
    meta: `Bot token · 3 tools`,
    actionLabel: "Manage",
    onAction: () => onManage(service),
    actionDisabled: pendingId === service.id,
  }));

  return (
    <IntegrationItem
      title="Slack"
      subtitle="Token-backed tools; OAuth MCP servers stay in MCP."
      stateTitle={
        connected.length
          ? `${connected.length} ${connected.length === 1 ? "service" : "services"} ready`
          : "No services"
      }
      stateDetail={`${connected.length ? 3 : 0} tools available`}
      enabled={connected.length > 0}
      onToggle={(next) => void onToggle(next)}
      action={{ label: "Run setup assistant", onClick: onAssistant }}
      rows={rows}
      footer={{
        text: "Writes still require tool approval",
        actionLabel: "Add workspace",
        ActionIcon: Plus,
        onAction: onAssistant,
      }}
    />
  );
}
