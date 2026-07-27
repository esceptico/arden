import { Plus } from "@/components/icons";
import type { MCPServer } from "@/api/settings";
import { settingsErrorMessage } from "@/features/settings/lib/settingsLoadState";
import { SettingsConnectionHint, SettingsInlineError } from "@/features/settings/components/SettingsNotice";
import { SettingsPageAction, SettingsSection, SettingsSurface } from "@/features/settings/components/SettingsPage";
import { ICON } from "@/lib/icons";
import { Button } from "@/components/ui/Button";
import { IconButton } from "@/components/ui/IconButton";
import { SettingsTabSkeleton } from "@/features/settings/components/SettingsTabSkeleton";
import { ServerRow } from "@/features/settings/components/mcp/ServerRow";

export function ServerList({
  servers,
  loadError,
  onAdd,
  onEdit,
  onChanged,
  onAssistant,
}: {
  servers: MCPServer[] | null;
  loadError: string | null;
  onAdd: () => void;
  onEdit: (name: string) => void;
  onChanged: () => Promise<void>;
  onAssistant: () => void;
}) {
  return (
    <SettingsSection title="Servers">
      {/* Page-scoped utilities live on the page header (the placement
          grammar in docs/design-language.md); this action list unmounts
          with the list view, so the form views keep a clean header. */}
      {!loadError && (
        <SettingsPageAction>
          <span className="flex items-center gap-2">
            <Button size="sm" onClick={onAssistant}>
              Guided setup
            </Button>
            <IconButton
              tone="primary"
              shape="circle"
              onClick={onAdd}
              aria-label="Add MCP server"
            >
              <Plus size={ICON.XS} />
            </IconButton>
          </span>
        </SettingsPageAction>
      )}
      {servers === null ? (
        <SettingsTabSkeleton variant="cards" label="Loading MCP servers…" />
      ) : loadError ? (
        <div className="grid gap-3">
          <SettingsInlineError
            title="Couldn't load MCP servers"
            message={settingsErrorMessage(loadError)}
            action={
              <Button size="sm" onClick={() => void onChanged()}>
                Retry
              </Button>
            }
          />
          <SettingsConnectionHint />
        </div>
      ) : servers.length === 0 ? (
        <div className="settings-mcp-empty">
          <strong>No servers configured</strong>
          <span>
            Guided setup starts from a URL, package, or command. Add server opens every field directly.
          </span>
        </div>
      ) : (
        <SettingsSurface>
          <ul className="m-0 min-w-0 list-none p-0">
            {servers.map((s) => (
              <ServerRow key={s.name} server={s} onEdit={() => onEdit(s.name)} onChanged={onChanged} />
            ))}
          </ul>
        </SettingsSurface>
      )}
    </SettingsSection>
  );
}
