import { type ReactNode } from "react";
import { AnimatePresence, motion } from "motion/react";
import { CheckCircle2, ExternalLink, KeyRound, Loader2 } from "@/components/icons";
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
    <div className="rounded-[12px] border border-line-soft bg-surface overflow-hidden">
      <div className="flex flex-wrap items-start gap-3 px-3.5 py-3">
        <div className="min-w-[150px] flex-1 grid gap-1">
          <div className="flex items-center gap-2 min-w-0">
            <ProviderIcon connected={provider.connected} />
            <div className="text-base font-medium text-ink truncate">{provider.name}</div>
          </div>
          <div className="text-xs text-muted font-mono truncate">
            {provider.connected
              ? `${providerModelCountLabel(provider)}${connectionPill ? ` · ${connectionPill}` : ""}`
              : providerDescription(provider.id)}
          </div>
          {!provider.connected && (
            <div className="text-xs text-muted font-mono truncate">
              {providerModelCountLabel(provider)}
            </div>
          )}
        </div>

        <div className="ml-auto flex flex-wrap items-center justify-end gap-2">
          {readOnlyPrimary ? (
            <span className="inline-flex items-center h-8 px-3 rounded-md border border-line-soft bg-surface-soft text-sm font-medium text-muted">
              {isCustom ? "Configured separately" : actionLabel}
            </span>
          ) : (
            <Button
              variant={provider.connected ? "secondary" : "primary"}
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

      <AnimatePresence initial={false}>
        {editing && !provider.connected && !isOauth && !isCustom && (
          <SecretConnectEditor
            motionKey="key-editor"
            value={apiKey}
            label="API key"
            pending={pending}
            spinner={<Loader2 size={ICON.MD} strokeWidth={2} className="animate-spin" />}
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
            <Loader2 size={ICON.MD} strokeWidth={2} className="animate-spin" />
            <span>Waiting for browser sign-in…</span>
            {codexStatus.url && (
              <a
                href={codexStatus.url}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1 text-info hover:underline underline-offset-2"
              >
                Open URL <ExternalLink size={ICON.XS} strokeWidth={2} />
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
  );
}

function ProviderIcon({ connected }: { connected: boolean }) {
  return connected ? (
    <CheckCircle2 size={ICON.MD} strokeWidth={2} className="text-ok shrink-0" />
  ) : (
    <KeyRound size={ICON.MD} strokeWidth={2} className="text-faint shrink-0" />
  );
}
