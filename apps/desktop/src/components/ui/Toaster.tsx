import { useEffect, useRef } from "react";
import { AnimatePresence, motion } from "motion/react";
import { Check, Slash, X } from "@/components/icons";
import { useStore } from "@/stores";
import { switchSession } from "@/actions/sessions";
import {
  EASE_DECELERATE,
  MOTION,
  SPRING_LAYOUT,
  SPRING_TAP,
  originFromEvent,
} from "@/lib/tokens/motion";
import { ICON } from "@/lib/icons";
import type { Toast } from "@/lib/taskToast";

const DISMISS_MS = 5000;
const STATUS_ICON = { completed: Check, failed: X, cancelled: Slash } as const;

export function Toaster() {
  const toasts = useStore((s) => s.toasts);
  return (
    <div
      role="status"
      aria-live="polite"
      className="fixed top-3 right-3 z-[var(--z-toast)] flex w-[min(360px,calc(100vw-24px))] flex-col gap-2 pointer-events-none"
    >
      <AnimatePresence initial={false}>
        {toasts.map((toast) => (
          <ToastCard key={toast.id} toast={toast} />
        ))}
      </AnimatePresence>
    </div>
  );
}

function ToastCard({ toast }: { toast: Toast }) {
  const dismissToast = useStore((s) => s.dismissToast);
  const openAutomations = useStore((s) => s.openAutomations);
  const Icon = STATUS_ICON[toast.status];
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    timerRef.current = setTimeout(() => dismissToast(toast.id), DISMISS_MS);
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, [toast.id, dismissToast]);

  function onClick(e: React.MouseEvent<HTMLButtonElement>) {
    if (timerRef.current) clearTimeout(timerRef.current);
    if (toast.target.kind === "session") void switchSession(toast.target.sessionId);
    else openAutomations(originFromEvent(e.currentTarget));
    dismissToast(toast.id);
  }

  return (
    <motion.button
      type="button"
      layout
      onClick={onClick}
      initial={{ opacity: 0, y: -8, scale: 0.97 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      exit={{ opacity: 0, y: -8, scale: 0.97 }}
      whileTap={{ scale: 0.97 }}
      transition={{
        layout: SPRING_LAYOUT,
        scale: SPRING_TAP,
        duration: MOTION.panel,
        ease: EASE_DECELERATE,
      }}
      data-toast-status={toast.status}
      className="surface-panel surface-radius-md pointer-events-auto flex w-full items-start gap-2.5 px-3.5 py-3 text-left"
    >
      <span className="mt-0.5 grid h-4 w-4 shrink-0 place-items-center text-ink-soft">
        <Icon size={ICON.SM} strokeWidth={2} />
      </span>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-sm font-medium text-ink">{toast.title}</span>
        {toast.detail && (
          <span className="block truncate text-2xs text-muted">{toast.detail}</span>
        )}
      </span>
    </motion.button>
  );
}
