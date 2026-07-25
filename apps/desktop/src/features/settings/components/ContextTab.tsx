import { useEffect, useState } from "react";
import { NumberField, PercentField } from "@/features/settings/components/Field";
import { updateServerConfig, fetchServerConfig } from "@/actions/server";
import type { ServerConfigPatch } from "@/api/settings";
import type { ServerConfig } from "@/api/types";
import { useStore } from "@/stores";
import { Button } from "@/components/ui/Button";
import { SettingsTabSkeleton } from "@/features/settings/components/SettingsTabSkeleton";
import { SettingsConnectionHint, SettingsInlineError } from "@/features/settings/components/SettingsNotice";
import { SettingsSection } from "@/features/settings/components/SettingsPage";

type Draft = Pick<
  ServerConfig,
  | "compression_threshold"
  | "max_messages"
  | "compression_keep_ratio"
  | "summary_max_tokens"
  | "consolidation_interval"
>;

const KEYS: Array<keyof Draft> = [
  "compression_threshold",
  "max_messages",
  "compression_keep_ratio",
  "summary_max_tokens",
  "consolidation_interval",
];

export function ContextTab({ serverConfig }: { serverConfig: ServerConfig | null }) {
  const connected = useStore((s) => s.connected);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!serverConfig) return;
    setDraft({
      compression_threshold: serverConfig.compression_threshold,
      max_messages: serverConfig.max_messages,
      compression_keep_ratio: serverConfig.compression_keep_ratio,
      summary_max_tokens: serverConfig.summary_max_tokens,
      consolidation_interval: serverConfig.consolidation_interval,
    });
  }, [serverConfig]);

  if (!serverConfig || !draft) {
    if (!connected) return <SettingsConnectionHint />;
    return <SettingsTabSkeleton rows={5} label="Loading context settings…" />;
  }

  const dirty = KEYS.some((k) => draft[k] !== serverConfig[k]);

  const update = (patch: Partial<Draft>) => setDraft((prev) => (prev ? { ...prev, ...patch } : prev));

  const save = async () => {
    if (!dirty || saving) return;
    setSaving(true);
    setError(null);
    try {
      const patch: ServerConfigPatch = {};
      for (const k of KEYS) {
        if (draft[k] !== serverConfig[k]) {
          (patch as Record<string, unknown>)[k] = draft[k];
        }
      }
      await updateServerConfig(patch);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      await fetchServerConfig();
    } finally {
      setSaving(false);
    }
  };

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    void save();
  };

  return (
    <form onSubmit={submit}>
      <SettingsSection title="Compression" detail="save changes">
        <div className="settings-field-stack">
          <PercentField
            label="Compression threshold"
            help="Share of the model's context window used before older turns start being compressed."
            value={draft.compression_threshold}
            min={10}
            max={100}
            onChange={(n) => update({ compression_threshold: n })}
          />

          <NumberField
            label="Max messages"
            help="Hard cap on the number of raw messages kept before compression kicks in."
            value={draft.max_messages}
            min={10}
            max={1000}
            step={10}
            onChange={(n) => update({ max_messages: n })}
          />

          <PercentField
            label="Keep ratio"
            help="Share of recent messages preserved verbatim during compression."
            value={draft.compression_keep_ratio}
            min={0}
            max={100}
            onChange={(n) => update({ compression_keep_ratio: n })}
          />

          <NumberField
            label="Summary max tokens"
            suffix="tokens"
            help="Upper bound on each compression summary."
            value={draft.summary_max_tokens}
            min={256}
            max={8000}
            step={64}
            onChange={(n) => update({ summary_max_tokens: n })}
          />

          <NumberField
            label="Reflection interval"
            suffix="messages"
            help="How many user messages between knowledge reflection passes."
            value={draft.consolidation_interval}
            min={1}
            max={500}
            step={5}
            onChange={(n) => update({ consolidation_interval: n })}
          />
        </div>

        {error && <SettingsInlineError title="Couldn't save" message={error} />}

        <div className="settings-field-actions">
          <Button type="submit" variant="primary" disabled={saving} aria-busy={saving}>
            {saving ? "Saving" : "Save changes"}
          </Button>
        </div>
      </SettingsSection>
    </form>
  );
}
