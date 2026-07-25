import { type ReactNode } from "react";
import { X } from "@/components/icons";
import { IconButton } from "@/components/ui/IconButton";
import { PageModal } from "@/components/ui/PageModal";
import { ICON } from "@/lib/icons";

type SystemSheetTone = "success" | "warning" | "danger" | "neutral";

export interface SystemSheetProps {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  ariaLabel: string;
  children: ReactNode;
  footer?: ReactNode;
  status?: ReactNode;
  statusTone?: SystemSheetTone;
  headerAction?: ReactNode;
  closeLabel?: string;
  bodyClassName?: string;
  elevated?: boolean;
}

/**
 * The shared shell for blocking review and viewer surfaces. Content remains
 * product-specific; geometry, material, header, footer, level, and dismissal
 * stay identical to the system-overlay mockup.
 */
export function SystemSheet({
  open,
  onClose,
  title,
  ariaLabel,
  children,
  footer,
  status,
  statusTone = "neutral",
  headerAction,
  closeLabel = "Close",
  bodyClassName = "",
  elevated = false,
}: SystemSheetProps) {
  return (
    <PageModal
      open={open}
      onClose={onClose}
      placement="bottom-sheet"
      elevated={elevated}
      grid={footer ? "grid-rows-[auto_minmax(0,1fr)_auto]" : "grid-rows-[auto_minmax(0,1fr)]"}
      ariaLabel={ariaLabel}
    >
      <header className="flex min-h-12 shrink-0 items-center gap-2.5 border-b border-line px-3 pl-4">
        <strong className="min-w-0 truncate text-base font-semibold text-ink">{title}</strong>
        {status && (
          <span
            data-tone={statusTone === "neutral" ? undefined : statusTone}
            className="arden-status ml-auto shrink-0"
            role="status"
            aria-live="polite"
          >
            {status}
          </span>
        )}
        <div className={`${status ? "" : "ml-auto "}flex shrink-0 items-center gap-1`}>
          {headerAction}
          <IconButton onClick={onClose} aria-label={closeLabel} title={closeLabel}>
            <X size={ICON.SM} />
          </IconButton>
        </div>
      </header>
      <div className={`min-h-0 min-w-0 overflow-y-auto scroll-thin scroll-fade px-4 py-4 ${bodyClassName}`}>
        {children}
      </div>
      {footer && (
        <footer className="flex min-h-[3.25rem] shrink-0 items-center justify-end gap-2 border-t border-line px-3">
          {footer}
        </footer>
      )}
    </PageModal>
  );
}
