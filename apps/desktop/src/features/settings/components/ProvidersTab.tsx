import { useCallback, useEffect, useMemo, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import clsx from "clsx";
import { RefreshCw } from "@/components/icons";
import { createCustomModelApi, connectModelProviderApi, deleteCustomModelApi, disconnectModelProviderApi, getOpenAICodexOAuthStatusApi, listModelProvidersApi, startOpenAICodexOAuthApi, type ModelProvider, type OpenAICodexOAuthStatus } from "@/api/settings";
import { fetchServerConfig } from "@/actions/server";
import { useStore } from "@/stores";
import { ReadinessCard } from "@/features/settings/components/ReadinessCard";
import { SettingsGroupSection } from "@/features/settings/components/SettingsGroupSection";
import { ProviderRow } from "@/features/settings/components/ProviderRow";
import { CustomModelsPanel } from "@/features/settings/components/CustomModelsPanel";
import { SettingsTabSkeleton } from "@/features/settings/components/SettingsTabSkeleton";
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
import { DISSOLVE_OUT, EASE_OUT, MOTION, RISE_IN, RISE_SETTLED } from "@/lib/tokens/motion";
import { ICON } from "@/lib/icons";
import { Button } from "@/components/ui/Button";

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
        onToggleCustom={() => setCustomOpen((value) => !value)}
        onEdit={() => {
          setEditingId(provider.id);
          setApiKey("");
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
    <div className="grid gap-4">
      <div className="flex items-start justify-between gap-3">
        <p className="m-0 text-sm text-muted leading-[1.45] max-w-[520px]">
          Connect model providers here. Server connection and tool integrations stay separate.
        </p>
        <Button variant="secondary" onClick={() => void refresh()} disabled={loading}>
          <RefreshCw size={ICON.SM} strokeWidth={2} className={clsx(loading && "animate-spin")} />
          Refresh
        </Button>
      </div>

      {error && (
        <SettingsInlineError
          title={settingsErrorTitle("providers", hasLoadedData)}
          message={settingsErrorMessage(error)}
        />
      )}

      <div className="grid gap-3">
        {loading && providers.length === 0 ? (
          <SettingsTabSkeleton variant="cards" label="Loading providers…" />
        ) : !showContent ? (
          <SettingsConnectionHint />
        ) : (
          <>
            <ReadinessCard
              tone={readiness.ready ? "ok" : "warn"}
              label={readiness.label}
              detail={readiness.detail}
              footnote={`${readiness.connectedProviderCount} connected · ${readiness.availableModelCount} available models`}
            />
            <SettingsGroupSection
              title="Ready providers"
              detail={`${connectedProviders.length} connected`}
              empty="No model providers are connected yet."
              headerClassName="px-0.5"
            >
              {connectedProviders.map(renderProvider)}
            </SettingsGroupSection>
            <SettingsGroupSection
              title="Set up more"
              detail={`${setupProviders.length} available`}
              empty="All configured providers are ready."
              headerClassName="px-0.5"
            >
              {setupProviders.map(renderProvider)}
            </SettingsGroupSection>
          </>
        )}
      </div>
    </div>
  );
}
