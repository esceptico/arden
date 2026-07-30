import { expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import {
  EASE_LINEAR,
  DISCLOSURE,
  MOTION,
  POPOVER_ENTER_TRANSITION,
  POSE_PORTALED_POPOVER_IN,
  POSE_PORTALED_POPOVER_VISIBLE,
  SHEET_CLEANUP_ENTER_TRANSITION,
  SHEET_CLEANUP_LINEAR_CSS,
} from "../src/lib/tokens/motion";

const read = (path: string) => readFileSync(new URL(path, import.meta.url), "utf8");

test("shared control motion has one semantic owner", () => {
  expect(MOTION.indicator).toBe(0.1);
  expect(MOTION.iconSwap).toBe(0.25);
  expect(MOTION.success).toBe(0.3);
  expect(MOTION.spinner).toBe(0.7);
  expect(MOTION.skeletonPulse).toBe(1);
  expect(MOTION.skeletonReveal).toBe(0.4);
  expect(MOTION.toolCallCadence).toBe(1.6);
  expect(MOTION.toastLifetime).toBe(7);
  expect(POPOVER_ENTER_TRANSITION).toEqual({
    duration: MOTION.popover,
    ease: [0.22, 1, 0.36, 1],
  });
  expect(POSE_PORTALED_POPOVER_IN).toEqual({
    opacity: 0,
    y: 5,
    scale: 0.985,
    filter: "blur(2px)",
  });
  expect(POSE_PORTALED_POPOVER_VISIBLE.transitionEnd).toEqual({ filter: "none" });
  expect(SHEET_CLEANUP_ENTER_TRANSITION.duration).toBe(0.26);
  expect(SHEET_CLEANUP_ENTER_TRANSITION.ease(64 / 260)).toBeCloseTo(0.415, 2);
  expect(SHEET_CLEANUP_LINEAR_CSS).toStartWith("linear(0.00000, 0.01595");
  expect(EASE_LINEAR).toBe("linear");
});

test("shared primitives consume the motion contract rather than local values", () => {
  const theme = read("../src/components/ui/ThemeToggle.tsx");
  const slider = read("../src/components/ui/Slider.tsx");
  const foundation = read("../src/design/foundation.css");
  const confirmDelete = read("../src/components/ui/ConfirmDeleteButton.tsx");
  const radio = read("../src/components/ui/RadioGroup.tsx");
  const anchoredPopover = read("../src/components/ui/AnchoredPopover.tsx");
  const pageModal = read("../src/components/ui/PageModal.tsx");
  const mermaid = read("../src/components/ui/Mermaid.tsx");

  expect(theme).toContain("<IconSwap");
  expect(theme).toContain("Moon02");
  expect(theme).toContain("Sun");
  expect(theme).toContain('size="md"');
  expect(theme).toContain("Use dark appearance");
  expect(theme).toContain("Use light appearance");
  expect(theme).not.toContain("motion.");
  expect(slider).not.toContain("motion/react");
  expect(slider).not.toContain("SPRING_SLIDER");
  expect(foundation).toContain("--slider-fill-x var(--motion-tab-shift) var(--smooth-out)");
  expect(foundation).toContain("--slider-marker-x var(--motion-tab-shift) var(--smooth-out)");
  expect(confirmDelete).toContain("EASE_LINEAR");
  expect(radio).toContain("duration-indicator");
  expect(anchoredPopover).toContain("useHasBlockingOverlay");
  expect(anchoredPopover).toContain("POSE_PORTALED_POPOVER_IN");
  expect(anchoredPopover).toContain('data-overlay-depth={nested ? "nested" : "page"}');
  expect(pageModal).toContain("useRetainedPresence");
  expect(pageModal).toContain("surface-sheet");
  expect(pageModal).toContain('data-overlay-state={open ? "open" : "closing"}');
  expect(pageModal).toContain('inert={open ? undefined : true}');
  expect(mermaid).toContain("<PageModal");
  expect(mermaid).toContain('placement="bottom-sheet"');
  expect(mermaid).toContain("bottomSheetFill");
  expect(mermaid).toContain("Workflow diagram");
  expect(mermaid).toContain('aria-label="Close Mermaid viewer"');
  expect(mermaid).toContain('className="flex min-h-12');
  expect(mermaid).not.toContain("fixed inset-0 z-");
});

test("ambient details float in the shared peek surface", () => {
  const workBrief = read("../src/features/home/components/WorkBrief.tsx");

  expect(DISCLOSURE).toEqual({
    panel: { duration: 0.18, ease: [0.22, 1, 0.36, 1] },
    close: { duration: 0.18, ease: [0.22, 1, 0.36, 1] },
    followers: { duration: 0.18, ease: [0.22, 1, 0.36, 1] },
    poseIn: { opacity: 0, y: 5, scale: 0.99, filter: "blur(2px)" },
    poseVisible: { opacity: 1, y: 0, scale: 1, filter: "blur(0px)" },
    poseOut: { opacity: 0, y: 5, scale: 0.99, filter: "blur(2px)" },
  });
  expect(workBrief).toContain("<PeekSurface");
  expect(workBrief).toContain('motionPreset="peek"');
  expect(workBrief).toContain("closeOnOutsidePointerDown");
  expect(workBrief).toContain('outsidePointerDownIgnoreSelector="[data-strip-trigger]"');
  expect(workBrief).toContain("transition={DISCLOSURE.followers}");
  expect(workBrief).not.toContain("InlinePopover");
});

test("the CSS motion mirror owns shared cadence and clears filter layers", () => {
  const tokens = read("../src/design/tokens.css");
  const styles = read("../src/styles.css");

  expect(tokens).toContain("--motion-indicator: 100ms;");
  expect(tokens).toContain("--motion-icon-swap: 250ms;");
  expect(tokens).toContain("--motion-success: 300ms;");
  expect(tokens).toContain("--motion-spinner: 700ms;");
  expect(tokens).toContain("--motion-skeleton-pulse: 1000ms;");
  expect(tokens).toContain("--motion-skeleton-reveal: 400ms;");
  expect(tokens).toContain("--motion-shimmer: 1800ms;");
  expect(tokens).toContain("--z-popover: 30;");
  expect(tokens).toContain("--z-nested: 70;");
  expect(tokens).toContain("--shadow-8: inset 0 1px 0 0 var(--dm-hi-peak)");
  expect(tokens).not.toContain("--shadow-8: var(--shadow-5);");
  expect(tokens).toContain("--duration-indicator: var(--motion-indicator);");
  expect(styles).not.toContain("skeleton-shimmer");
  expect(styles).toContain(".surface-sheet {");
  expect(styles).toContain(".surface-popover[data-overlay-depth=\"nested\"]");
  expect(styles).toContain("background: var(--surface-7);");
  expect(styles).toContain("transform: translateY(var(--motion-distance-entry));");
});
