import { useState } from "react";
import { ArrowLeft } from "@/components/icons";
import { useStore } from "@/stores";
import { type MCPServer, type MCPServerConfigPayload, type MCPTransport, addMCPServerApi, removeMCPServerApi, startMCPOAuthApi, updateMCPServerApi } from "@/api/settings";
import { useMutationState } from "@/lib/hooks";
import { SettingsInlineError } from "@/features/settings/components/SettingsNotice";
import { SaveStatus } from "@/features/settings/components/SaveStatus";
import { Button } from "@/components/ui/Button";
import { ConfirmDeleteButton } from "@/components/ui/ConfirmDeleteButton";
import { Tab, Tabs } from "@/components/ui/Tabs";
import { Input } from "@/components/ui/Input";
import { type KeyVal } from "@/features/settings/components/mcp/editors";
import { buildMCPServerPayload, type MCPAuthMode } from "@/features/settings/components/mcp/payload";
import { HttpFields, StdioFields } from "@/features/settings/components/mcp/transportFields";
import { OAuthStatus } from "@/features/settings/components/mcp/OAuthStatus";
import { ToolsSection } from "@/features/settings/components/mcp/ToolsSection";

export function ServerForm({
  mode,
  server,
  onClose,
  onSaved,
  onRemoved,
}: {
  mode: "add" | "edit";
  server?: MCPServer;
  onClose: () => void;
  onSaved: () => Promise<void>;
  onRemoved?: () => Promise<void>;
}) {
  const config = useStore((s) => s.config);
  const { busy, saved, error, run } = useMutationState();

  const initialTransport: MCPTransport =
    server?.transport === "http" || server?.transport === "stdio" ? server.transport : "http";

  const [name, setName] = useState(server?.name ?? "");
  const [transport, setTransport] = useState<MCPTransport>(initialTransport);

  // stdio fields
  const [command, setCommand] = useState(server?.command ?? "");
  const [argsList, setArgsList] = useState<string[]>(server?.args ?? [""]);
  const [envEntries, setEnvEntries] = useState<KeyVal[]>([{ key: "", value: "" }]);

  // http fields
  const existingHeaderKeys = server?.header_keys ?? [];
  const [url, setUrl] = useState(server?.url ?? "");
  const [headerEntries, setHeaderEntries] = useState<KeyVal[]>(
    existingHeaderKeys.length > 0
      ? existingHeaderKeys.map((key) => ({ key, value: "" }))
      : [{ key: "", value: "" }],
  );
  const [auth, setAuth] = useState<MCPAuthMode>(
    existingHeaderKeys.length > 0 ? "headers" : "auto",
  );

  const isOAuth = mode === "edit" && server?.auth === "oauth";

  const onReauthenticate = () =>
    void run(async () => {
      if (!server) return;
      await startMCPOAuthApi(config, server.name);
      await onSaved();
    });

  function buildPayload(): MCPServerConfigPayload {
    return buildMCPServerPayload({
      transport,
      command,
      argsList,
      envEntries,
      url,
      headerEntries,
      auth,
    });
  }

  const valid =
    name.trim().length > 0 &&
    (transport === "stdio" ? command.trim().length > 0 : url.trim().length > 0);

  async function save() {
    if (!valid) return;
    const payload = buildPayload();
    await run(async () => {
      if (mode === "add") {
        await addMCPServerApi(config, name.trim(), payload);
      } else if (server) {
        await updateMCPServerApi(config, server.name, payload);
      }
      await onSaved();
    });
  }

  async function remove() {
    if (!server) return;
    await run(async () => {
      await removeMCPServerApi(config, server.name);
      if (onRemoved) await onRemoved();
    });
  }

  const transportLocked = mode === "edit";

  return (
    <div className="grid gap-4">
      <div className="flex items-center justify-between gap-3">
        <Button variant="quiet" size="sm" onClick={onClose} leadingIcon={ArrowLeft}>
          Back
        </Button>
        {mode === "edit" && server && (
          <ConfirmDeleteButton
            label="Uninstall"
            busy={busy}
            size="md"
            onConfirm={() => void remove()}
          />
        )}
      </div>

      <h3 className="m-0 text-lg font-semibold tracking-[-0.012em] text-ink">
        {mode === "add" ? "Connect to a custom MCP" : `Update ${server?.name} MCP`}
      </h3>

      <div className="grid gap-3">
        <Input
          label="Name"
          size="sm"
          value={name}
          onChange={(e) => setName(e.target.value)}
          disabled={mode === "edit"}
          placeholder="MCP server name"
          spellCheck={false}
          className="disabled:bg-surface-soft disabled:text-muted"
        />

        {transportLocked ? (
          <div className="inline-flex items-center h-8 px-3 rounded-md bg-surface-soft border border-line-soft text-sm font-medium text-ink-soft self-start">
            {transport === "stdio" ? "STDIO" : "Streamable HTTP"}
          </div>
        ) : (
          <Tabs variant="segmented"
            size="sm"
            value={transport}
            onChange={(v) => setTransport(v as MCPTransport)}
          >
            <Tab value="stdio">STDIO</Tab>
            <Tab value="http">Streamable HTTP</Tab>
          </Tabs>
        )}

        {transportLocked && (
          <p className="m-0 text-xs text-muted">
            To switch transport type, uninstall first.
          </p>
        )}

        {transport === "stdio" ? (
          <StdioFields
            command={command}
            onCommand={setCommand}
            argsList={argsList}
            onArgs={setArgsList}
            envEntries={envEntries}
            onEnv={setEnvEntries}
          />
        ) : (
          <HttpFields
            url={url}
            onUrl={setUrl}
            headerEntries={headerEntries}
            onHeaders={setHeaderEntries}
            auth={auth}
            onAuth={setAuth}
            hasExistingHeaders={existingHeaderKeys.length > 0}
            oauthSection={
              isOAuth && server ? (
                <OAuthStatus server={server} busy={busy} onReauthenticate={onReauthenticate} />
              ) : undefined
            }
          />
        )}
      </div>

      {mode === "edit" && server && server.tools.length > 0 && (
        <ToolsSection server={server} onChanged={onSaved} />
      )}

      {error && <SettingsInlineError title="Couldn't save MCP server" message={error} />}

      <div className="flex items-center justify-end gap-3 pt-1">
        <SaveStatus busy={busy} saved={saved} />
        <Button variant="primary" size="md" onClick={() => void save()} disabled={!valid || busy}>
          Save
        </Button>
      </div>
    </div>
  );
}
