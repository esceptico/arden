import { createPortal } from "react-dom";
import { Button } from "@/components/ui/Button";
import { IconButton } from "@/components/ui/IconButton";
import { PeekSurface } from "@/components/workspace/PeekSurface";
import { Markdown } from "@/components/ui/Markdown";
import { X } from "@/components/icons";
import { ICON } from "@/lib/icons";

/**
 * The task a review ask will run, read in full.
 *
 * It used to sit inline in the request card. A custodian writes these for an
 * agent, not for a person — they run to a couple of thousand characters — so
 * the card grew past the room and buried the controls under it.
 *
 * Approval is what grants this task its capability, so the text stays one click
 * away rather than being summarised out of reach, and Approve lives in here too
 * so the reading and the decision are the same gesture.
 */
export function AskActionPeek({
  action,
  title,
  open,
  busy,
  onApprove,
  onClose,
}: {
  action: string;
  title: string;
  open: boolean;
  busy: boolean;
  onApprove: () => void;
  onClose: () => void;
}) {
  // Portaled to the body like every other peek that opens from inside animated
  // content: `position: fixed` resolves against the nearest transformed
  // ancestor, and both call sites sit under motion transforms.
  return createPortal(
    <PeekSurface
      open={open}
      onClose={onClose}
      ariaLabel={`What approving runs: ${title}`}
      className="arden-peek surface-peek"
      closeOnOutsidePointerDown
      outsidePointerDownIgnoreSelector="[data-ask-action-owner]"
    >
      <header className="arden-peek-rule-below">
        <span>
          <b>Approving runs this</b>
          <small>{title}</small>
        </span>
        <IconButton aria-label="Close" onClick={onClose} size="sm">
          <X size={ICON.SM} aria-hidden />
        </IconButton>
      </header>

      <div className="arden-peek__body scroll-fade" tabIndex={0}>
        {/* Custodians write these with backticked paths and numbered steps, so
          * they read as Markdown like every other peek in the app. */}
        <Markdown content={action} codeChrome={false} />
      </div>

      <footer className="arden-peek__foot arden-peek-rule-above">
        <Button variant="primary" size="sm" disabled={busy} onClick={onApprove}>
          Approve
        </Button>
        <Button variant="quiet" size="sm" disabled={busy} onClick={onClose}>
          Close
        </Button>
      </footer>
    </PeekSurface>,
    document.body,
  );
}
