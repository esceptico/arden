import { useEffect, useMemo, useState } from "react";
import { useStore } from "@/stores";
import { updateServerConfig, fetchServerConfig } from "@/actions/server";
import {
  getEmbeddingModelsApi,
  getIndexStatusApi,
  startIndexingApi,
  type EmbeddingModelsResponse,
  type IndexStatus,
  updateEmbeddingModelApi,
} from "@/api/settings";
import type { ModelGroup } from "@/api/types";
import { ModelMenuPicker, ModelReasoningPicker, shortModelLabel } from "@/components/ui/ModelPickers";
import { ChevronDown } from "@/components/icons";
import { BlurSwap } from "@/components/ui/BlurSwap";
import { DialogLayer } from "@/components/workspace/DialogLayer";
import { useTimeoutFlag } from "@/lib/hooks";
import { ICON } from "@/lib/icons";
import { SettingsConnectionHint, SettingsInlineError } from "@/features/settings/components/SettingsNotice";
import { SaveStatus } from "@/features/settings/components/SaveStatus";
import {
  SettingsSection,
  SettingsSettingRow,
  SettingsSurface,
} from "@/features/settings/components/SettingsPage";
import { SettingsTabSkeleton } from "@/features/settings/components/SettingsTabSkeleton";
import { Button } from "@/components/ui/Button";

const NO_EMBEDDINGS = "__no_embeddings__";
const INDEX_POLL_MS = 1_000;

/** One row per background role. Model and effort live together in the role's
 *  own setup, so effort stops being keyed by model alone — these roles
 *  routinely share a model, and sharing its single effort entry made the
 *  rows move at once. A role falls back to the per-model value until it names
 *  its own. */
type ModelKind = "research" | "workflow" | "auxiliary" | "memory";

const KIND_LABELS: Record<ModelKind, { title: string; description: string }> = {
  research: {
    title: "Research",
    description: "Used by research-style sub-agents and deeper investigations.",
  },
  workflow: {
    title: "Workflows",
    description: "Default for workflow agents; a script's explicit per-agent model still wins.",
  },
  auxiliary: {
    title: "Auxiliary",
    description: "Chat filing suggestions and generated automation descriptions.",
  },
  memory: {
    title: "Memory",
    description: "Knowledge reflection, activation, and retention.",
  },
};

const SETTINGS_MODEL_KINDS: ModelKind[] = ["research", "workflow", "auxiliary", "memory"];

export function ModelsTab() {
  const connected = useStore((s) => s.connected);
  const cfg = useStore((s) => s.serverConfig);
  const models = useStore((s) => s.serverModels);
  const [saving, setSaving] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saved, fireSaved] = useTimeoutFlag(1500);

  if (!cfg) {
    if (!connected) return <SettingsConnectionHint />;
    return <SettingsTabSkeleton label="Loading models…" />;
  }

  if (!models) {
    return (
      <div className="grid gap-3">
        <SettingsInlineError
          title="Couldn't load models"
          message="The server is reachable, but the model list did not load."
          action={
            <Button size="sm" onClick={() => void fetchServerConfig()}>
              Retry
            </Button>
          }
        />
        <SettingsConnectionHint
          title="Check provider setup"
          detail="Connect at least one model provider in Providers, then refresh this view."
        />
      </div>
    );
  }

  if (
    !Object.prototype.hasOwnProperty.call(cfg, "model_reasoning_efforts") ||
    !Object.prototype.hasOwnProperty.call(cfg, "model_roles") ||
    !Object.prototype.hasOwnProperty.call(models, "reasoning_efforts")
  ) {
    return (
      <SettingsInlineError
        title="Model settings contract changed"
        message="The desktop UI and server model metadata are out of sync. Restart the server, then reopen Settings."
      />
    );
  }

  const groups: ModelGroup[] = models.groups.length > 0
    ? models.groups
    : [{ provider: "all", label: "Models", models: models.models }];

  return (
    <>
      <SettingsSection
        title="Defaults"
        detail="saved automatically"
        action={(saving || saved) ? <SaveStatus busy={!!saving} saved={saved} /> : undefined}
      >
        <SettingsSurface>
        <Section
          title="Default chat (new chats)"
          description="Model new chats start on. Existing chats keep their own."
          current={cfg.chat_model}
          savingModel={saving === "chat_model:model"}
          savingReasoning={saving === "chat_model:reasoning"}
          groups={groups}
          reasoningEfforts={models.reasoning_efforts}
          currentReasoning={cfg.model_reasoning_efforts[cfg.chat_model] ?? null}
          onSelect={async (model) => {
            if (model === cfg.chat_model || saving) return;
            setSaving("chat_model:model");
            setError(null);
            try {
              await updateServerConfig({ chat_model: model });
              fireSaved();
            } catch (err) {
              setError(err instanceof Error ? err.message : String(err));
              await fetchServerConfig();
            } finally {
              setSaving(null);
            }
          }}
          onSetReasoning={async (effort) => {
            if (saving) return;
            setSaving("chat_model:reasoning");
            setError(null);
            try {
              await updateServerConfig({
                reasoning_model: cfg.chat_model,
                reasoning_effort: effort,
              });
              fireSaved();
            } catch (err) {
              setError(err instanceof Error ? err.message : String(err));
              await fetchServerConfig();
            } finally {
              setSaving(null);
            }
          }}
        />
        {SETTINGS_MODEL_KINDS.map((kind) => {
          const setup = cfg.model_roles?.[kind];
          const current = setup?.model;
          // Absent until the server restarts onto a build that ships this key.
          if (!current) return null;
          const meta = KIND_LABELS[kind];
          return (
            <Section
              key={kind}
              title={meta.title}
              description={meta.description}
              current={current}
              savingModel={saving === `${kind}:model`}
              savingReasoning={saving === `${kind}:reasoning`}
              groups={groups}
              reasoningEfforts={models.reasoning_efforts}
              currentReasoning={setup?.reasoning_effort ?? cfg.model_reasoning_efforts[current] ?? null}
              onSelect={async (model) => {
                if (model === current || saving) return;
                setSaving(`${kind}:model`);
                setError(null);
                try {
                  await updateServerConfig({ model_roles: { [kind]: { model } } });
                  fireSaved();
                } catch (err) {
                  setError(err instanceof Error ? err.message : String(err));
                  await fetchServerConfig();
                } finally {
                  setSaving(null);
                }
              }}
              onSetReasoning={async (effort) => {
                if (saving) return;
                setSaving(`${kind}:reasoning`);
                setError(null);
                try {
                  await updateServerConfig({ model_roles: { [kind]: { reasoning_effort: effort } } });
                  fireSaved();
                } catch (err) {
                  setError(err instanceof Error ? err.message : String(err));
                  await fetchServerConfig();
                } finally {
                  setSaving(null);
                }
              }}
            />
          );
        })}
        </SettingsSurface>
      </SettingsSection>
      <EmbeddingSection configuredModel={cfg.embedding_model} />
      {error && (
        <SettingsInlineError title="Couldn't update model" message={error} />
      )}
    </>
  );
}

