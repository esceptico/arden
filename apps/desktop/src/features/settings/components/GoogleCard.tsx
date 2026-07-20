import type { GoogleAccountSummary, GoogleIntegrationId } from "@/api/settings";
import { ConfirmDeleteButton } from "@/components/ui/ConfirmDeleteButton";
import { Button } from "@/components/ui/Button";
import { GoogleServiceCard } from "@/features/settings/components/GoogleServiceCard";
import { googleServiceConnectionSummary } from "@/features/settings/lib/integrationConnection";

const INTEGRATIONS: GoogleIntegrationId[] = ["gmail", "calendar", "google_drive"];

export function GoogleCard({
  enabled,
  accounts,
  pendingId,
  onToggle,
  onConnect,
  onDisconnect,
  onRemoveAccount,
  onAssistant,
}: {
  enabled: Record<GoogleIntegrationId, boolean>;
  accounts: GoogleAccountSummary[];
  pendingId: string | null;
  onToggle: (integrationId: GoogleIntegrationId, enabled: boolean) => Promise<void>;
  onConnect: (integrationId: GoogleIntegrationId, accountId?: string) => Promise<void>;
  onDisconnect: (integrationId: GoogleIntegrationId, account: GoogleAccountSummary) => Promise<void>;
  onRemoveAccount: (account: GoogleAccountSummary) => Promise<void>;
  onAssistant: () => void;
}) {
  return (
    <section className="grid gap-3 rounded-[12px] border border-line-soft bg-surface p-3.5">
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="text-base font-medium text-ink">Google services</div>
          <div className="text-xs text-muted">One Google account can authorize services independently inside ntrp.</div>
        </div>
        <Button variant="secondary" onClick={onAssistant}>Setup credentials</Button>
      </div>

      <div className="grid gap-2">
        {INTEGRATIONS.map((integrationId) => (
          <GoogleServiceCard
            key={integrationId}
            integrationId={integrationId}
            enabled={enabled[integrationId]}
            summary={googleServiceConnectionSummary(integrationId, enabled[integrationId], accounts)}
            accounts={accounts}
            pendingId={pendingId}
            onToggle={onToggle}
            onConnect={onConnect}
            onDisconnect={onDisconnect}
          />
        ))}
      </div>

      {accounts.length > 0 && (
        <div className="grid gap-1 pt-1">
          <div className="text-xs font-medium text-muted">Connected Google accounts</div>
          {accounts.map((account) => (
            <div key={account.id} className="flex items-center gap-2 text-xs">
              <span className="min-w-0 flex-1 truncate text-ink-soft">{account.email || account.id}</span>
              <ConfirmDeleteButton
                size="md"
                label={`Remove Google account ${account.email || account.id}`}
                busy={pendingId === `account:${account.id}`}
                onConfirm={() => void onRemoveAccount(account)}
              />
            </div>
          ))}
          <div className="text-xs text-faint">Removing an account revokes all Google services for it.</div>
        </div>
      )}
    </section>
  );
}
