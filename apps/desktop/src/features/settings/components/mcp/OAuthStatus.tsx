import type { MCPServer } from "@/api/settings";
import { BlurSwap } from "@/components/ui/BlurSwap";
import { Button } from "@/components/ui/Button";
import { LabeledField } from "@/components/ui/LabeledField";

function DetailRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline gap-2 min-w-0">
      <span className="shrink-0 text-muted">{label}</span>
      <span className="truncate font-mono text-ink-soft" title={value}>
        {value}
      </span>
    </div>
  );
}

export function OAuthStatus({
  server,
  busy,
  onReauthenticate,
}: {
  server: MCPServer;
  busy: boolean;
  onReauthenticate: () => void;
}) {
  const expired = !server.connected && !!server.error;
  const status = server.connected ? "Connected" : expired ? "Tokens expired" : "Not connected";

  return (
    <LabeledField label="Auth">
      <div className="grid gap-2 rounded-[var(--r-house)] border border-line-soft bg-surface-soft px-3 py-2.5">
        <div className="flex items-center justify-between gap-3">
          <div className="flex min-w-0 items-center gap-2">
            <span className="text-sm font-medium text-ink">OAuth · {status}</span>
          </div>
          <Button
            size="sm"
            onClick={onReauthenticate}
            disabled={busy}
          >
            <BlurSwap swapKey={busy ? "busy" : server.connected ? "reauth" : "signin"} blur={2}>
              {busy ? "…" : server.connected ? "Re-authenticate" : "Sign in"}
            </BlurSwap>
          </Button>
        </div>
        {(server.client_name || server.client_id || server.scope) && (
          <div className="grid gap-0.5 text-xs">
            {server.client_name && <DetailRow label="Client" value={server.client_name} />}
            {server.client_id && <DetailRow label="Client ID" value={server.client_id} />}
            {server.scope && <DetailRow label="Scope" value={server.scope} />}
          </div>
        )}
      </div>
    </LabeledField>
  );
}
