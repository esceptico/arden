import { useEffect, useRef, useState } from "react";
import { NumberField } from "@/features/settings/components/Field";
import { updateServerConfig, fetchServerConfig } from "@/actions/server";
import type { ServerConfig } from "@/api/types";
import { useStore } from "@/stores";
import { useMutationState } from "@/lib/hooks";
import { SettingsConnectionHint, SettingsInlineError } from "@/features/settings/components/SettingsNotice";
import { SaveStatus } from "@/features/settings/components/SaveStatus";
import { SettingsTabSkeleton } from "@/features/settings/components/SettingsTabSkeleton";
import { SectionHeader } from "@/components/ui/SectionHeader";

// Coalesce per-keystroke edits so typing "16" saves once (16), not 1 then 16.
const SAVE_DEBOUNCE_MS = 500;

export function AgentTab({ serverConfig }: { serverConfig: ServerConfig | null }) {
  const connected = useStore((s) => s.connected);
  const { busy, saved, error, run } = useMutationState();
  const [depth, setDepth] = useState<number | null>(serverConfig?.max_depth ?? null);
  const timer = useRef<number | undefined>(undefined);

  // Server is the source of truth; resync the draft whenever it changes
  // (initial load, or revert after a failed save).
  useEffect(() => {
    if (serverConfig) setDepth(serverConfig.max_depth);
  }, [serverConfig]);
  useEffect(() => () => window.clearTimeout(timer.current), []);

  if (!serverConfig || depth === null) {
    if (!connected) return <SettingsConnectionHint />;
    return <SettingsTabSkeleton label="Loading agent settings…" />;
  }

  const onChange = (n: number) => {
    setDepth(n); // reflect the keystroke immediately
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => {
      void run(async () => {
        try {
          await updateServerConfig({ max_depth: n });
        } catch (e) {
          await fetchServerConfig(); // revert to server truth (resyncs draft)
          throw e;
        }
      });
    }, SAVE_DEBOUNCE_MS);
  };

  return (
    <div className="grid gap-5">
      <SectionHeader label="Sub-agents" action={<SaveStatus busy={busy} saved={saved} />} />

      <NumberField
        label="Max sub-agent depth"
        help="How deep Arden will spawn sub-agents before refusing to recurse further."
        value={depth}
        min={1}
        max={16}
        onChange={onChange}
      />

      {error && <SettingsInlineError title="Couldn't save" message={error} />}
    </div>
  );
}
