import { useEffect, useRef } from "react";
import { createPortal } from "react-dom";
import { AnimatePresence, motion } from "motion/react";
import { useStore } from "@/stores";
import { switchSession } from "@/actions/sessions";
import { applyAppDestination } from "@/actions/navigation";
import { useHasBlockingOverlay } from "@/lib/overlayStack";
import {
  DISSOLVE_OUT,
  EASE_OUT,
  EXIT_ROW,
  MOTION,
  RISE_IN,
  RISE_SETTLED,
  SPRING_LAYOUT,
} from "@/lib/tokens/motion";
import type { Toast, ToastStatus } from "@/lib/taskToast";

const TOAST_TONE: Record<ToastStatus, string> = {
  completed: "success",
  failed: "danger",
  cancelled: "warning",
  info: "info",
};

/* The status slot reads as a word, not an enum: "info" is a severity, and an
   agent's navigation offer is a suggestion the user can take or ignore. */
const TOAST_STATUS_LABEL: Record<ToastStatus, string> = {
  completed: "completed",
  failed: "failed",
  cancelled: "cancelled",
  info: "suggested",
};

export function Toaster() {
  const toasts = useStore((s) => s.toasts);
  const blocked = useHasBlockingOverlay();
  if (typeof document === "undefined") return null;
  return createPortal(
    <div
      role="status"
      aria-live="polite"
      data-toast-host=""
      data-modal-blocked={blocked ? "true" : "false"}
      className="fixed right-4 bottom-4 z-[var(--z-toast)] flex w-[min(340px,calc(100vw-32px))] flex-col gap-2.5 pointer-events-none"
    >
      <AnimatePresence initial={false}>
        {toasts.slice(-3).map((toast) => <ToastCard key={toast.id} toast={toast} blocked={blocked} />)}
      </AnimatePresence>
    </div>,
    document.body,
  );
}

function ToastCard({ toast, blocked }: { toast: Toast; blocked: boolean }) {
  const dismissToast = useStore((s) => s.dismissToast);
  const pushToast = useStore((s) => s.pushToast);
  const openAutomations = useStore((s) => s.openAutomations);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const armDismiss = () => {
    if (timerRef.current) clearTimeout(timerRef.current);
    // A blocking overlay makes the card inert, so a lifetime spent behind one
    // would drop the toast unread — hold it until the overlay closes.
    if (blocked) return;
    timerRef.current = setTimeout(
      () => dismissToast(toast.id),
      MOTION.toastLifetime * 1_000,
    );
  };

  useEffect(() => {
    armDismiss();
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, [toast.id, blocked, dismissToast]);

  /* Hovering holds the toast while it's being read; leaving re-arms the
     full lifetime so there's always time to act after reading. */
  function onMouseEnter() {
    if (timerRef.current) clearTimeout(timerRef.current);
  }

  function onClick() {
    if (timerRef.current) clearTimeout(timerRef.current);
    const target = toast.target;
    if (target.kind === "session") void switchSession(target.sessionId);
    else if (target.kind === "automation") openAutomations(target.taskId, { run: "latest" });
    else {
      const result = applyAppDestination(target.destination);
      // The destination can go away between the offer and the click. Say why
      // instead of letting the card vanish as if it had worked.
      if (!result.ok) {
        dismissToast(toast.id);
        pushToast({
          id: `${toast.id}:failed`,
          title: toast.title,
          detail: result.error,
          status: "failed",
          target,
        });
        return;
      }
    }
    dismissToast(toast.id);
  }

  return (
    <motion.button
      layout="position"
      initial={RISE_IN}
      animate={RISE_SETTLED}
      exit={{ ...DISSOLVE_OUT, transition: EXIT_ROW }}
      transition={{
        duration: MOTION.popover,
        ease: EASE_OUT,
        layout: SPRING_LAYOUT,
      }}
      type="button"
      disabled={blocked}
      onMouseEnter={onMouseEnter}
      onMouseLeave={armDismiss}
      onClick={onClick}
      data-tone={TOAST_TONE[toast.status]}
      className="arden-toast"
    >
      <span className="arden-toast__head">
        <strong className="arden-toast__title">{toast.title}</strong>
        <span className="arden-toast__status">{TOAST_STATUS_LABEL[toast.status]}</span>
      </span>
      {toast.detail && <span className="arden-toast__description">{toast.detail}</span>}
    </motion.button>
  );
}
