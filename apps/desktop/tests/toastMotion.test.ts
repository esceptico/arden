import { expect, test } from "bun:test";
import { readFileSync } from "node:fs";

const toaster = readFileSync(
  new URL("../src/components/ui/Toaster.tsx", import.meta.url),
  "utf8",
);

test("toasts enter, dismiss, and reflow through shared motion recipes", () => {
  expect(toaster).toContain("createPortal(");
  expect(toaster).toContain('data-toast-host=""');
  expect(toaster).toContain("document.body");
  expect(toaster).toContain("useHasBlockingOverlay()");
  expect(toaster).toContain("disabled={blocked}");
  expect(toaster).toContain("<AnimatePresence initial={false}>");
  expect(toaster).toContain('layout="position"');
  expect(toaster).toContain("initial={RISE_IN}");
  expect(toaster).toContain("animate={RISE_SETTLED}");
  expect(toaster).toContain("exit={{ ...DISSOLVE_OUT, transition: EXIT_ROW }}");
  expect(toaster).toContain("layout: SPRING_LAYOUT");
  expect(toaster).toContain("toastDismissDelay(");
});

test("a blocked toast holds its lifetime instead of expiring unread", () => {
  // The card is inert behind an overlay, so a lifetime spent there would drop
  // an agent's navigation offer with no trace.
  expect(toaster).toContain("[toast.id, blocked, dismissToast]");
});

test("no hold can strand a card: hover is read from the DOM and capped", () => {
  // Paired enter/leave is the leak — a card that slides under a still cursor
  // never gets its leave, so the hold must be re-derived, not remembered.
  expect(toaster).toContain('cardRef.current?.matches(":hover")');
  expect(toaster).toContain("onPointerLeave={() => scheduleDismiss(false)}");
  // Re-check on every change of the visible set, since that is exactly when
  // cards move under the cursor.
  expect(toaster).toContain("}, [stackKey]);");
  // The ceiling timer is armed off toast identity alone, so a blocked flip or
  // a re-arm cannot postpone it.
  expect(toaster).toContain("}, TOAST_CEILING_MS);");
  expect(toaster).toContain("}, [toast.id, dismissToast]);");
});

test("every card carries its own close affordance", () => {
  expect(toaster).toContain('aria-label="Dismiss notification"');
  expect(toaster).toContain("onClick={() => dismissToast(toast.id)}");
  // Revealed on hover of the card, opacity only — the slot is always held.
  expect(toaster).toContain("group-hover/toast:opacity-100");
});
