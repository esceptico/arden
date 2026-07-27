import { useEffect, useRef } from "react";
import { createPortal } from "react-dom";
import { AnimatePresence, motion } from "motion/react";
import { useStore } from "@/stores";
import { switchSession } from "@/actions/sessions";
import { applyAppDestination } from "@/actions/navigation";
import { useHasBlockingOverlay } from "@/lib/overlayStack";
import { IconButton } from "@/components/ui/IconButton";
import { X } from "@/components/icons";
import { ICON } from "@/lib/icons";
import {
  DISSOLVE_OUT,
  EASE_OUT,
  EXIT_ROW,
  MOTION,
  RISE_IN,
  RISE_SETTLED,
  SPRING_LAYOUT,
} from "@/lib/tokens/motion";
import { TOAST_CEILING_MS, toastDismissDelay } from "@/lib/taskToast";
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

const TOAST_VISIBLE = 3;

export function Toaster() {
  const toasts = useStore((s) => s.toasts);
  const blocked = useHasBlockingOverlay();
  if (typeof document === "undefined") return null;
  const visible = toasts.slice(-TOAST_VISIBLE);
  // Cards shift whenever the set changes, and a shift under a still cursor
  // fires no mouseleave — hand every card a reason to re-check its timer.
  const stackKey = visible.map((toast) => toast.id).join("|");
  return createPortal(
    <div
      role="status"
      aria-live="polite"
      data-toast-host=""
      data-modal-blocked={blocked ? "true" : "false"}
      className="fixed right-4 bottom-4 z-[var(--z-toast)] flex w-[min(340px,calc(100vw-32px))] flex-col gap-2.5 pointer-events-none"
    >
      <AnimatePresence initial={false}>
        {visible.map((toast) => (
          <ToastCard key={toast.id} toast={toast} blocked={blocked} stackKey={stackKey} />
        ))}
      </AnimatePresence>
    </div>,
    document.body,
  );
}

function ToastCard(
  { toast, blocked, stackKey }: { toast: Toast; blocked: boolean; stackKey: string },
) {
  const dismissToast = useStore((s) => s.dismissToast);
  const pushToast = useStore((s) => s.pushToast);
  const openAutomations = useStore((s) => s.openAutomations);
  const cardRef = useRef<HTMLDivElement | null>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const expiredRef = useRef(false);

  // Ask the DOM who is hovered rather than trusting paired enter/leave events.
  const isHovered = () => cardRef.current?.matches(":hover") ?? false;

  const clearTimer = () => {
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = null;
  };

  const scheduleDismiss = (hovered: boolean) => {
    clearTimer();
    const delay = toastDismissDelay({ hovered, expired: expiredRef.current, blocked });
    if (delay === null) return;
    timerRef.current = setTimeout(() => dismissToast(toast.id), delay);
  };

  const armDismiss = () => scheduleDismiss(isHovered());

  useEffect(() => {
    armDismiss();
    return clearTimer;
  }, [toast.id, blocked, dismissToast]);

  // Independent of every hold, so blocked flips and re-arms cannot restart it.
  useEffect(() => {
    const ceiling = setTimeout(() => {
      expiredRef.current = true;
      if (!isHovered()) dismissToast(toast.id);
    }, TOAST_CEILING_MS);
    return () => clearTimeout(ceiling);
  }, [toast.id, dismissToast]);

  // A card whose timer was cleared by a hover it never left is stuck; the next
  // stack change re-checks the real hover state and restarts the countdown.
  useEffect(() => {
    if (timerRef.current) return;
    armDismiss();
  }, [stackKey]);

  function onClick() {
    clearTimer();
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
    <motion.div
      ref={cardRef}
      layout="position"
      initial={RISE_IN}
      animate={RISE_SETTLED}
      exit={{ ...DISSOLVE_OUT, transition: EXIT_ROW }}
      transition={{
        duration: MOTION.popover,
        ease: EASE_OUT,
        layout: SPRING_LAYOUT,
      }}
      /* Hovering holds the toast while it's being read; leaving re-arms the
         full lifetime so there's always time to act after reading. The leave
         asserts `false` rather than re-reading :hover — the pointer is
         demonstrably gone, and a stale read here is what strands a card. */
      onPointerEnter={clearTimer}
      onPointerLeave={() => scheduleDismiss(false)}
      data-tone={TOAST_TONE[toast.status]}
      className="arden-toast group/toast"
    >
      <button
        type="button"
        disabled={blocked}
        onClick={onClick}
        className="arden-toast__body"
      >
        <span className="arden-toast__head">
          <strong className="arden-toast__title">{toast.title}</strong>
          <span className="arden-toast__status">{TOAST_STATUS_LABEL[toast.status]}</span>
        </span>
        {toast.detail && <span className="arden-toast__description">{toast.detail}</span>}
      </button>
      <IconButton
        size="xs"
        tone="faint"
        aria-label="Dismiss notification"
        onClick={() => dismissToast(toast.id)}
        className="arden-toast__close opacity-0 transition-opacity duration-check ease-out group-hover/toast:opacity-100 focus-visible:opacity-100"
      >
        <X size={ICON.XS} />
      </IconButton>
    </motion.div>
  );
}
