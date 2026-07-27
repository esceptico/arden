import { useState } from "react";
import { useStore } from "@/stores";
import { updateServerConfig, fetchServerConfig } from "@/actions/server";
import type { ModelGroup } from "@/api/types";
import { ModelReasoningPicker } from "@/components/ui/ModelPickers";
import { useTimeoutFlag } from "@/lib/hooks";
import { SettingsConnectionHint, SettingsInlineError } from "@/features/settings/components/SettingsNotice";
import { SaveStatus } from "@/features/settings/components/SaveStatus";
import {
  SettingsSection,
  SettingsSettingRow,
  SettingsSurface,
} from "@/features/settings/components/SettingsPage";
import { SettingsTabSkeleton } from "@/features/settings/components/SettingsTabSkeleton";
import { Button } from "@/components/ui/Button";

type ModelKind = "research_model" | "workflow_model" | "memory_model";

const KIND_LABELS: Record<ModelKind, { title: string; description: string }> = {
  research_model: {
    title: "Research",
    description: "Used by research-style sub-agents and deeper investigations.",
  },
  workflow_model: {
    title: "Workflows",
    description: "Default for workflow agents; a script's explicit per-agent model still wins.",
  },
  memory_model: {
    title: "Memory",
    description: "Knowledge reflection, activation, and retention.",
  },
};

const SETTINGS_MODEL_KINDS: ModelKind[] = ["research_model", "workflow_model", "memory_model"];

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
          const current = cfg[kind];
          // Absent until the server restarts onto a build that ships this key.
          if (current === undefined) return null;
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
              currentReasoning={cfg.model_reasoning_efforts[current] ?? null}
              onSelect={async (model) => {
                if (model === current || saving) return;
                setSaving(`${kind}:model`);
                setError(null);
                try {
                  await updateServerConfig({ [kind]: model });
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
                  await updateServerConfig({
                    reasoning_model: current,
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
          );
        })}
        </SettingsSurface>
      </SettingsSection>
      {error && (
        <SettingsInlineError title="Couldn't update model" message={error} />
      )}
    </>
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
