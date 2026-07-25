import { useEffect, useMemo, useState } from "react";
import { fetchServerConfig, updateServerConfig } from "@/actions/server";
import { listToolsApi } from "@/api/settings";
import type { ToolMetadata, ToolOverrideDecision } from "@/api/types";
import { useStore } from "@/stores";
import { useMutationState } from "@/lib/hooks";
import { settingsErrorMessage } from "@/features/settings/lib/settingsLoadState";
import { SettingsConnectionHint, SettingsInlineError } from "@/features/settings/components/SettingsNotice";
import { SaveStatus } from "@/features/settings/components/SaveStatus";
import { SettingsTabSkeleton } from "@/features/settings/components/SettingsTabSkeleton";
import { ToolPolicySelect } from "@/features/settings/components/ToolPolicySelect";
import { SettingsSection, SettingsSurface } from "@/features/settings/components/SettingsPage";
import { SearchInput } from "@/components/ui/SearchInput";
import { Button } from "@/components/ui/Button";

export function ToolsTab() {
  const config = useStore((s) => s.config);
  const serverConfig = useStore((s) => s.serverConfig);
  const { busy, saved, error, run } = useMutationState();
  const [tools, setTools] = useState<ToolMetadata[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [query, setQuery] = useState("");

  async function refresh() {
    setLoadError(null);
    try {
      const r = await listToolsApi(config);
      setTools(r.tools);
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : String(err));
      setTools([]);
    }
  }

  useEffect(() => {
    void refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const overrides = serverConfig?.tool_overrides ?? {};
  const nonMcpTools = useMemo(() => (tools ?? []).filter((tool) => tool.source !== "mcp"), [tools]);

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return nonMcpTools;
    return nonMcpTools.filter((tool) =>
      [tool.name, tool.display_name, tool.description, tool.source ?? ""]
        .join(" ")
        .toLowerCase()
        .includes(needle),
    );
  }, [nonMcpTools, query]);

  const groups = useMemo(() => {
    const out = new Map<string, ToolMetadata[]>();
    for (const tool of filtered) {
      const source = tool.source || "unknown";
      if (!out.has(source)) out.set(source, []);
      out.get(source)!.push(tool);
    }
    return Array.from(out.entries()).sort(([a], [b]) => a.localeCompare(b));
  }, [filtered]);

  function baseDecision(tool: ToolMetadata): ToolOverrideDecision {
    return tool.policy.requires_approval ? "ask" : "approve";
  }

  function setOverride(tool: ToolMetadata, decision: ToolOverrideDecision) {
    const next = { ...overrides };
    if (decision === baseDecision(tool)) delete next[tool.name];
    else next[tool.name] = decision;
    void run(async () => {
      await updateServerConfig({ tool_overrides: next });
      await fetchServerConfig();
      await refresh();
    });
  }

  if (tools === null) {
    return <SettingsTabSkeleton label="Loading tools…" />;
  }

  if (loadError) {
    return (
      <div className="grid gap-[30px]">
        <SettingsInlineError
          title="Couldn't load tools"
          message={settingsErrorMessage(loadError)}
          action={
            <Button variant="secondary" size="sm" onClick={() => void refresh()}>
              Retry
            </Button>
          }
        />
        <SettingsConnectionHint />
      </div>
    );
  }

  return (
    <div className="grid gap-[30px]">
      <div className="settings-list-toolbar">
        <SearchInput
          value={query}
          onChange={setQuery}
          placeholder="Search tools"
          trailing={formatToolCount(filtered.length)}
          className="settings-list-search"
        />
        <SaveStatus busy={busy} saved={saved} className="settings-list-save-status" />
      </div>

      {error && <SettingsInlineError title="Couldn't save tool override" message={error} />}

      <div className="grid gap-3">
        {groups.length === 0 && (
          <div className="settings-empty-note">
            {query.trim() ? `No tools match "${query.trim()}".` : "No tools available."}
          </div>
        )}
        {groups.map(([source, items]) => (
          <SettingsSection key={source} title={formatSource(source)} detail={formatToolCount(items.length)}>
            <SettingsSurface>
              {items.map((tool) => {
                const current = overrides[tool.name] ?? baseDecision(tool);
                return (
                  <div key={tool.name} className="settings-data-row settings-tool-row">
                    <div className="settings-data-row-main">
                      <div className="settings-data-row-title">
                        <span className="truncate">{tool.display_name}</span>
                      </div>
                      <div className="settings-data-row-sub">{tool.name} · {tool.policy.action}</div>
                    </div>
                    <div className="settings-data-row-copy">{tool.description}</div>
                    <div className="settings-data-row-end">
                      <ToolPolicySelect
                        value={current}
                        onChange={(decision) => setOverride(tool, decision)}
                      />
                    </div>
                  </div>
                );
              })}
            </SettingsSurface>
          </SettingsSection>
        ))}
      </div>
    </div>
  );
}

function formatToolCount(count: number): string {
  return `${count} ${count === 1 ? "tool" : "tools"}`;
}

function formatSource(source: string): string {
  const normalized = source.replace(/^_+/, "");
  if (normalized === "builtin" || normalized === "built_in") return "Built in";
  const label = normalized.replaceAll("_", " ");
  return label ? `${label[0].toUpperCase()}${label.slice(1)}` : "Unknown";
}
