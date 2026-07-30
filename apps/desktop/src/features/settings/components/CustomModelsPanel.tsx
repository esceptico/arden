import { Loader2, Plus } from "@/components/icons";
import {
  type CustomEmbeddingModelSummary,
  type CustomModelSummary,
  type ModelProvider,
} from "@/api/settings";
import {
  canSaveCustomEmbeddingModelDraft,
  canSaveCustomModelDraft,
  CUSTOM_EMBEDDING_MAX_DIMENSIONS,
  CUSTOM_MODEL_MAX_CONTEXT_WINDOW,
  customModelContextPatch,
  type CustomEmbeddingModelDraft,
  type CustomModelDraft,
} from "@/features/settings/lib/customModelDraft";
import { ICON } from "@/lib/icons";
import { IconSwap } from "@/components/ui/IconSwap";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { ConfirmDeleteButton } from "@/components/ui/ConfirmDeleteButton";

function customModels(provider: ModelProvider): CustomModelSummary[] {
  return provider.models.filter((model): model is CustomModelSummary => typeof model !== "string");
}

function customEmbeddingModels(provider: ModelProvider): CustomEmbeddingModelSummary[] {
  return provider.custom_embedding_models ?? [];
}

export function CustomModelsPanel({
  provider,
  draft,
  embeddingDraft,
  pendingId,
  onDraftChange,
  onEmbeddingDraftChange,
  onCreate,
  onCreateEmbedding,
  onDelete,
  onDeleteEmbedding,
}: {
  provider: ModelProvider;
  draft: CustomModelDraft;
  embeddingDraft: CustomEmbeddingModelDraft;
  pendingId: string | null;
  onDraftChange: (patch: Partial<CustomModelDraft>) => void;
  onEmbeddingDraftChange: (patch: Partial<CustomEmbeddingModelDraft>) => void;
  onCreate: () => void;
  onCreateEmbedding: () => void;
  onDelete: (modelId: string) => void;
  onDeleteEmbedding: (modelId: string) => void;
}) {
  const models = customModels(provider);
  const embeddingModels = customEmbeddingModels(provider);
  const creating = pendingId === "custom:create";
  const creatingEmbedding = pendingId === "custom:embedding:create";
  const busy = pendingId !== null;

  return (
    <div className="grid gap-5 px-3.5 py-3 bg-surface-soft/35">
      <section className="grid gap-2" aria-labelledby="custom-chat-models-title">
        <div>
          <h3 id="custom-chat-models-title" className="text-sm font-medium text-ink-soft">Chat models</h3>
          <p className="text-xs text-muted">OpenAI-compatible chat completion endpoints.</p>
        </div>
        <div className="grid gap-1.5">
          {models.length === 0 ? (
            <div className="text-sm text-muted">No custom models configured.</div>
          ) : (
            models.map((model) => {
              const deleting = pendingId === `custom:delete:${model.id}`;
              return (
                <div
                  key={model.id}
                  className="grid grid-cols-[minmax(0,1fr)_auto] gap-2 items-center rounded-[9px] border border-line-soft bg-surface px-2.5 py-2"
                >
                  <div className="min-w-0">
                    <div className="text-sm font-medium text-ink-soft truncate">{model.id}</div>
                    <div className="text-xs text-muted truncate">
                      {model.base_url || "default base URL"} · {model.context_window.toLocaleString()} ctx
                    </div>
                  </div>
                  <div className="flex items-center gap-1.5">
                    <ConfirmDeleteButton
                      size="md"
                      label={`Delete ${model.id}`}
                      busy={deleting}
                      onConfirm={() => onDelete(model.id)}
                    />
                  </div>
                </div>
              );
            })
          )}
        </div>

        <div className="grid gap-2">
          <div className="grid grid-cols-[repeat(auto-fit,minmax(180px,1fr))] gap-2">
            <Input
              value={draft.model_id}
              onChange={(event) => onDraftChange({ model_id: event.target.value })}
              placeholder="model id"
              aria-label="Chat model ID"
              spellCheck={false}
            />
            <Input
              value={draft.base_url}
              onChange={(event) => onDraftChange({ base_url: event.target.value })}
              placeholder="base URL"
              aria-label="Chat model base URL"
              spellCheck={false}
              autoComplete="off"
            />
          </div>
          <Input
            label="Context window"
            type="number"
            inputMode="numeric"
            value={draft.context_window}
            min={1}
            max={CUSTOM_MODEL_MAX_CONTEXT_WINDOW}
            step={1}
            onChange={(event) => {
              if (Number.isFinite(event.currentTarget.valueAsNumber)) {
                onDraftChange(customModelContextPatch(draft, event.currentTarget.valueAsNumber));
              }
            }}
          />
          <Input
            label="Max output tokens"
            type="number"
            inputMode="numeric"
            value={draft.max_output_tokens}
            min={1}
            max={draft.context_window}
            step={1}
            onChange={(event) => {
              if (Number.isFinite(event.currentTarget.valueAsNumber)) {
                onDraftChange({ max_output_tokens: event.currentTarget.valueAsNumber });
              }
            }}
          />
          <div className="grid grid-cols-[minmax(0,1fr)_auto] gap-2">
            <Input
              type="password"
              value={draft.api_key}
              onChange={(event) => onDraftChange({ api_key: event.target.value })}
              placeholder="API key (optional)"
              aria-label="API key (optional)"
              spellCheck={false}
              autoComplete="off"
            />
            <Button variant="primary" onClick={onCreate} disabled={!canSaveCustomModelDraft(draft) || busy}>
              <IconSwap
                state={creating ? "b" : "a"}
                iconA={<Plus size={ICON.MD} />}
                iconB={<Loader2 size={ICON.MD} className="animate-spin" />}
              />
              Add
            </Button>
          </div>
        </div>
      </section>

      <section className="grid gap-2" aria-labelledby="custom-embedding-models-title">
        <div>
          <h3 id="custom-embedding-models-title" className="text-sm font-medium text-ink-soft">
            Embedding models
          </h3>
          <p className="text-xs text-muted">OpenAI-compatible base URLs, usually ending in /v1.</p>
        </div>
        <div className="grid gap-1.5">
          {embeddingModels.length === 0 ? (
            <div className="text-sm text-muted">No custom embedding models configured.</div>
          ) : (
            embeddingModels.map((model) => {
              const deleting = pendingId === `custom:embedding:delete:${model.id}`;
              return (
                <div
                  key={model.id}
                  className="grid grid-cols-[minmax(0,1fr)_auto] gap-2 items-center rounded-[9px] border border-line-soft bg-surface px-2.5 py-2"
                >
                  <div className="min-w-0">
                    <div className="text-sm font-medium text-ink-soft truncate">{model.id}</div>
                    <div className="text-xs text-muted truncate">
                      {model.base_url || "default base URL"} · {model.dimensions.toLocaleString()} dimensions
                    </div>
                  </div>
                  <ConfirmDeleteButton
                    size="md"
                    label={`Delete ${model.id}`}
                    busy={deleting}
                    onConfirm={() => onDeleteEmbedding(model.id)}
                  />
                </div>
              );
            })
          )}
        </div>

        <div className="grid gap-2">
          <div className="grid grid-cols-[repeat(auto-fit,minmax(180px,1fr))] gap-2">
            <Input
              value={embeddingDraft.model_id}
              onChange={(event) => onEmbeddingDraftChange({ model_id: event.target.value })}
              placeholder="model id"
              aria-label="Embedding model ID"
              spellCheck={false}
            />
            <Input
              value={embeddingDraft.base_url}
              onChange={(event) => onEmbeddingDraftChange({ base_url: event.target.value })}
              placeholder="http://127.0.0.1:8081/v1"
              aria-label="Embedding model base URL"
              spellCheck={false}
              autoComplete="off"
            />
          </div>
          <Input
            label="Output dimensions"
            type="number"
            inputMode="numeric"
            value={embeddingDraft.dimensions}
            min={1}
            max={CUSTOM_EMBEDDING_MAX_DIMENSIONS}
            step={1}
            onChange={(event) => {
              if (Number.isFinite(event.currentTarget.valueAsNumber)) {
                onEmbeddingDraftChange({ dimensions: event.currentTarget.valueAsNumber });
              }
            }}
          />
          <div className="grid grid-cols-[minmax(0,1fr)_auto] gap-2">
            <Input
              type="password"
              value={embeddingDraft.api_key}
              onChange={(event) => onEmbeddingDraftChange({ api_key: event.target.value })}
              placeholder="API key (optional)"
              aria-label="Embedding API key (optional)"
              spellCheck={false}
              autoComplete="off"
            />
            <Button
              variant="primary"
              onClick={onCreateEmbedding}
              disabled={!canSaveCustomEmbeddingModelDraft(embeddingDraft) || busy}
            >
              <IconSwap
                state={creatingEmbedding ? "b" : "a"}
                iconA={<Plus size={ICON.MD} />}
                iconB={<Loader2 size={ICON.MD} className="animate-spin" />}
              />
              Add
            </Button>
          </div>
        </div>
      </section>
    </div>
  );
}
