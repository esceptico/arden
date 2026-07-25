import { useCallback, useEffect, useMemo, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { createCustomModelApi, connectModelProviderApi, deleteCustomModelApi, disconnectModelProviderApi, getOpenAICodexOAuthStatusApi, listModelProvidersApi, startOpenAICodexOAuthApi, type ModelProvider, type OpenAICodexOAuthStatus } from "@/api/settings";
import { fetchServerConfig } from "@/actions/server";
import { useStore } from "@/stores";
import { ProviderRow } from "@/features/settings/components/ProviderRow";
import { CustomModelsPanel } from "@/features/settings/components/CustomModelsPanel";
import { SettingsTabSkeleton } from "@/features/settings/components/SettingsTabSkeleton";
import {
  SettingsDataSection,
  SettingsPageAction,
  SettingsSummary,
} from "@/features/settings/components/SettingsPage";
import { providerReadinessSummary } from "@/features/settings/lib/providerConnection";
import {
  canSaveCustomModelDraft,
  defaultCustomModelDraft,
  type CustomModelDraft,
} from "@/features/settings/lib/customModelDraft";
import {
  settingsErrorMessage,
  settingsErrorTitle,
  shouldShowLoadedSettingsContent,
} from "@/features/settings/lib/settingsLoadState";
import { SettingsConnectionHint, SettingsInlineError } from "@/features/settings/components/SettingsNotice";
import { SettingsRefreshAction } from "@/features/settings/components/SettingsRefreshAction";
import { DISSOLVE_OUT, EASE_OUT, MOTION, RISE_IN, RISE_SETTLED } from "@/lib/tokens/motion";

const PRIMARY_PROVIDERS = ["openai-codex", "openai", "anthropic", "google", "openrouter"];

export function ProvidersTab() {
  const config = useStore((s) => s.config);
  const serverConfig = useStore((s) => s.serverConfig);
  const [providers, setProviders] = useState<ModelProvider[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [loadedOnce, setLoadedOnce] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [apiKey, setApiKey] = useState("");
  const [pendingId, setPendingId] = useState<string | null>(null);
  const [codexStatus, setCodexStatus] = useState<OpenAICodexOAuthStatus | null>(null);
  const [customOpen, setCustomOpen] = useState(false);
  const [customDraft, setCustomDraft] = useState<CustomModelDraft>(() => defaultCustomModelDraft());

  const sortedProviders = useMemo(() => {
    const rank = new Map(PRIMARY_PROVIDERS.map((id, index) => [id, index]));
    return providers
      .slice()
      .sort((a, b) => (rank.get(a.id) ?? 99) - (rank.get(b.id) ?? 99));
  }, [providers]);
  const connectedProviders = useMemo(
    () => sortedProviders.filter((provider) => provider.connected),
    [sortedProviders],
  );
  const setupProviders = useMemo(
    () => sortedProviders.filter((provider) => !provider.connected),
    [sortedProviders],
  );
  const readiness = useMemo(
    () => providerReadinessSummary(sortedProviders, serverConfig?.chat_model ?? null),
    [serverConfig?.chat_model, sortedProviders],
  );

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const next = await listModelProvidersApi(config);
      setProviders(next);
      setLoadedOnce(true);
      const codex = next.find((provider) => provider.id === "openai-codex");
      if (codex?.connected) {
        setCodexStatus({ connected: true, status: "connected" });
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [config]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    if (codexStatus?.status !== "pending") return;
    const interval = window.setInterval(async () => {
      try {
        const next = await getOpenAICodexOAuthStatusApi(config);
        setCodexStatus(next);
        if (next.connected) {
          await refresh();
          await fetchServerConfig();
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      }
    }, 1500);
    return () => window.clearInterval(interval);
  }, [codexStatus?.status, config, refresh]);

  async function connect(provider: ModelProvider) {
    if (!apiKey.trim()) return;
    setPendingId(provider.id);
    setError(null);
    try {
      await connectModelProviderApi(config, provider.id, apiKey.trim());
      setEditingId(null);
      setApiKey("");
      await refresh();
      await fetchServerConfig();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setPendingId(null);
    }
  }

  async function disconnect(provider: ModelProvider) {
    setPendingId(provider.id);
    setError(null);
    try {
      await disconnectModelProviderApi(config, provider.id);
      await refresh();
      await fetchServerConfig();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setPendingId(null);
    }
  }

  async function startCodexSignIn() {
    setPendingId("openai-codex");
    setError(null);
    try {
      const status = await startOpenAICodexOAuthApi(config);
      setCodexStatus({ connected: false, status: status.status, url: status.url, opened: status.opened });
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setPendingId(null);
    }
  }

  async function createCustomModel() {
    if (!canSaveCustomModelDraft(customDraft)) return;
    setPendingId("custom:create");
    setError(null);
    try {
      await createCustomModelApi(config, {
        model_id: customDraft.model_id.trim(),
        base_url: customDraft.base_url.trim(),
        context_window: customDraft.context_window,
        max_output_tokens: customDraft.max_output_tokens,
        api_key: customDraft.api_key.trim() || null,
      });
      setCustomDraft(defaultCustomModelDraft());
      await refresh();
      await fetchServerConfig();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setPendingId(null);
    }
  }

  async function deleteCustomModel(modelId: string) {
    setPendingId(`custom:delete:${modelId}`);
    setError(null);
    try {
      await deleteCustomModelApi(config, modelId);
      await refresh();
      await fetchServerConfig();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setPendingId(null);
    }
  }

  function updateCustomDraft(patch: Partial<CustomModelDraft>) {
    setCustomDraft((prev) => ({ ...prev, ...patch }));
  }

  const hasLoadedData = loadedOnce || providers.length > 0;
  const showContent = shouldShowLoadedSettingsContent({ loading, error, hasData: hasLoadedData });

  function renderProvider(provider: ModelProvider) {
    return (
      <ProviderRow
        key={provider.id}
        provider={provider}
        editing={editingId === provider.id}
        apiKey={editingId === provider.id ? apiKey : ""}
        pending={pendingId === provider.id}
        codexStatus={provider.id === "openai-codex" ? codexStatus : null}
        customOpen={provider.id === "custom" ? customOpen : false}
        onToggleCustom={() => {
          setCustomOpen((value) => !value);
          setEditingId(null);
          setApiKey("");
        }}
        onEdit={() => {
          setEditingId(provider.id);
          setApiKey("");
          setCustomOpen(false);
        }}
        onCancel={() => {
          setEditingId(null);
          setApiKey("");
        }}
        onKeyChange={setApiKey}
        onConnect={() => void connect(provider)}
        onDisconnect={() => void disconnect(provider)}
        onCodexSignIn={() => void startCodexSignIn()}
      >
        {provider.id === "custom" && (
          <AnimatePresence initial={false}>
            {customOpen && (
              <motion.div
                key="custom-models"
                initial={{ ...RISE_IN, y: -4 }}
                animate={RISE_SETTLED}
                exit={{ ...DISSOLVE_OUT, transition: { duration: MOTION.fast, ease: EASE_OUT } }}
                transition={{ duration: MOTION.row, ease: EASE_OUT }}
              >
                <CustomModelsPanel
                  provider={provider}
                  draft={customDraft}
                  pendingId={pendingId}
                  onDraftChange={updateCustomDraft}
                  onCreate={() => void createCustomModel()}
                  onDelete={(modelId) => void deleteCustomModel(modelId)}
                />
              </motion.div>
            )}
          </AnimatePresence>
        )}
      </ProviderRow>
    );
  }

  return (
    <>
      <SettingsPageAction>
        <SettingsRefreshAction label="Providers" loading={loading} onRefresh={refresh} />
      </SettingsPageAction>

      {error && (
        <SettingsInlineError
          title={settingsErrorTitle("providers", hasLoadedData)}
          message={settingsErrorMessage(error)}
        />
      )}

      <div className="settings-provider-flow">
        {loading && providers.length === 0 ? (
          <SettingsTabSkeleton variant="cards" label="Loading providers…" />
        ) : !showContent ? (
          <SettingsConnectionHint />
        ) : (
          <>
            <SettingsSummary
              label={readiness.ready ? "Models ready" : readiness.label}
              detail={readiness.detail}
              stats={[
                {
                  value: readiness.connectedProviderCount,
                  label: "connected",
                  tone: readiness.connectedProviderCount > 0 ? "ok" : "warn",
                },
                { value: readiness.availableModelCount, label: "models" },
              ]}
            />
            <SettingsDataSection
              title="Ready providers"
              detail={`${connectedProviders.length} connected`}
              empty="No model providers are connected yet."
            >
              {connectedProviders.map(renderProvider)}
            </SettingsDataSection>
            <SettingsDataSection
              title="Set up more"
              detail={`${setupProviders.length} available`}
              empty="All configured providers are ready."
            >
              {setupProviders.map(renderProvider)}
            </SettingsDataSection>
          </>
        )}
      </div>
    </>
  );
}
