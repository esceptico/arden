import { Settings as SettingsIcon } from "@/components/icons";
import { useStore } from "@/stores";
import { type MCPServer, startMCPOAuthApi, toggleMCPServerApi } from "@/api/settings";
import { useMutationState } from "@/lib/hooks";
import { ICON } from "@/lib/icons";
import { BlurSwap } from "@/components/ui/BlurSwap";
import { Button } from "@/components/ui/Button";
import { IconButton } from "@/components/ui/IconButton";
import { StatusDot } from "@/components/ui/StatusDot";
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
  if (server.connected) subtitleParts.push(`${server.tool_count} tool${server.tool_count === 1 ? "" : "s"}`);
  else if (!server.enabled) subtitleParts.push("disabled");
  else if (needsAuth) subtitleParts.push(server.error ? "tokens expired" : "sign in needed");
  else if (server.error) subtitleParts.push("error");
  else subtitleParts.push("disconnected");

  return (
    <li className="flex min-w-0 items-center gap-2 px-3.5 py-2.5">
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <StatusDot tone={server.connected ? "ok" : server.error ? "bad" : "neutral"} />
          <span className="text-base font-medium text-ink tracking-[-0.005em] truncate">
            {server.name}
          </span>
        </div>
        <div className="mt-0.5 ml-3.5 text-xs text-muted tabular-nums">
          {subtitleParts.join(" · ")}
        </div>
        {(error || (server.error && !needsAuth)) && (
          <div
            className="mt-1 ml-3.5 text-xs text-bad truncate"
            title={error ?? server.error ?? ""}
          >
            {error ?? server.error}
          </div>
        )}
      </div>
      <div className="flex shrink-0 items-center gap-1">
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
          <SettingsIcon size={ICON.MD} strokeWidth={2} />
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
