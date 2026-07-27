import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { connectServiceApi, createCustomModelApi, connectModelProviderApi, deleteCustomModelApi, disconnectModelProviderApi, disconnectServiceApi, getOpenAICodexOAuthStatusApi, listModelProvidersApi, listServicesApi, startOpenAICodexOAuthApi, type ModelProvider, type OpenAICodexOAuthStatus, type ServiceConnection } from "@/api/settings";
import { fetchServerConfig, updateServerConfig } from "@/actions/server";
import { useStore } from "@/stores";
import { ProviderRow } from "@/features/settings/components/ProviderRow";
import { CustomModelsPanel } from "@/features/settings/components/CustomModelsPanel";
import { SettingsTabSkeleton } from "@/features/settings/components/SettingsTabSkeleton";
import {
  SettingsDataSection,
  SettingsPageAction,
  SettingsSection,
  SettingsSettingRow,
  SettingsSummary,
  SettingsSurface,
} from "@/features/settings/components/SettingsPage";
import { Tab, Tabs } from "@/components/ui/Tabs";
import { BlurSwap } from "@/components/ui/BlurSwap";
import { Button } from "@/components/ui/Button";
import { IconButton } from "@/components/ui/IconButton";
import { Input } from "@/components/ui/Input";
import { PeekSurface } from "@/components/workspace/PeekSurface";
import { Loader2, X } from "@/components/icons";
import { ICON } from "@/lib/icons";
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
            <WebSearchSection />
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

const WEB_SEARCH_MODES = [
  { id: "auto", label: "Auto" },
  { id: "exa", label: "Exa" },
  { id: "ddgs", label: "DuckDuckGo" },
  { id: "none", label: "Off" },
] as const;

type WebSearchMode = (typeof WEB_SEARCH_MODES)[number]["id"];

/** The web-search provider lives with the other provider decisions: which
 *  engine backs the agent's search tools. Auto resolves per key availability
 *  server-side; the hint names the live resolution. Selecting Exa without a
 *  connected key opens the key peek INSTEAD of committing — the mode never
 *  points at an engine that cannot run. */
