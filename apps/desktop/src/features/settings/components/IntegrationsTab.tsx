import { useCallback, useEffect, useMemo, useState } from "react";
import clsx from "clsx";
import { RefreshCw } from "@/components/icons";
import {
  connectGoogleServiceApi,
  connectServiceApi,
  disconnectGoogleServiceApi,
  disconnectServiceApi,
  listGoogleAccountsApi,
  listServicesApi,
  removeGoogleAccountApi,
  type GoogleAccountSummary,
  type GoogleIntegrationId,
  type ServiceConnection,
} from "@/api/settings";
import { fetchServerConfig, updateServerConfig } from "@/actions/server";
import { useStore } from "@/stores";
import { ReadinessCard } from "@/features/settings/components/ReadinessCard";
import { GoogleCard } from "@/features/settings/components/GoogleCard";
import { ServiceCard } from "@/features/settings/components/ServiceCard";
import { SettingsTabSkeleton } from "@/features/settings/components/SettingsTabSkeleton";
import {
  settingsErrorMessage,
  settingsErrorTitle,
  shouldShowLoadedSettingsContent,
} from "@/features/settings/lib/settingsLoadState";
import { SettingsConnectionHint, SettingsInlineError } from "@/features/settings/components/SettingsNotice";
import { ICON } from "@/lib/icons";
import { Button } from "@/components/ui/Button";
import { SetupAssistant, type SetupAssistantKind } from "@/features/settings/components/setup/SetupAssistant";

