import { X } from "@/components/icons";
import { ICON } from "@/lib/icons";
import { IconButton } from "@/components/ui/IconButton";
import { GoogleSetupAssistant } from "@/features/settings/components/setup/GoogleSetupAssistant";
import { SlackSetupAssistant } from "@/features/settings/components/setup/SlackSetupAssistant";
import { MCPSetupAssistant } from "@/features/settings/components/setup/MCPSetupAssistant";

export type SetupAssistantKind = "google" | "slack" | "mcp";

export function SetupAssistant({
  kind,
  onClose,
  onDone,
}: {
  kind: SetupAssistantKind;
  onClose: () => void;
  onDone: () => Promise<void> | void;
}) {
  const title = kind === "google" ? "Google setup assistant" : kind === "slack" ? "Slack setup assistant" : "MCP setup assistant";
  return (
    <section className="surface-panel grid gap-4 p-4 border border-line-soft">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="m-0 text-lg font-semibold tracking-[-0.012em] text-ink">{title}</h3>
          <p className="m-0 mt-1 text-sm text-muted">Step-by-step setup using the server-owned integration APIs.</p>
        </div>
        <IconButton size="lg" onClick={onClose} aria-label="Close setup assistant">
          <X size={ICON.SM} strokeWidth={2} />
        </IconButton>
      </div>
      {kind === "google" ? (
        <GoogleSetupAssistant onDone={onDone} />
      ) : kind === "slack" ? (
        <SlackSetupAssistant onDone={onDone} />
      ) : (
        <MCPSetupAssistant onDone={onDone} />
      )}
    </section>
  );
}
