import { useCallback, useEffect, useMemo, useState } from "react";
import {
  connectGoogleServiceApi,
  connectServiceApi,
  disconnectGoogleServiceApi,
  disconnectServiceApi,
  listGoogleAccountsApi,
  listServicesApi,
  type GoogleAccountSummary,
  type GoogleIntegrationId,
  type ServiceConnection,
} from "@/api/settings";
import { fetchServerConfig, updateServerConfig } from "@/actions/server";
import { useStore } from "@/stores";
import { GoogleServiceCard } from "@/features/settings/components/GoogleServiceCard";
import { ServiceCard } from "@/features/settings/components/ServiceCard";
import { SettingsTabSkeleton } from "@/features/settings/components/SettingsTabSkeleton";
import {
  settingsErrorMessage,
  settingsErrorTitle,
  shouldShowLoadedSettingsContent,
} from "@/features/settings/lib/settingsLoadState";
import { SettingsConnectionHint, SettingsInlineError } from "@/features/settings/components/SettingsNotice";
import { SettingsPageAction, SettingsSummary, SettingsSurface } from "@/features/settings/components/SettingsPage";
import { SettingsRefreshAction } from "@/features/settings/components/SettingsRefreshAction";
import { SetupAssistant } from "@/features/settings/components/setup/SetupAssistant";
import { slackTokenPrefixValid } from "@/features/settings/lib/setupAssistant";

const GOOGLE_INTEGRATIONS: GoogleIntegrationId[] = ["gmail", "calendar", "google_drive"];

export function IntegrationsTab() {
  const config = useStore((s) => s.config);
  const serverConfig = useStore((s) => s.serverConfig);
  const [services, setServices] = useState<ServiceConnection[]>([]);
  const [googleAccounts, setGoogleAccounts] = useState<GoogleAccountSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [loadedOnce, setLoadedOnce] = useState(false);
  const [pendingId, setPendingId] = useState<string | null>(null);
  const [assistantOpen, setAssistantOpen] = useState(false);

  const googleEnabled = useMemo(() => {
    const value = (id: GoogleIntegrationId) => {
      const enabled = serverConfig?.integrations[id]?.enabled;
      return typeof enabled === "boolean" ? enabled : false;
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
  const readyGoogleCount = GOOGLE_INTEGRATIONS.filter(
    (id) => googleEnabled[id] && googleAccounts.some((account) => account.services.includes(id)),
  ).length;
  const readyToolsCount = readyGoogleCount + connectedSlackServices.length;
  const connectedIntegrationCount = useMemo(
    () => googleAccounts.filter((account) => account.services.length > 0).length + connectedSlackServices.length,
    [connectedSlackServices.length, googleAccounts],
  );

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
    setPendingId(`${integrationId}:toggle`);
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

  /** OAuth runs server-side and detects the account email automatically;
   *  re-connecting an existing address merges into its account record. */
  async function connectGoogle(integrationId: GoogleIntegrationId) {
    setPendingId(`${integrationId}:connect`);
    setError(null);
    try {
      await connectGoogleServiceApi(config, integrationId);
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

  async function toggleSlack(enabled: boolean) {
    if (enabled) {
      setAssistantOpen(true);
      return;
    }
    const disconnectable = connectedSlackServices.filter((service) => !service.from_env);
    setPendingId("slack:toggle");
    setError(null);
    try {
      await Promise.all(disconnectable.map((service) => disconnectServiceApi(config, service.id)));
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
    <div className="settings-provider-flow">
      <SetupAssistant
        open={assistantOpen}
        kind="slack"
        onClose={() => setAssistantOpen(false)}
        onDone={async () => {
          setAssistantOpen(false);
          await fetchServerConfig();
          await refresh();
        }}
        onPrimary={async (_kind, value) => {
          if (!value.trim()) throw new Error("A bot token is required.");
          if (!slackTokenPrefixValid("slack_bot_token", value)) {
            throw new Error("A Slack bot token must start with xoxb-.");
          }
          await connectServiceApi(config, "slack_bot_token", value.trim());
          await fetchServerConfig();
          await refresh();
          setAssistantOpen(false);
        }}
      />

      <SettingsPageAction>
        <SettingsRefreshAction label="Integrations" loading={loading} onRefresh={refresh} />
      </SettingsPageAction>

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
          <SettingsSummary
            label={readyToolsCount > 0 ? "Tools ready" : "Connect tools"}
            detail={`Google: ${readyGoogleCount || "none"} · Slack: ${connectedSlackServices.length || "none"}`}
            stats={[
              {
                value: connectedIntegrationCount,
                label: "connections",
                tone: connectedIntegrationCount > 0 ? "ok" : "warn",
              },
              { value: readyToolsCount, label: "services ready" },
            ]}
          />

          <SettingsSurface className="settings-integration-surface">
            {GOOGLE_INTEGRATIONS.map((integrationId) => (
              <GoogleServiceCard
                key={integrationId}
                integrationId={integrationId}
                enabled={googleEnabled[integrationId]}
                accounts={googleAccounts}
                pendingId={pendingId}
                onToggle={toggleGoogle}
                onConnect={connectGoogle}
                onDisconnect={disconnectGoogle}
              />
            ))}
            <ServiceCard
              services={slackServices}
              pendingId={pendingId}
              onToggle={toggleSlack}
              onAssistant={() => setAssistantOpen(true)}
              onManage={() => setAssistantOpen(true)}
            />
          </SettingsSurface>
        </>
      )}
    </div>
  );
}