export function IntegrationsTab() {
  const config = useStore((s) => s.config);
  const serverConfig = useStore((s) => s.serverConfig);
  const [services, setServices] = useState<ServiceConnection[]>([]);
  const [googleAccounts, setGoogleAccounts] = useState<GoogleAccountSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [loadedOnce, setLoadedOnce] = useState(false);
  const [pendingId, setPendingId] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [serviceKey, setServiceKey] = useState("");
  const [assistant, setAssistant] = useState<Extract<SetupAssistantKind, "google" | "slack"> | null>(null);

  const googleEnabled = useMemo(() => {
    const value = (id: GoogleIntegrationId) => {
      const enabled = serverConfig?.integrations[id]?.enabled;
      if (typeof enabled === "boolean") return enabled;
      return id === "google_drive" ? false : (serverConfig?.google_enabled ?? false);
    };
    return { gmail: value("gmail"), calendar: value("calendar"), google_drive: value("google_drive") };
  }, [serverConfig]);
  const slackServices = useMemo(
    () => services.filter((service) => service.id.startsWith("slack_")),
    [services],
  );
  const connectedSlackServices = useMemo(
    () => slackServices.filter((service) => service.connected),
    [slackServices],
  );
  const setupSlackServices = useMemo(
    () => slackServices.filter((service) => !service.connected),
    [slackServices],
  );
  const readyGoogleCount = (["gmail", "calendar", "google_drive"] as GoogleIntegrationId[]).filter(
    (id) => googleEnabled[id] && googleAccounts.some((account) => account.services.includes(id)),
  ).length;
  const readyToolsCount = readyGoogleCount + connectedSlackServices.length;

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [nextServices, nextGoogleAccounts] = await Promise.all([
        listServicesApi(config),
        listGoogleAccountsApi(config),
      ]);
      setServices(nextServices);
      setGoogleAccounts(nextGoogleAccounts);
      setLoadedOnce(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [config]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  async function toggleGoogle(integrationId: GoogleIntegrationId, enabled: boolean) {
    setPendingId(integrationId);
    setError(null);
    try {
      await updateServerConfig({ integrations: { [integrationId]: enabled } });
      await fetchServerConfig();
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setPendingId(null);
    }
  }

  async function connectGoogle(integrationId: GoogleIntegrationId, accountId?: string) {
    setPendingId(`${integrationId}:connect${accountId ? `:${accountId}` : ""}`);
    setError(null);
    try {
      await connectGoogleServiceApi(config, integrationId, accountId);
      await updateServerConfig({ integrations: { [integrationId]: true } });
      await fetchServerConfig();
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setPendingId(null);
    }
  }

  async function disconnectGoogle(integrationId: GoogleIntegrationId, account: GoogleAccountSummary) {
    setPendingId(`${integrationId}:${account.id}`);
    setError(null);
    try {
      await disconnectGoogleServiceApi(config, integrationId, account.id);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setPendingId(null);
    }
  }

  async function removeGoogleAccount(account: GoogleAccountSummary) {
    setPendingId(`account:${account.id}`);
    setError(null);
    try {
      await removeGoogleAccountApi(config, account.id);
      await fetchServerConfig();
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setPendingId(null);
    }
  }

  async function connectService(service: ServiceConnection) {
    if (!serviceKey.trim()) return;
    setPendingId(service.id);
    setError(null);
    try {
      await connectServiceApi(config, service.id, serviceKey.trim());
      setEditingId(null);
      setServiceKey("");
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setPendingId(null);
    }
  }

  async function disconnectService(service: ServiceConnection) {
    setPendingId(service.id);
    setError(null);
    try {
      await disconnectServiceApi(config, service.id);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setPendingId(null);
    }
  }

  const hasLoadedData = loadedOnce || services.length > 0 || googleAccounts.length > 0;
  const showContent = shouldShowLoadedSettingsContent({ loading, error, hasData: hasLoadedData });

  return (
    <div className="grid gap-5">
      <div className="flex items-start justify-between gap-3">
        <p className="m-0 text-sm text-muted leading-[1.45] max-w-[540px]">
          Connect the data and action providers ntrp can use as tools. Model providers stay in
          Providers; MCP servers stay in MCP.
        </p>
        <Button variant="secondary" onClick={() => void refresh()} disabled={loading}>
          <RefreshCw size={ICON.SM} strokeWidth={2} className={clsx(loading && "animate-spin")} />
          Refresh
        </Button>
      </div>

      {error && (
        <SettingsInlineError
          title={settingsErrorTitle("integrations", hasLoadedData)}
          message={settingsErrorMessage(error)}
        />
      )}

      {loading && !hasLoadedData ? (
        <SettingsTabSkeleton variant="cards" label="Loading integrations…" />
      ) : !showContent ? (
        <SettingsConnectionHint />
      ) : (
        <>
          {assistant && (
            <SetupAssistant
              kind={assistant}
              onClose={() => setAssistant(null)}
              onDone={async () => {
                setAssistant(null);
                await fetchServerConfig();
                await refresh();
              }}
            />
          )}

          <ReadinessCard
            tone={readyToolsCount > 0 ? "ok" : "warn"}
            label={readyToolsCount > 0 ? "Tools ready" : "Connect tools"}
            detail={`Google: ${readyGoogleCount || "none"} · Slack: ${connectedSlackServices.length || "none"}`}
            footnote="Tool integrations are optional, but connected tools become available to the agent."
          />

          <GoogleCard
            enabled={googleEnabled}
            accounts={googleAccounts}
            pendingId={pendingId}
            onToggle={toggleGoogle}
            onConnect={connectGoogle}
            onDisconnect={disconnectGoogle}
            onRemoveAccount={removeGoogleAccount}
            onAssistant={() => setAssistant("google")}
          />

          <ServiceCard
            connectedServices={connectedSlackServices}
            setupServices={setupSlackServices}
            editingId={editingId}
            serviceKey={serviceKey}
            pendingId={pendingId}
            onEdit={(service) => {
              setEditingId(service.id);
              setServiceKey("");
            }}
            onCancel={() => {
              setEditingId(null);
              setServiceKey("");
            }}
            onKeyChange={setServiceKey}
            onConnect={connectService}
            onDisconnect={disconnectService}
            onAssistant={() => setAssistant("slack")}
          />
        </>
      )}
    </div>
  );
}