function WebSearchSection() {
  const config = useStore((s) => s.config);
  const serverConfig = useStore((s) => s.serverConfig);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [exa, setExa] = useState<ServiceConnection | null>(null);
  const [peekOpen, setPeekOpen] = useState(false);
  const [keyValue, setKeyValue] = useState("");
  const [keyPending, setKeyPending] = useState(false);
  const [keyError, setKeyError] = useState<string | null>(null);
  // Set when the peek was opened by selecting Exa keyless: a successful
  // connect commits that intent; cancel leaves the mode untouched.
  const commitExaAfterConnect = useRef(false);

  const loadExa = useCallback(async () => {
    try {
      const services = await listServicesApi(config);
      setExa(services.find((service) => service.id === "exa_api_key") ?? null);
    } catch {
      setExa(null);
    }
  }, [config]);

  useEffect(() => {
    void loadExa();
  }, [loadExa]);

  const applyMode = useCallback(async (value: WebSearchMode) => {
    setPending(true);
    setError(null);
    try {
      await updateServerConfig({ web_search: value });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setPending(false);
    }
  }, []);

  if (!serverConfig) return null;

  const mode = serverConfig.web_search;
  const resolved = serverConfig.web_search_provider;
  const exaConnected = !!exa?.connected;

  const select = (value: string) => {
    if (pending || value === mode) return;
    if (value === "exa" && !exaConnected) {
      commitExaAfterConnect.current = true;
      setKeyError(null);
      setPeekOpen(true);
      return;
    }
    void applyMode(value as WebSearchMode);
  };

  const openManage = () => {
    commitExaAfterConnect.current = false;
    setKeyError(null);
    setPeekOpen(true);
  };

  const closePeek = () => {
    setPeekOpen(false);
    setKeyValue("");
    commitExaAfterConnect.current = false;
  };

  const connectExa = async () => {
    if (keyPending || !keyValue.trim()) return;
    setKeyPending(true);
    setKeyError(null);
    try {
      await connectServiceApi(config, "exa_api_key", keyValue.trim());
      const commitMode = commitExaAfterConnect.current;
      setKeyValue("");
      setPeekOpen(false);
      commitExaAfterConnect.current = false;
      await loadExa();
      if (commitMode) await applyMode("exa");
      else await fetchServerConfig();
    } catch (cause) {
      setKeyError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setKeyPending(false);
    }
  };

  const disconnectExa = async () => {
    if (keyPending) return;
    setKeyPending(true);
    setKeyError(null);
    try {
      await disconnectServiceApi(config, "exa_api_key");
      setPeekOpen(false);
      await loadExa();
      await fetchServerConfig();
    } catch (cause) {
      setKeyError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setKeyPending(false);
    }
  };

  const manageAffordance = (
    <button
      type="button"
      onClick={openManage}
      className="text-muted underline decoration-line-strong underline-offset-2 transition-colors duration-check hover:text-ink"
    >
      Manage key
    </button>
  );

  return (
    <SettingsSection title="Web search">
      <SettingsSurface>
        <SettingsSettingRow
          title="Search provider"
          hint={
            /* Fixed two-line footprint: the Auto hint wraps to two lines and
               the others are one — without the reserve the whole card jumps
               on every switch. The swap itself rides BlurSwap. */
            <span className="block min-h-[2lh]">
              <BlurSwap swapKey={`${mode}:${resolved}:${exaConnected}`}>
                {mode === "auto" ? (
                  <>
                    Auto prefers Exa when its key is connected and falls back to DuckDuckGo.{" "}
                    <strong className="font-medium text-ink-soft">
                      Currently using {resolved === "exa" ? "Exa" : resolved === "ddgs" ? "DuckDuckGo" : "none"}.
                    </strong>
                    {exaConnected && <> {manageAffordance}</>}
                  </>
                ) : mode === "none" ? (
                  "Web search tools are disabled for the agent."
                ) : mode === "exa" ? (
                  <>
                    All agent web searches go through Exa
                    {exa?.key_hint ? ` (${exa.key_hint})` : ""}. {manageAffordance}
                  </>
                ) : (
                  "All agent web searches go through DuckDuckGo. No key needed."
                )}
              </BlurSwap>
            </span>
          }
          control={
            <div className="flex flex-col items-end gap-1">
              <Tabs variant="segmented" size="sm" value={mode} onChange={select}>
                {WEB_SEARCH_MODES.map((option) => (
                  <Tab key={option.id} value={option.id}>{option.label}</Tab>
                ))}
              </Tabs>
              {error && <span role="alert" className="text-xs text-bad">{error}</span>}
            </div>
          }
        />
      </SettingsSurface>

      <PeekSurface
        open={peekOpen}
        onClose={closePeek}
        className="settings-key-peek surface-peek"
        ariaLabel="Exa key"
        layer="exa-key-peek"
        focusOnOpen
        focusSelector='input[type="password"]'
        closeOnOutsidePointerDown
      >
        <header className="arden-peek-rule-below">
          <strong>Exa key</strong>
          <IconButton data-peek-close onClick={closePeek} aria-label="Close" title="Close">
            <X size={ICON.SM} />
          </IconButton>
        </header>
        <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto p-4">
          <p className="m-0 text-sm leading-relaxed text-muted">
            {exaConnected
              ? <>Connected{exa?.key_hint ? <> · <span className="font-mono text-xs">{exa.key_hint}</span></> : null}{exa?.from_env ? " (set via EXA_API_KEY)" : ""}. Paste a new key to rotate it.</>
              : "Exa powers the higher-quality engine. Paste an API key from exa.ai to connect it."}
          </p>
          <Input
            type="password"
            value={keyValue}
            onChange={(event) => setKeyValue(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") void connectExa();
            }}
            placeholder="Exa API key"
            aria-label="Exa API key"
            spellCheck={false}
            autoComplete="off"
          />
          {keyError && <p role="alert" className="m-0 text-xs text-bad">{keyError}</p>}
        </div>
        <footer className="arden-peek-rule-above flex shrink-0 items-center gap-2 p-3">
          {exaConnected && !exa?.from_env && (
            <Button variant="danger" disabled={keyPending} onClick={() => void disconnectExa()}>
              Disconnect
            </Button>
          )}
          <span className="ml-auto flex items-center gap-2">
            <Button variant="quiet" onClick={closePeek} disabled={keyPending}>Cancel</Button>
            <Button variant="primary" onClick={() => void connectExa()} disabled={!keyValue.trim() || keyPending}>
              {keyPending && <Loader2 size={ICON.SM} className="animate-spin" />}
              Connect
            </Button>
          </span>
        </footer>
      </PeekSurface>
    </SettingsSection>
  );
}
