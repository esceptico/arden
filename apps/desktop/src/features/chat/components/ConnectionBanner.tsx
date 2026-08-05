import { useEffect, useState } from "react";
import { Mail, Plug } from "@/components/icons";
import { Button } from "@/components/ui/Button";
import {
  connectAndResume,
  declineConnection,
  verifyAndResume,
} from "@/actions/connections";
import { useStore, type PendingConnection } from "@/stores";
import { ICON } from "@/lib/icons";

function actionLabel(connection: PendingConnection): string {
  if (connection.action === "enable") return "Enable";
  if (connection.action === "oauth" && ["gmail", "calendar", "google_drive"].includes(connection.integrationId)) {
    return "Add account";
  }
  return connection.source === "recovery" ? "Reconnect" : "Connect";
}

export function ConnectionBanner() {
  const connection = useStore((state) => state.pendingConnections[0]);
  const openSettings = useStore((state) => state.openSettings);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    setBusy(false);
    setError(null);
  }, [connection?.toolId]);
  if (!connection) return null;

  const needsSettings = connection.action === "credentials" || connection.action === "settings";
  const run = async (operation: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await operation();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  };
  const Icon = connection.integrationId === "gmail" ? Mail : Plug;

  return (
    <section className="board-connection-banner mx-auto mb-2 w-[min(760px,calc(100%-32px))] rounded-[var(--r-panel)] border border-line-soft bg-surface px-3.5 py-3 shadow-sm">
      <div className="flex items-start gap-3">
        <span className="mt-0.5 grid size-8 shrink-0 place-items-center rounded-[var(--r-icon)] bg-surface-soft text-ink-soft">
          <Icon size={ICON.MD} />
        </span>
        <div className="min-w-0 flex-1">
          <div className="text-sm font-semibold text-ink">
            {connection.source === "recovery" ? `${connection.label} needs attention` : `Connect ${connection.label}`}
          </div>
          <p className="mt-0.5 text-sm leading-5 text-muted">{connection.capability}</p>
          {connection.accountRef && <p className="mt-1 text-xs text-faint">Account: {connection.accountRef}</p>}
          {connection.detail && connection.detail !== connection.capability && (
            <p className="mt-1 text-xs text-faint">{connection.detail}</p>
          )}
          {connection.requiredScopes.length > 0 && (
            <p className="mt-1 text-xs text-faint">
              Permissions: {connection.requiredScopes.map((scope) => scope.split("/").at(-1)).join(", ")}
            </p>
          )}
          {error && <p role="alert" className="mt-1.5 text-xs text-bad">{error}</p>}
          <div className="mt-2.5 flex flex-wrap gap-2">
            {needsSettings ? (
              <>
                <Button size="sm" onClick={() => openSettings("integrations")}>Open settings</Button>
                <Button size="sm" disabled={busy} onClick={() => void run(() => verifyAndResume(connection))}>
                  Check connection
                </Button>
              </>
            ) : (
              <Button size="sm" variant="primary" disabled={busy} onClick={() => void run(() => connectAndResume(connection))}>
                {busy ? "Connecting…" : actionLabel(connection)}
              </Button>
            )}
            <Button size="sm" variant="quiet" disabled={busy} onClick={() => void run(() => declineConnection(connection))}>
              Not now
            </Button>
          </div>
        </div>
      </div>
    </section>
  );
}
