import { Plus } from "@/components/icons";
import type { MCPServer } from "@/api/settings";
import { settingsErrorMessage } from "@/features/settings/lib/settingsLoadState";
import { SettingsConnectionHint, SettingsInlineError } from "@/features/settings/components/SettingsNotice";
import { ICON } from "@/lib/icons";
import { SectionHeader } from "@/components/ui/SectionHeader";
import { Button } from "@/components/ui/Button";
import { DividedList } from "@/components/ui/DividedList";
import { SettingsTabSkeleton } from "@/features/settings/components/SettingsTabSkeleton";
import { EmptyNote } from "@/components/ui/EmptyState";
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
    <div className="grid gap-4">
      <div>
        <p className="m-0 text-sm text-muted leading-[1.45] max-w-[460px]">
          Connect external tools and data sources via Model Context Protocol.
        </p>
      </div>

      <div className="grid gap-2">
        <SectionHeader
          label="Servers"
          action={
            !loadError && (
              <div className="flex flex-wrap gap-2">
                <Button variant="secondary" size="sm" onClick={onAssistant}>
                  Run setup assistant
                </Button>
                <Button size="sm" onClick={onAdd}>
                  <Plus size={ICON.XS} strokeWidth={2.2} /> Add server
                </Button>
              </div>
            )
          }
        />

        {servers === null ? (
          <SettingsTabSkeleton variant="cards" label="Loading MCP servers…" />
        ) : loadError ? (
          <div className="grid gap-3">
            <SettingsInlineError
              title="Couldn't load MCP servers"
              message={settingsErrorMessage(loadError)}
              action={
                <Button variant="secondary" size="sm" onClick={() => void onChanged()}>
                  Retry
                </Button>
              }
            />
            <SettingsConnectionHint />
          </div>
        ) : servers.length === 0 ? (
          <EmptyNote>No MCP servers yet.</EmptyNote>
        ) : (
          <DividedList className="min-w-0 overflow-hidden">
            {servers.map((s) => (
              <ServerRow key={s.name} server={s} onEdit={() => onEdit(s.name)} onChanged={onChanged} />
            ))}
          </DividedList>
        )}
      </div>
    </div>
  );
}
