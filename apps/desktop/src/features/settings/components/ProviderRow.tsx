import { type ReactNode } from "react";
import { AnimatePresence, motion } from "motion/react";
import { ExternalLink, Loader2 } from "@/components/icons";
import { type ModelProvider, type OpenAICodexOAuthStatus } from "@/api/settings";
import {
  providerActionLabel,
  providerConnectionPill,
  providerModelCountLabel,
} from "@/features/settings/lib/providerConnection";
import { DISSOLVE_OUT, EASE_OUT, MOTION, RISE_IN, RISE_SETTLED } from "@/lib/tokens/motion";
import { ICON } from "@/lib/icons";
import { BlurSwap } from "@/components/ui/BlurSwap";
import { Button } from "@/components/ui/Button";
import { Collapse } from "@/components/ui/Collapse";
import { SecretConnectEditor } from "@/features/settings/components/SecretConnectEditor";

function providerDescription(id: string): string {
  switch (id) {
    case "openai-codex":
      return "Use your OpenAI account login for Codex-backed models.";
    case "openai":
      return "Use OpenAI API keys for GPT models and embeddings.";
    case "anthropic":
      return "Use Anthropic API keys for Claude models.";
    case "google":
      return "Use Gemini API keys for Gemini chat and embeddings.";
    case "openrouter":
      return "Use OpenRouter API keys for routed third-party models.";
    case "custom":
      return "OpenAI-compatible local or hosted models.";
    default:
      return "Connect this model provider.";
  }
}

function providerSetupLabel(id: string): string {
  switch (id) {
    case "openai":
      return "GPT models and embeddings";
    case "anthropic":
      return "Claude models";
    case "google":
      return "Gemini chat and embeddings";
    case "openrouter":
      return "Routed third-party models";
    case "custom":
      return "OpenAI-compatible endpoints";
    default:
      return "Connect a provider";
  }
}

export function ProviderRow({
  provider,
  editing,
  apiKey,
  pending,
  codexStatus,
  customOpen,
  onEdit,
  onCancel,
  onKeyChange,
  onConnect,
  onDisconnect,
  onCodexSignIn,
  onToggleCustom,
  children,
}: {
  provider: ModelProvider;
  editing: boolean;
  apiKey: string;
  pending: boolean;
  codexStatus: OpenAICodexOAuthStatus | null;
  customOpen: boolean;
  onEdit: () => void;
  onCancel: () => void;
  onKeyChange: (value: string) => void;
  onConnect: () => void;
  onDisconnect: () => void;
  onCodexSignIn: () => void;
  onToggleCustom: () => void;
  children?: ReactNode;
}) {
  const isCustom = provider.id === "custom";
  const isOauth = provider.auth_type === "oauth";
  const actionLabel = isCustom ? (customOpen ? "Done" : "Manage") : pending ? "Working…" : providerActionLabel(provider);
  const readOnlyPrimary = provider.connected && provider.from_env;
  const connectionPill = providerConnectionPill(provider);
  const showingEditor = editing && !provider.connected && !isOauth && !isCustom;
  const hasDetail = showingEditor || codexStatus?.status === "pending" || !!codexStatus?.error || (isCustom && customOpen);

  function primaryAction() {
    if (isCustom) {
      onToggleCustom();
      return;
    }
    if (isOauth) {
      if (provider.connected) onDisconnect();
      else onCodexSignIn();
      return;
    }
    if (provider.connected) onDisconnect();
    else onEdit();
  }

  return (
    <div className="settings-data-shell">
      <div className="settings-data-row">
        <div className="settings-data-row-main">
          <div className="settings-data-row-title">
            {provider.connected && <span className="settings-provider-status">Connected</span>}
            <span className="truncate">{provider.name}</span>
          </div>
          <div className="settings-data-row-sub">
            {provider.connected
              ? `${providerModelCountLabel(provider)}${connectionPill ? ` · ${connectionPill}` : ""}`
              : providerSetupLabel(provider.id)}
          </div>
        </div>

        <div className="settings-data-row-copy">{providerDescription(provider.id)}</div>

        <div className="settings-data-row-end">
          {readOnlyPrimary ? (
            <span className="btn arden-button">
              {isCustom ? "Configured separately" : actionLabel}
            </span>
          ) : (
            <Button
              // Never an ink slab: several unconnected rows would stack
              // several primaries on one page (fill policy).
              onClick={primaryAction}
              disabled={pending}
            >
              <BlurSwap swapKey={actionLabel} blur={2}>
                {actionLabel}
              </BlurSwap>
            </Button>
          )}
        </div>
      </div>

      <Collapse open={hasDetail} mode="height">
        {/* Reveal gap + separator sit inside the measured content so the
            height spring carries them; the row's own 12px bottom padding
            completes the 20px gap above the rule. */}
        <div className="pt-2">
          <div className="settings-data-row-detail-body">
            <AnimatePresence initial={false}>
              {showingEditor && (
                <SecretConnectEditor
                  motionKey="key-editor"
                  value={apiKey}
                  label="API key"
                  pending={pending}
                  spinner={<Loader2 size={ICON.MD} className="animate-spin" />}
                  onChange={onKeyChange}
                  onConnect={onConnect}
                  onCancel={onCancel}
                />
              )}
            </AnimatePresence>

            <AnimatePresence initial={false}>
              {codexStatus?.status === "pending" && (
                <motion.div
                  key="codex-pending"
                  initial={{ ...RISE_IN, y: -4 }}
                  animate={RISE_SETTLED}
                  exit={{ ...DISSOLVE_OUT, transition: { duration: MOTION.fast, ease: EASE_OUT } }}
                  transition={{ duration: MOTION.row, ease: EASE_OUT }}
                  className="flex items-center gap-2 px-3.5 py-2.5 bg-surface-soft/35 text-sm text-muted"
                >
                  <Loader2 size={ICON.MD} className="animate-spin" />
                  <span>Waiting for browser sign-in…</span>
                  {codexStatus.url && (
                    <a
                      href={codexStatus.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex items-center gap-1 text-info hover:underline underline-offset-2"
                    >
                      Open URL <ExternalLink size={ICON.XS} />
                    </a>
                  )}
                </motion.div>
              )}
            </AnimatePresence>

            <AnimatePresence initial={false}>
              {codexStatus?.error && (
                <motion.div
                  key="codex-error"
                  initial={{ ...RISE_IN, y: -4 }}
                  animate={RISE_SETTLED}
                  exit={{ ...DISSOLVE_OUT, transition: { duration: MOTION.fast, ease: EASE_OUT } }}
                  transition={{ duration: MOTION.row, ease: EASE_OUT }}
                  className="px-3.5 py-2.5 bg-bad-soft text-sm text-bad"
                >
                  {codexStatus.error}
                </motion.div>
              )}
            </AnimatePresence>

            {children}
          </div>
        </div>
      </Collapse>
    </div>
  );
}