function EmbeddingSection({ configuredModel }: { configuredModel: string | null }) {
  const apiConfig = useStore((s) => s.config);
  const [catalog, setCatalog] = useState<EmbeddingModelsResponse | null>(null);
  const [indexStatus, setIndexStatus] = useState<IndexStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadAttempt, setLoadAttempt] = useState(0);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [applying, setApplying] = useState(false);
  const [retrying, setRetrying] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState<{ model: string | null } | null>(null);

  useEffect(() => {
    let disposed = false;

    async function load() {
      setLoading(true);
      setLoadError(null);
      try {
        const [nextCatalog, nextStatus] = await Promise.all([
          getEmbeddingModelsApi(apiConfig),
          getIndexStatusApi(apiConfig),
        ]);
        if (disposed) return;
        setCatalog(nextCatalog);
        setIndexStatus(nextStatus);
      } catch (err) {
        if (!disposed) setLoadError(err instanceof Error ? err.message : String(err));
      } finally {
        if (!disposed) setLoading(false);
      }
    }

    void load();
    return () => {
      disposed = true;
    };
  }, [apiConfig, loadAttempt]);

  useEffect(() => {
    if (indexStatus?.state !== "reindexing") return;
    let disposed = false;
    let timer: number | undefined;

    async function poll() {
      try {
        const next = await getIndexStatusApi(apiConfig);
        if (disposed) return;
        setIndexStatus(next);
        setError(null);
        if (next.state === "reindexing") timer = window.setTimeout(() => void poll(), INDEX_POLL_MS);
      } catch (err) {
        if (disposed) return;
        setError(err instanceof Error ? err.message : String(err));
        timer = window.setTimeout(() => void poll(), INDEX_POLL_MS);
      }
    }

    void poll();
    return () => {
      disposed = true;
      window.clearTimeout(timer);
    };
  }, [apiConfig, indexStatus?.state]);

  const currentModel = catalog ? catalog.current : configuredModel;
  const options = useMemo(() => {
    const models = [...new Set([currentModel, ...(catalog?.models ?? [])].filter(Boolean))] as string[];
    return [
      { value: NO_EMBEDDINGS, label: "No embeddings" },
      ...models.map((model) => ({ value: model, label: model })),
    ];
  }, [catalog?.models, currentModel]);
  const reindexing = indexStatus?.state === "reindexing";
  const disabled = loading || !catalog || applying || retrying || reindexing;
  const currentValue = currentModel ?? NO_EMBEDDINGS;
  const currentLabel = currentModel ? shortModelLabel(currentModel) : "No embeddings";

  async function apply() {
    if (!pending || applying) return;
    setApplying(true);
    setError(null);
    try {
      const next = await updateEmbeddingModelApi(apiConfig, pending.model);
      setCatalog((current) => current && { ...current, current: next.model });
      setIndexStatus(next);
      setPending(null);
      await fetchServerConfig();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setApplying(false);
    }
  }

  async function retry() {
    if (retrying) return;
    setRetrying(true);
    setError(null);
    try {
      setIndexStatus(await startIndexingApi(apiConfig));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setRetrying(false);
    }
  }

  return (
    <>
      <SettingsSection title="Embeddings">
        <SettingsSurface>
          <SettingsSettingRow
            title="Embedding model"
            hint={
              currentModel
                ? "Used for semantic matching across facts and wiki pages."
                : "Semantic matching is off. Keyword search remains available."
            }
            control={
              <div className="model-picker--embedding">
                <ModelMenuPicker
                  value={currentValue}
                  options={options}
                  ariaLabel="Choose embedding model"
                  disabled={disabled}
                  triggerClassName="model-picker__trigger model-picker__model-trigger group"
                  trigger={
                    <>
                      <BlurSwap swapKey={currentValue} className="model-picker__current-model">
                        {loading ? "Loading…" : currentLabel}
                      </BlurSwap>
                      <ChevronDown size={ICON.SM} strokeWidth={2} className="model-picker__chevron" />
                    </>
                  }
                  onValueChange={(value) => {
                    const model = value === NO_EMBEDDINGS ? null : value;
                    if (model !== currentModel) setPending({ model });
                  }}
                />
              </div>
            }
          />
          {reindexing && indexStatus && <EmbeddingProgress status={indexStatus} />}
        </SettingsSurface>
      </SettingsSection>

      {indexStatus?.state === "error" && (
        <SettingsInlineError
          title="Couldn't rebuild semantic index"
          message={indexStatus.error ?? "The rebuild stopped before it completed."}
          action={
            <Button size="sm" onClick={() => void retry()} disabled={retrying}>
              {retrying ? "Retrying…" : "Retry"}
            </Button>
          }
        />
      )}
      {loadError && (
        <SettingsInlineError
          title="Couldn't load embedding settings"
          message={loadError}
          action={<Button size="sm" onClick={() => setLoadAttempt((attempt) => attempt + 1)}>Retry</Button>}
        />
      )}
      {error && <SettingsInlineError title="Couldn't update embeddings" message={error} />}

      <DialogLayer
        open={pending !== null}
        alert
        className="settings-embedding-dialog"
        labelledBy="embedding-confirmation-title"
        describedBy="embedding-confirmation-description"
        onClose={() => !applying && setPending(null)}
      >
        <div className="settings-embedding-dialog__panel">
          <div>
            <h2 id="embedding-confirmation-title">
              {pending?.model === null ? "Turn off embeddings?" : "Rebuild semantic index?"}
            </h2>
            <p id="embedding-confirmation-description">
              {pending?.model === null
                ? "Semantic matching will be turned off. Keyword search remains available."
                : `Changing to ${pending?.model} re-embeds facts and wiki pages. This may take a while.`}
            </p>
          </div>
          <footer>
            <Button variant="quiet" autoFocus onClick={() => setPending(null)} disabled={applying}>
              Cancel
            </Button>
            <Button variant="primary" onClick={() => void apply()} disabled={applying}>
              {applying
                ? "Applying…"
                : pending?.model === null
                  ? "Turn off embeddings"
                  : "Rebuild index"}
            </Button>
          </footer>
        </div>
      </DialogLayer>
    </>
  );
}

