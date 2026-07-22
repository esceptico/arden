import { FileSpreadsheet } from "lucide-react";
import { CalendarDays, Mail } from "@/components/icons";
import type { GoogleAccountSummary, GoogleIntegrationId } from "@/api/settings";
import type { GoogleConnectionSummary } from "@/features/settings/lib/integrationConnection";
import { Badge, type BadgeTone } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { SwitchControl } from "@/components/ui/SwitchControl";
import { ICON } from "@/lib/icons";

const META: Record<GoogleIntegrationId, { label: string; detail: string; Icon: typeof Mail }> = {
  gmail: { label: "Gmail", detail: "Read email; sending requires approval", Icon: Mail },
  calendar: { label: "Google Calendar", detail: "Read events; changes require approval", Icon: CalendarDays },
  google_drive: { label: "Google Drive", detail: "Read Docs and Sheets; changes require approval", Icon: FileSpreadsheet },
};

export function GoogleServiceCard({
  integrationId,
  enabled,
  summary,
  accounts,
  pendingId,
  onToggle,
  onConnect,
  onDisconnect,
}: {
  integrationId: GoogleIntegrationId;
  enabled: boolean;
  summary: GoogleConnectionSummary;
  accounts: GoogleAccountSummary[];
  pendingId: string | null;
  onToggle: (integrationId: GoogleIntegrationId, enabled: boolean) => Promise<void>;
  onConnect: (integrationId: GoogleIntegrationId, accountId?: string) => Promise<void>;
  onDisconnect: (integrationId: GoogleIntegrationId, account: GoogleAccountSummary) => Promise<void>;
}) {
  const meta = META[integrationId];
  const bound = accounts.filter((account) => account.services.includes(integrationId));
  const available = accounts.filter((account) => !account.services.includes(integrationId));
  const tone = ({ ready: "ok", paused: "warn", setup: "neutral" } as const)[summary.tone] as BadgeTone;
  return (
    <section className="rounded-[10px] border border-line-soft bg-surface-soft/25 overflow-hidden">
      <div className="flex items-start gap-3 px-3 py-2.5">
        <meta.Icon size={ICON.MD} className={enabled && bound.length ? "text-ok" : "text-muted"} />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <div className="text-sm font-medium text-ink">{meta.label}</div>
            <Badge tone={tone}>{summary.label}</Badge>
          </div>
          <div className="text-xs text-muted">{meta.detail}</div>
        </div>
        <Button size="sm" variant="secondary" disabled={pendingId === `${integrationId}:connect`} onClick={() => void onConnect(integrationId)}>
          {pendingId === `${integrationId}:connect` ? "Connecting…" : bound.length ? "Add account" : "Connect"}
        </Button>
        <SwitchControl checked={enabled} onChange={(next) => void onToggle(integrationId, next)} aria-label={`Enable ${meta.label}`} />
      </div>
      {(bound.length > 0 || available.length > 0) && (
        <div className="border-t border-line-soft divide-y divide-line-soft/40">
          {bound.map((account) => (
            <div key={account.id} className="flex items-center gap-2 px-3 py-2 text-xs">
              <span className="min-w-0 flex-1 truncate text-ink-soft">{account.email || "Unknown Google account"}</span>
              <Button
                size="sm"
                variant="quiet"
                aria-label={`Disconnect ${meta.label} for ${account.email || account.id}`}
                disabled={pendingId === `${integrationId}:${account.id}`}
                onClick={() => void onDisconnect(integrationId, account)}
              >
                Disconnect
              </Button>
            </div>
          ))}
          {available.map((account) => (
            <div key={account.id} className="flex items-center gap-2 px-3 py-2 text-xs">
              <span className="min-w-0 flex-1 truncate text-muted">
                Add for {account.email || "existing Google account"}
              </span>
              <Button
                size="sm"
                variant="quiet"
                disabled={pendingId === `${integrationId}:connect:${account.id}`}
                onClick={() => void onConnect(integrationId, account.id)}
              >
                {pendingId === `${integrationId}:connect:${account.id}` ? "Connecting…" : "Connect"}
              </Button>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
