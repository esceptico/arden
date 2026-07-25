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
  expect(toaster).toContain("MOTION.toastLifetime * 1_000");
});

test("a blocked toast holds its lifetime instead of expiring unread", () => {
  // The card is inert behind an overlay, so a lifetime spent there would drop
  // an agent's navigation offer with no trace.
  expect(toaster).toContain("if (blocked) return;");
  expect(toaster).toContain("[toast.id, blocked, dismissToast]");
});