function EmbeddingProgress({ status }: { status: IndexStatus }) {
  const phase = status.phase === "facts" ? "facts" : status.phase === "wiki" ? "wiki pages" : "index";
  const retrySeconds = status.retry_at === null
    ? null
    : Math.max(0, Math.ceil((Date.parse(status.retry_at) - Date.now()) / 1_000));
  const label = retrySeconds === null
    ? `Rebuilding ${phase}`
    : `Rate limited — resuming in ${retrySeconds}s`;
  const hasTotal = status.total > 0;

  return (
    <div className="settings-embedding-progress" role="status" aria-live="polite">
      <div className="settings-embedding-progress-head">
        <span>{label}</span>
        <span>{hasTotal ? `${Math.min(status.done, status.total)} / ${status.total}` : "Preparing…"}</span>
      </div>
      <progress
        aria-label={label}
        value={hasTotal ? Math.min(status.done, status.total) : undefined}
        max={hasTotal ? status.total : undefined}
      />
    </div>
  );
}

function Section({
  title,
  description,
  current,
  groups,
  savingModel,
  savingReasoning,
  reasoningEfforts,
  currentReasoning,
  onSelect,
  onSetReasoning,
}: {
  title: string;
  description: string;
  current: string;
  groups: ModelGroup[];
  savingModel: boolean;
  savingReasoning: boolean;
  reasoningEfforts: Record<string, string[]>;
  currentReasoning: string | null;
  onSelect: (model: string) => void;
  onSetReasoning: (effort: string | null) => void;
}) {
  const efforts = reasoningEfforts[current] ?? [];

  return (
    <SettingsSettingRow
      title={title}
      hint={description}
      control={<ModelReasoningPicker
        buttonLabel={savingModel ? "Saving…" : undefined}
        currentModel={current}
        currentEffort={currentReasoning}
        efforts={efforts}
        groups={groups}
        disabled={savingModel || savingReasoning}
        onSelectModel={onSelect}
        onSelectEffort={onSetReasoning}
      />}
    />
  );
}
