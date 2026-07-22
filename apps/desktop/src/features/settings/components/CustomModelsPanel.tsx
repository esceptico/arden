import { Loader2, Plus } from "@/components/icons";
import { type CustomModelSummary, type ModelProvider } from "@/api/settings";
import { canSaveCustomModelDraft, type CustomModelDraft } from "@/features/settings/lib/customModelDraft";
import { ICON } from "@/lib/icons";
import { IconSwap } from "@/components/ui/IconSwap";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { NumberField } from "@/features/settings/components/Field";
import { ConfirmDeleteButton } from "@/components/ui/ConfirmDeleteButton";

const MAX_CONTEXT_WINDOW = 2_000_000;

function customModels(provider: ModelProvider): CustomModelSummary[] {
  return provider.models.filter((model): model is CustomModelSummary => typeof model !== "string");
}

export function CustomModelsPanel({
  provider,
  draft,
  pendingId,
  onDraftChange,
  onCreate,
  onDelete,
}: {
  provider: ModelProvider;
  draft: CustomModelDraft;
  pendingId: string | null;
  onDraftChange: (patch: Partial<CustomModelDraft>) => void;
  onCreate: () => void;
  onDelete: (modelId: string) => void;
}) {
  const models = customModels(provider);
  const creating = pendingId === "custom:create";

  return (
    <div className="grid gap-3 px-3.5 py-3 bg-surface-soft/35">
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
            aria-label="Model ID"
            spellCheck={false}
          />
          <Input
            value={draft.base_url}
            onChange={(event) => onDraftChange({ base_url: event.target.value })}
            placeholder="base URL"
            aria-label="Base URL"
            spellCheck={false}
            autoComplete="off"
          />
        </div>
        <NumberField
          label="Context window"
          suffix="tokens"
          value={draft.context_window}
          min={1}
          max={MAX_CONTEXT_WINDOW}
          step={1024}
          onChange={(n) => onDraftChange({ context_window: n })}
        />
        <NumberField
          label="Max output tokens"
          suffix="tokens"
          value={draft.max_output_tokens}
          min={1}
          max={draft.context_window}
          step={256}
          onChange={(n) => onDraftChange({ max_output_tokens: n })}
        />
        <div className="grid grid-cols-[repeat(auto-fit,minmax(130px,1fr))] gap-2">
          <Input
            type="password"
            value={draft.api_key}
            onChange={(event) => onDraftChange({ api_key: event.target.value })}
            placeholder="API key (optional)"
            spellCheck={false}
            autoComplete="off"
          />
          <Button onClick={onCreate} disabled={!canSaveCustomModelDraft(draft) || creating}>
            <IconSwap
              state={creating ? "b" : "a"}
              iconA={<Plus size={ICON.MD} strokeWidth={2} />}
              iconB={<Loader2 size={ICON.MD} strokeWidth={2} className="animate-spin" />}
            />
            Add
          </Button>
        </div>
      </div>
    </div>
  );
}
