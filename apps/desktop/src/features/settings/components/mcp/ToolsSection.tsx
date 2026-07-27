import { useMemo } from "react";
import { useStore } from "@/stores";
import { type MCPServer, updateMCPToolsApi } from "@/api/settings";
import type { ToolOverrideDecision } from "@/api/types";
import { fetchServerConfig, updateServerConfig } from "@/actions/server";
import { useMutationState } from "@/lib/hooks";
import { SettingsInlineError } from "@/features/settings/components/SettingsNotice";
import { SaveStatus } from "@/features/settings/components/SaveStatus";
import { ToolPolicySelect } from "@/features/settings/components/ToolPolicySelect";
import { SettingsSection, SettingsSurface } from "@/features/settings/components/SettingsPage";
import { SwitchControl } from "@/components/ui/SwitchControl";

export function ToolsSection({
  server,
  onChanged,
}: {
  server: MCPServer;
  onChanged: () => Promise<void>;
}) {
  const config = useStore((s) => s.config);
  const serverConfig = useStore((s) => s.serverConfig);
  const { busy, saved, error, run } = useMutationState();
  const overrides = serverConfig?.tool_overrides ?? {};

  const enabledNames = useMemo(
    () => new Set(server.tools.filter((t) => t.enabled).map((t) => t.name)),
    [server.tools],
  );

  function commit(next: Set<string>) {
    // null means "all tools enabled" (no whitelist).
    const allEnabled = next.size === server.tools.length;
    const tools = allEnabled ? null : Array.from(next);
    void run(async () => {
      await updateMCPToolsApi(config, server.name, tools);
      await onChanged();
    });
  }

  function baseDecision(tool: MCPServer["tools"][number]): ToolOverrideDecision {
    return tool.policy.requires_approval ? "ask" : "approve";
  }

  function setToolDecision(tool: MCPServer["tools"][number], decision: ToolOverrideDecision) {
    const next = { ...overrides };
    if (decision === baseDecision(tool)) delete next[tool.full_name];
    else next[tool.full_name] = decision;
    void run(async () => {
      await updateServerConfig({ tool_overrides: next });
      await fetchServerConfig();
      await onChanged();
    });
  }

  return (
    <SettingsSection
      title="Tools"
      detail={`${server.tools.length} tools`}
      action={<SaveStatus busy={busy} saved={saved} />}
    >
      <SettingsSurface>
        {server.tools.map((t) => {
          const checked = enabledNames.has(t.name);
          return (
            <div key={t.name} className="settings-data-row settings-tool-row">
              <div className="settings-data-row-main">
                <div className="settings-data-row-title">
                  <SwitchControl
                    checked={checked}
                    disabled={busy}
                    onChange={(next) => {
                      const updated = new Set(enabledNames);
                      if (next) updated.add(t.name);
                      else updated.delete(t.name);
                      commit(updated);
                    }}
                    aria-label={`Include ${t.name}`}
                  />
                  <span className="truncate">{t.name}</span>
                </div>
                <div className="settings-data-row-sub">{t.full_name}</div>
              </div>
              <div className="settings-data-row-copy">{t.description}</div>
              <div className="settings-data-row-end">
                <ToolPolicySelect
                  value={overrides[t.full_name] ?? baseDecision(t)}
                  onChange={(decision) => setToolDecision(t, decision)}
                />
              </div>
            </div>
          );
        })}
      </SettingsSurface>
      {error && <SettingsInlineError title="Couldn't update tools" message={error} />}
    </SettingsSection>
  );
}
