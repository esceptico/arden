import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { X } from "@/components/icons";
import { ICON } from "@/lib/icons";
import { IconButton } from "@/components/ui/IconButton";
import { Button } from "@/components/ui/Button";
import { PeekSurface } from "@/components/workspace/PeekSurface";

export type SetupAssistantKind = "slack" | "mcp";

type SetupCopy = {
  title: string;
  kicker: string;
  heading: string;
  description: string;
  fieldLabel: string;
  fieldPlaceholder: string;
  primaryLabel: string;
  capability?: readonly [string, string, string, string];
  beforeEnabling?: string;
};

const SETUP_COPY: Record<SetupAssistantKind, SetupCopy> = {
  slack: {
    title: "Connect Slack",
    kicker: "INTEGRATION SETUP",
    heading: "Slack workspace",
    description: "Connect token-backed Slack tools. Choose workspace scope before making them available to the agent.",
    fieldLabel: "Bot token",
    fieldPlaceholder: "Required",
    primaryLabel: "Save connection",
    capability: [
      "Read scope",
      "Channels + messages",
      "Write scope",
      "Post message — always ask",
    ],
  },
  mcp: {
    title: "Guided MCP setup",
    kicker: "GUIDED SETUP",
    heading: "Connect an MCP server",
    description: "Paste a server URL, package name, or command. Arden carries it into the server form for review; nothing is connected yet.",
    fieldLabel: "Server or package",
    fieldPlaceholder: "URL, package name, or command",
    primaryLabel: "Continue to server form",
    beforeEnabling: "Review transport, authentication, discovered tools, and each approval policy.",
  },
};

export function SetupAssistant({
  kind,
  open,
  onClose,
  onDone,
  onPrimary,
}: {
  kind: SetupAssistantKind | null;
  open: boolean;
  onClose: () => void;
  onDone: () => Promise<void> | void;
  /** Lets the owning Settings surface keep mutations in its canonical flow. */
  onPrimary?: (kind: SetupAssistantKind, value: string) => Promise<void> | void;
}) {
  const retainedKind = useRef<SetupAssistantKind | null>(kind);
  if (kind) retainedKind.current = kind;
  const visibleKind = retainedKind.current;
  const copy = visibleKind ? SETUP_COPY[visibleKind] : null;
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setValue("");
    setBusy(false);
    setError(null);
  }, [open, visibleKind]);

  if (!visibleKind || !copy) return null;
  const activeKind = visibleKind;

  async function handlePrimary() {
    setBusy(true);
    setError(null);
    try {
      if (onPrimary) await onPrimary(activeKind, value);
      else await onDone();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  }

  const isMcp = activeKind === "mcp";

  return createPortal(
    <PeekSurface
      open={open}
      onClose={onClose}
      ariaLabel={copy.title}
      layer="settings-setup"
      focusOnOpen
      focusSelector=".sheet-body input:not(:disabled), [data-peek-close]"
      motionPreset="peek"
      className="setup-sheet surface-sheet surface-radius-md"
    >
      <header className="sheet-head dp-sheet-header arden-peek-rule-below">
        <strong>{copy.title}</strong>
        <IconButton
          data-peek-close
          className="sheet-close"
          onClick={onClose}
          aria-label={`Close ${copy.title}`}
        >
          <X size={ICON.SM} />
        </IconButton>
      </header>
      <div className="sheet-body dp-sheet-body scroll-fade">
        <div className="sheet-kicker">{copy.kicker}</div>
        <h3>{copy.heading}</h3>
        <p>{copy.description}</p>

        <div className="sheet-step">
          {!isMcp && (
            <div className="sheet-step-label">
              <span className="step-num">1</span>
              {copy.fieldLabel}
            </div>
          )}
          <div className="sheet-field">
            <label htmlFor={`setup-${activeKind}-field`}>{copy.fieldLabel}</label>
            <input
              id={`setup-${activeKind}-field`}
              className="arden-field dp-field"
              value={value}
              onChange={(event) => setValue(event.target.value)}
              placeholder={copy.fieldPlaceholder}
              type={activeKind === "slack" ? "password" : "text"}
            />
          </div>
        </div>

        <div className="sheet-step">
          {!isMcp && (
            <div className="sheet-step-label">
              <span className="step-num">2</span>
              Review capability
            </div>
          )}
          <div className="scope-list">
            {isMcp ? (
              <><b>Before enabling</b><br />{copy.beforeEnabling}</>
            ) : (
              <>
                <b>{copy.capability![0]}</b><br />{copy.capability![1]}<br />
                <b>{copy.capability![2]}</b><br />{copy.capability![3]}
              </>
            )}
          </div>
        </div>

        {error && <p className="setup-sheet-error" role="alert">{error}</p>}
      </div>
      <footer className="sheet-actions dp-sheet-footer">
        <Button className="sheet-cancel" onClick={onClose}>Cancel</Button>
        <Button
          variant="primary"
          className="sheet-primary"
          onClick={() => void handlePrimary()}
          disabled={busy || (isMcp && value.trim().length === 0)}
          aria-busy={busy || undefined}
        >
          {copy.primaryLabel}
        </Button>
      </footer>
    </PeekSurface>,
    document.body,
  );
}
