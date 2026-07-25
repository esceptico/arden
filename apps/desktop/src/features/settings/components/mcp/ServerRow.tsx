import { Settings as SettingsIcon } from "@/components/icons";
import { useStore } from "@/stores";
import { type MCPServer, startMCPOAuthApi, toggleMCPServerApi } from "@/api/settings";
import { useMutationState } from "@/lib/hooks";
import { ICON } from "@/lib/icons";
import { BlurSwap } from "@/components/ui/BlurSwap";
import { Button } from "@/components/ui/Button";
import { IconButton } from "@/components/ui/IconButton";
import { SwitchControl } from "@/components/ui/SwitchControl";

export function ServerRow({
  server,
  onEdit,
  onChanged,
}: {
  server: MCPServer;
  onEdit: () => void;
  onChanged: () => Promise<void>;
}) {
  const config = useStore((s) => s.config);
  const { busy, error, run } = useMutationState();

  const onToggle = (next: boolean) =>
    void run(async () => {
      await toggleMCPServerApi(config, server.name, next);
      await onChanged();
    });

  const onAuthenticate = () =>
    void run(async () => {
      await startMCPOAuthApi(config, server.name);
      await onChanged();
    });

  const needsAuth = server.auth === "oauth" && !server.connected;
  const subtitleParts: string[] = [];
  subtitleParts.push(server.transport.toUpperCase());
  if (!server.enabled) subtitleParts.push("disabled");
  else if (server.connected) subtitleParts.push(`${server.tool_count} tool${server.tool_count === 1 ? "" : "s"}`);
  else if (needsAuth) subtitleParts.push(server.error ? "tokens expired" : "sign in needed");
  else if (server.error) subtitleParts.push("error");
  else subtitleParts.push("disconnected");
  const state = !server.enabled
    ? { label: "Disabled", tone: "neutral", detail: "This server is disabled." }
    : server.connected
      ? { label: "Ready", tone: "ok", detail: "Connected and enabled." }
      : needsAuth
        ? { label: "Auth required", tone: "warn", detail: "OAuth authentication is required before discovery." }
        : server.error
          ? { label: "Error", tone: "bad", detail: server.error }
          : { label: "Offline", tone: "neutral", detail: "The server is not connected." };
  const rowCopy = error ?? (server.error && !needsAuth ? server.error : state.detail);

  return (
    <li className="settings-data-row settings-mcp-row">
      <div className="settings-data-row-main">
        <div className="settings-data-row-title">
          <span className="truncate">{server.name}</span>
          <span className="settings-mcp-status" data-tone={state.tone}>{state.label}</span>
        </div>
        <div className="settings-data-row-sub">{subtitleParts.join(" · ")}</div>
      </div>
      <div className={error || state.tone === "bad" ? "settings-data-row-copy settings-mcp-error" : "settings-data-row-copy"}>
        {rowCopy}
      </div>
      <div className="settings-data-row-end">
        {needsAuth && (
          <Button
            variant="secondary"
            size="sm"
            onClick={onAuthenticate}
            disabled={busy}
          >
            <BlurSwap swapKey={busy ? "busy" : server.error ? "reauth" : "signin"} blur={2}>
              {busy ? "…" : server.error ? "Re-authenticate" : "Sign in"}
            </BlurSwap>
          </Button>
        )}
        <IconButton onClick={onEdit} aria-label="Configure">
          <SettingsIcon size={ICON.MD} />
        </IconButton>
        <SwitchControl
          size="sm"
          checked={server.enabled}
          onChange={onToggle}
          disabled={busy}
          aria-label={server.enabled ? `Disable ${server.name}` : `Enable ${server.name}`}
        />
      </div>
    </li>
  );
}
