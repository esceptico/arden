/**
 * Arden motion contract.
 *
 * This is the JS authority mirrored by design/tokens.css. Components choose a
 * semantic recipe; they do not invent durations, offsets, blur, or easing.
 */

// ─── Geist primary ease ─────────────────────────────────────

// ─── Springs ────────────────────────────────────────────────
// Damping is at or above the critical threshold for each stiffness/mass.

/** Floating inspector / peek. Interruptible and intentionally physical. */
export const SPRING_PEEK = { type: "spring", stiffness: 210, damping: 26, mass: 1 } as const;
/** Piecewise-linear equivalent of board-motion's 30-sample `linear(...)`
 * easing. Motion accepts a function (unlike CSS's `linear(...)` string), so
 * this preserves the mock curve exactly without an unsafe type cast. */
function sampledEase(samples: readonly number[]): (progress: number) => number {
  return (progress) => {
    const clamped = Math.min(1, Math.max(0, progress));
    const position = clamped * (samples.length - 1);
    const index = Math.min(samples.length - 2, Math.floor(position));
    const fraction = position - index;
    return samples[index] + (samples[index + 1] - samples[index]) * fraction;
  };
}

const PEEK_ENTRY_SAMPLES = [
  0, 0.02075, 0.07297, 0.14445, 0.22618, 0.31168, 0.3964, 0.47728,
  0.55241, 0.62072, 0.68175, 0.73548, 0.78216, 0.82226, 0.85634, 0.88501,
  0.9089, 0.92863, 0.94476, 0.95784, 0.96834, 0.97668, 0.98324, 0.98833,
  0.99223, 0.99518, 0.99736, 0.99894, 1.00005, 1.0008, 1,
] as const;
export const PEEK_ENTRY_LINEAR_CSS =
  "linear(0.00000, 0.02075, 0.07297, 0.14445, 0.22618, 0.31168, 0.39640, 0.47728, 0.55241, 0.62072, 0.68175, 0.73548, 0.78216, 0.82226, 0.85634, 0.88501, 0.90890, 0.92863, 0.94476, 0.95784, 0.96834, 0.97668, 0.98324, 0.98833, 0.99223, 0.99518, 0.99736, 0.99894, 1.00005, 1.00080, 1.00000)";
export const PEEK_ENTRY_EASING = sampledEase(PEEK_ENTRY_SAMPLES);
/** Sheets and full-window surfaces. */
export const SPRING_SHEET = { type: "spring", stiffness: 360, damping: 32, mass: 0.75 } as const;
const SHEET_ENTRY_SAMPLES = [
  0, 0.04373, 0.14239, 0.26222, 0.38373, 0.49652, 0.5958, 0.68011,
  0.74985, 0.8064, 0.85153, 0.88706, 0.91472, 0.93605, 0.95236, 0.96472,
  0.97403, 0.98099, 0.98616, 0.98998, 0.99279, 0.99484, 0.99633, 0.99741,
  0.99818, 0.99873, 0.99912, 0.9994, 0.99959, 0.99972, 1,
] as const;
/** Deterministic 360/32/.75 sample used by every default blocking sheet. */
export const SHEET_ENTRY_LINEAR_CSS =
  "linear(0.00000, 0.04373, 0.14239, 0.26222, 0.38373, 0.49652, 0.59580, 0.68011, 0.74985, 0.80640, 0.85153, 0.88706, 0.91472, 0.93605, 0.95236, 0.96472, 0.97403, 0.98099, 0.98616, 0.98998, 0.99279, 0.99484, 0.99633, 0.99741, 0.99818, 0.99873, 0.99912, 0.99940, 0.99959, 0.99972, 1.00000)";
export const SHEET_ENTRY_EASING = sampledEase(SHEET_ENTRY_SAMPLES);
const SHEET_CLEANUP_SAMPLES = [
  0, 0.01595, 0.05654, 0.11293, 0.17854, 0.24856, 0.31951, 0.38898,
  0.45533, 0.51754, 0.57502, 0.6275, 0.67495, 0.7175, 0.75539, 0.78891,
  0.81842, 0.84426, 0.86679, 0.88636, 0.9033, 0.9179, 0.93046, 0.94123,
  0.95043, 0.95828, 0.96496, 0.97062, 0.97542, 0.97947, 1,
] as const;
/** The same sheet spring sampled over the Memory mock's shorter 260ms run. */
export const SHEET_CLEANUP_LINEAR_CSS =
  "linear(0.00000, 0.01595, 0.05654, 0.11293, 0.17854, 0.24856, 0.31951, 0.38898, 0.45533, 0.51754, 0.57502, 0.62750, 0.67495, 0.71750, 0.75539, 0.78891, 0.81842, 0.84426, 0.86679, 0.88636, 0.90330, 0.91790, 0.93046, 0.94123, 0.95043, 0.95828, 0.96496, 0.97062, 0.97542, 0.97947, 1.00000)";
export const SHEET_CLEANUP_EASING = sampledEase(SHEET_CLEANUP_SAMPLES);
/** Press/release feedback. Snappy so an interrupted release re-targets. */
export const SPRING_TAP = { type: "spring", stiffness: 520, damping: 46, mass: 1 } as const;
/** Layout / FLIP / list reorders. */
export const SPRING_LAYOUT = { type: "spring", stiffness: 380, damping: 34, mass: 1 } as const;
/** Popover / menu reveal — origin-anchored and over-damped. */
export const SPRING_POPOVER = { type: "spring", stiffness: 420, damping: 42, mass: 1 } as const;
/** Near-instant snap for small indicator travel (dense menu selection fill,
 *  focus ring) — critically damped at ~80ms settle, no floaty lag or
 *  overshoot. */
export const SPRING_SNAP = { type: "spring", stiffness: 900, damping: 44, mass: 0.5 } as const;
/** Row settles for sibling-row entrances. */
export const SPRING_ROW_ENTRY = { type: "spring", stiffness: 350, damping: 40, mass: 0.8 } as const;
/** Disclosure body height — Fluid Functionalism's accordion content spring
 *  (`spring.moderate` with `bounce: 0`). Pure height has no aesthetic value in
 *  bouncing, so it approaches smoothly and lands exactly on the measured px. */
export const SPRING_DISCLOSURE = { type: "spring", duration: 0.16, bounce: 0 } as const;
/** Scroll-container resize response from use-stick-to-bottom. These values
 * are normalized by that library, not by Motion. */
export const SPRING_SCROLL_RIVER = { damping: 0.92, stiffness: 0.025, mass: 1.5 } as const;
// ─── Eases (cubic-bezier tuples) ─────────────────────────────

export const EASE_DECELERATE = [0.05, 0.7, 0.1, 1] as const;
/** Sidebar / inspector route slides. */
export const EASE_EMPHASIZED = [0.32, 0.72, 0, 1] as const;
/** Shared smooth-out curve. */
export const EASE_OUT = [0.22, 1, 0.36, 1] as const;
/** Fast dissolve used by surfaces leaving the workspace. */
export const EASE_DISSOLVE = [0.23, 1, 0.32, 1] as const;
/** Directional content leaves on this curve before the successor settles. */
export const EASE_ACCELERATE = [0.4, 0, 1, 1] as const;
/** Brief in-place blur dissolves use the mock's blur curve. */
export const EASE_BLUR = [0.2, 0.8, 0.2, 1] as const;
/** Sequential inline text swaps use the browser's `ease-in-out` curve. */
export const EASE_IN_OUT = [0.42, 0, 0.58, 1] as const;
/** Constant-speed state indicators: progress sweeps and indeterminate spinners. */
export const EASE_LINEAR = "linear" as const;

// ─── Durations (seconds) ─────────────────────────────────────
export const MOTION = {
  reduced: 0.001,
  instant: 0.01,
  indicator: 0.1,
  press: 0.12,
  feedback: 0.15,
  exit: 0.16,
  popover: 0.18,
  focus: 0.18,
  dissolve: 0.22,
  peekEnter: 0.45,
  peekExit: 0.16,
  sheetEnter: 0.45,
  tabsLayout: 0.3,
  tabsSliding: 0.25,
  traceRow: 0.36,
  toolCallCadence: 1.6,
  textSwap: 0.15,
  iconSwap: 0.25,
  counterSpin: 0.42,
  skeletonPulse: 1,
  skeletonReveal: 0.4,
  skeletonSummaryDelay: 0.56,
  tabShift: 0.22,
  tabExit: 0.1,
  tabEnter: 0.12,
  iconMorph: 0.24,
  contentBlur: 0.26,
  instrument: 0.26,
  instrumentHideDelay: 0.25,
  content: 0.26,
  success: 0.3,
  furniture: 0.3,
  sidebarShow: 0.3,
  sidebarHide: 0.4,
  titleCleanup: 0.52,
  sheetCleanup: 0.26,
  refreshMorph: 0.22,
  refreshLoop: 0.7,
  refreshDoneHold: 0.65,
  holdArm: 0.65,
  deckExit: 0.16,
  deckEnter: 0.45,
  deckHandoff: 0.06,
  refreshRequest: 1.05,
  refreshSettleMin: 0.08,
  refreshSettleMax: 0.32,
  loadingShowDelay: 0.25,
  loadingMinVisible: 0.45,
  tooltipDelay: 0.5,
  tooltipWarm: 0.3,
  previewShow: 0.3,
  previewHide: 0.2,
  acknowledge: 0.42,
  copyFeedback: 0.9,
  shimmer: 1.8,
  demoDelay: 0.7,
  notificationDemoStart: 0.5,
  notificationDemoInterval: 0.8,
  toastLifetime: 7,
  sheetExit: 0.16,
  pageEnter: 0.24,
  pageChrome: 0.18,
  pageReduced: 0.16,
  pageStagger: 0.036,
  pageSkeletonHold: 0.18,
  pageSkeletonReveal: 0.22,
  spinner: 0.7,
  /** Backwards-compatible skeleton cadence; new surfaces use skeletonPulse. */
  skeleton: 1,
  fast: 0.12,
  check: 0.15,
  row: 0.15,
  palette: 0.18,
  trace: 0.22,
  panel: 0.22,
  route: 0.24,
} as const;

export const DISTANCE = {
  subtle: 2,
  entry: 6,
  popover: 5,
  contentSwap: 8,
  traceRow: 4,
  deckAction: 32,
  deckDrop: 4,
  peekEnter: 16,
  peekExit: 12,
  sheetEnter: 24,
  sheetExit: 28,
  sidebarHide: 48,
  dissolve: 4,
  textSwap: 4,
  pageEnter: 6,
} as const;

/** Geometry token for the underline tab indicator. */
export const TAB_INDICATOR_LINE_SIZE = 2;

export const BLUR = {
  subtle: 2,
  entry: 3,
  contentSwap: 2,
  traceRow: 2,
  title: 1.4,
  sidebar: 6,
  dissolve: 2,
  furniture: 1,
  iconSwap: 2,
  counter: 2,
  skeleton: 2,
  pageEnter: 2,
} as const;

/** Stagger roles shared by sequential detail rows. */
export const STAGGER = {
  traceDetail: 0.03,
  counterCharacter: 0.032,
} as const;

const TABS_LAYOUT_SAMPLES = [
  0, 0.01697, 0.06061, 0.12184, 0.19362, 0.27061, 0.34882, 0.42541,
  0.49837, 0.56641, 0.62876, 0.68507, 0.73525, 0.77948, 0.81804, 0.85132,
  0.87979, 0.90391, 0.92417, 0.94102, 0.95491, 0.96624, 0.9754, 0.9827,
  0.98845, 0.99292, 0.99633, 0.99887, 1.00071, 1.002, 1,
] as const;
/** `springEasing(spring.layout, 300, 30)` from board-motion.js. */
export const TABS_LAYOUT_LINEAR_CSS =
  "linear(0.00000, 0.01697, 0.06061, 0.12184, 0.19362, 0.27061, 0.34882, 0.42541, 0.49837, 0.56641, 0.62876, 0.68507, 0.73525, 0.77948, 0.81804, 0.85132, 0.87979, 0.90391, 0.92417, 0.94102, 0.95491, 0.96624, 0.97540, 0.98270, 0.98845, 0.99292, 0.99633, 0.99887, 1.00071, 1.00200, 1.00000)";
export const TABS_LAYOUT_EASING = sampledEase(TABS_LAYOUT_SAMPLES);

const TRACE_ROW_SAMPLES = [
  0, 0.01858, 0.06351, 0.12289, 0.18899, 0.25691, 0.32359, 0.38724,
  0.44686, 0.502, 0.55253, 0.59853, 0.6402, 0.67781, 0.71168, 0.74211,
  0.76942, 0.79389, 0.8158, 0.83541, 0.85295, 0.86863, 0.88265, 0.89518,
  0.90637, 0.91638, 0.92531, 0.93329, 0.94042, 0.94679, 0.95248, 0.95756,
  0.96209, 0.96615, 0.96977, 0.973, 1,
] as const;
/** `springEasing(spring.traceRow, 360, 36)` from board-motion.js. */
export const TRACE_ROW_LINEAR_CSS =
  "linear(0.00000, 0.01858, 0.06351, 0.12289, 0.18899, 0.25691, 0.32359, 0.38724, 0.44686, 0.50200, 0.55253, 0.59853, 0.64020, 0.67781, 0.71168, 0.74211, 0.76942, 0.79389, 0.81580, 0.83541, 0.85295, 0.86863, 0.88265, 0.89518, 0.90637, 0.91638, 0.92531, 0.93329, 0.94042, 0.94679, 0.95248, 0.95756, 0.96209, 0.96615, 0.96977, 0.97300, 1.00000)";
export const TRACE_ROW_EASING = sampledEase(TRACE_ROW_SAMPLES);
export const TRACE_ROW_ENTER = {
  opacity: 0,
  y: DISTANCE.traceRow,
  filter: `blur(${BLUR.traceRow}px)`,
} as const;
export const TRACE_ROW_VISIBLE = { opacity: 1, y: 0, filter: "blur(0px)" } as const;
export const TRACE_ROW_EXIT = {
  opacity: 0,
  y: -DISTANCE.traceRow,
  filter: `blur(${BLUR.traceRow}px)`,
} as const;
export const TRACE_ROW_TRANSITION = { duration: MOTION.traceRow, ease: TRACE_ROW_EASING } as const;

export const TEXT_SWAP_ENTER = {
  opacity: 0,
  y: DISTANCE.textSwap,
  filter: `blur(${BLUR.contentSwap}px)`,
} as const;
export const TEXT_SWAP_VISIBLE = { opacity: 1, y: 0, filter: "blur(0px)" } as const;
export const TEXT_SWAP_EXIT = {
  opacity: 0,
  y: -DISTANCE.textSwap,
  filter: `blur(${BLUR.contentSwap}px)`,
} as const;
export const TEXT_SWAP_TRANSITION = { duration: MOTION.textSwap, ease: EASE_IN_OUT } as const;

export const COUNTER_SPIN_OLD = { opacity: 1, y: 0, filter: "blur(0px)" } as const;
export const COUNTER_SPIN_OLD_EXIT = {
  opacity: 0,
  y: "-105%",
  filter: `blur(${BLUR.counter}px)`,
} as const;
export const COUNTER_SPIN_NEW = {
  opacity: 0,
  y: "105%",
  filter: `blur(${BLUR.counter}px)`,
} as const;
export const COUNTER_SPIN_VISIBLE = { opacity: 1, y: 0, filter: "blur(0px)" } as const;
export const COUNTER_SPIN_TRANSITION = {
  duration: MOTION.counterSpin,
  ease: TABS_LAYOUT_EASING,
} as const;
export function counterSpinDelay(length: number, index: number): number {
  return Math.min(Math.max(length - index - 1, 0), 3) * STAGGER.counterCharacter;
}
export function counterSpinSettleDelay(length: number): number {
  return COUNTER_SPIN_TRANSITION.duration + counterSpinDelay(length, 0);
}

/** Conversation-outline proximity field, mirrored by design/tokens.css. */
export const CONVERSATION_RAIL = {
  activeScale: 1.5,
  hoverScale: 8 / 3,
  pointerLead: 8,
  readLine: 96,
  bottomThreshold: 8,
  labelBlur: 2,
  sigmaY: 12,
  fullX: 32,
  sigmaX: 24,
  maxDx: 64,
  maxDy: 48,
  fadeX: 20,
  labelOn: 0.5,
  labelOff: 0.38,
} as const;

export const DURATION_POPOVER = MOTION.palette;
export const DURATION_PANEL = MOTION.panel;
/** CSS-equivalent popover entrance; use for portaled overlays. */
export const POPOVER_ENTER_TRANSITION = {
  duration: MOTION.popover,
  ease: EASE_OUT,
} as const;
/** Exact sampled 210/26 peek opening curve from board-motion.js. */
export const PEEK_ENTER_TRANSITION = {
  duration: MOTION.peekEnter,
  ease: PEEK_ENTRY_EASING,
} as const;
/** Exact sampled 360/32/.75 blocking-sheet entrance from board-motion.js. */
export const SHEET_ENTER_TRANSITION = {
  duration: MOTION.sheetEnter,
  ease: SHEET_ENTRY_EASING,
} as const;
/** Exact Memory tier-three sheet entrance from board-memory.html. */
export const SHEET_CLEANUP_ENTER_TRANSITION = {
  duration: MOTION.sheetCleanup,
  ease: SHEET_CLEANUP_EASING,
} as const;
/** Exact crisp exit paired with the Memory tier-three sheet. */
export const SHEET_CLEANUP_EXIT_TRANSITION = {
  duration: MOTION.focus,
  ease: EASE_DISSOLVE,
} as const;

// ─── Exit tweens ─────────────────────────────────────────────
// Enter = critically damped spring. Exit = a plain tween, one tier quicker
// than the entrance — so a dismissal reads crisp and final instead of
// replaying the entrance in reverse. Bigger thing = slower (both ways).
//
//   animate={RISE_SETTLED}
//   exit={{ ...DISSOLVE_OUT, transition: EXIT_FAST }}   // popover/overlay
//   transition={SPRING_POPOVER}                          // enter
//
// JS-only: framer-motion exits have no CSS counterpart, so there is no
// styles.css mirror to keep in sync (unlike the --duration-*/--ease-* the
// MOTION map above mirrors). Reuse the EASE_OUT curve and a MOTION tier;
// never hand-write an exit `{ duration }`.

/** Exit for popovers, tooltips, menus, modal panels — a tier quicker than
 *  their ~0.2s popover/panel entrance. */
export const EXIT_FAST = { duration: MOTION.fast, ease: EASE_OUT } as const;
/** Exit for list rows / sections — a tier quicker than their ~0.2s entrance,
 *  but slower than EXIT_FAST so a bigger element still leaves slower. */
export const EXIT_ROW = { duration: MOTION.row, ease: EASE_OUT } as const;

/** Attach the matching exit tween to an exit POSE. Keeps the "no bounce, one
 *  tier quicker" contract in one place:
 *  `exit={withExit(DISSOLVE_OUT)}` ≡ `{ ...DISSOLVE_OUT, transition: EXIT_FAST }`. */
export function withExit<T extends object>(
  pose: T,
  tween: typeof EXIT_FAST | typeof EXIT_ROW = EXIT_FAST,
) {
  return { ...pose, transition: tween };
}
/** Right sidebar hide: slower than a normal panel exit so the fade + blur
 *  dissolve reads while the panel drifts right and the chat reclaims the
 *  space on the SAME duration (kept in lockstep so the opaque card
 *  crossfades to reveal the expanding content — no overlap, no pause). */
export const DURATION_RIGHT_PANEL_HIDE = MOTION.sidebarHide;
/** Furniture hide distance shared by both app rails. */
export const DISTANCE_RAIL_HIDE = DISTANCE.sidebarHide;

// ─── Blur-dissolve vocabulary ────────────────────────────────
// House enter/exit poses: content rises into focus, exits dissolve.
// Spread + override per surface (`{ ...RISE_IN, y: -4 }`); pick duration
// from MOTION per tier. Exits run FASTER than entrances (fast/row vs
// row/panel). Keep blur ≤4px — these are content-sized elements, and
// fill-rate cost scales with area.

/** Entrance pose — pair with `animate={RISE_SETTLED}`. */
export const RISE_IN = {
  opacity: 0,
  y: DISTANCE.entry,
  filter: `blur(${BLUR.entry}px)`,
} as const;
export const RISE_SETTLED = { opacity: 1, y: 0, filter: "blur(0px)" } as const;
/** Exit pose for sections/banners/popunders — quicker, dissolving. */
export const DISSOLVE_OUT = { opacity: 0, scale: 0.97, filter: "blur(3px)" } as const;
/** Inline disclosure that grows upward from a trigger row. Portaled menus use
 * their measured corner; room-owned disclosures keep this bottom origin. */
export const POSE_INLINE_POPOVER_IN = {
  opacity: 0,
  y: DISTANCE.popover,
  scale: 0.99,
  filter: `blur(${BLUR.dissolve}px)`,
} as const;
export const POSE_INLINE_POPOVER_VISIBLE = {
  opacity: 1,
  y: 0,
  scale: 1,
  filter: "blur(0px)",
  transitionEnd: { filter: "none" },
} as const;
export const POSE_INLINE_POPOVER_OUT = {
  opacity: 0,
  y: DISTANCE.popover,
  scale: 0.99,
  filter: `blur(${BLUR.dissolve}px)`,
} as const;

/** Viewport popovers use the canonical 5px / 2px blur settle. The filter is
 * cleared after entry so a settled menu never retains a compositor layer. */
export const POSE_PORTALED_POPOVER_IN = {
  opacity: 0,
  y: DISTANCE.popover,
  scale: 0.985,
  filter: `blur(${BLUR.subtle}px)`,
} as const;
export const POSE_PORTALED_POPOVER_VISIBLE = {
  opacity: 1,
  y: 0,
  scale: 1,
  filter: "blur(0px)",
  transitionEnd: { filter: "none" },
} as const;
export const POSE_PORTALED_POPOVER_OUT = {
  opacity: 0,
  y: DISTANCE.popover,
  scale: 0.985,
  filter: `blur(${BLUR.subtle}px)`,
} as const;
/** Exit pose for popLayout list rows — the removed row dissolves while
 *  siblings FLIP up via their `layout` springs. */
export const ROW_EXIT = { opacity: 0, scale: 0.96, filter: "blur(4px)" } as const;

/** Compact nonblocking sidecar/peek lifecycle. */
export const POSE_PEEK_IN = {
  opacity: 0,
  x: DISTANCE.peekEnter,
} as const;
/** The peek's material blur is static; foreground text never defocuses. */
export const POSE_PEEK_VISIBLE = {
  opacity: 1,
  x: 0,
} as const;
export const POSE_PEEK_OUT = {
  opacity: 0,
  x: DISTANCE.peekExit,
} as const;

export const POSE_SHEET_IN = {
  opacity: 0,
  y: DISTANCE.sheetEnter,
} as const;
export const POSE_SHEET_VISIBLE = { opacity: 1, y: 0 } as const;
export const POSE_SHEET_OUT = {
  opacity: 0,
  y: DISTANCE.sheetExit,
} as const;

export const PAGE_VARIANTS = {
  enter: (direction: number) => ({
    opacity: 0,
    y: DISTANCE.pageEnter,
    x: direction * DISTANCE.pageEnter,
    filter: `blur(${BLUR.pageEnter}px)`,
  }),
  center: {
    opacity: 1,
    x: 0,
    y: 0,
    filter: "blur(0px)",
  },
  exit: (direction: number) => ({
    opacity: 0,
    x: direction * -DISTANCE.dissolve,
    y: -DISTANCE.dissolve,
    filter: `blur(${BLUR.dissolve}px)`,
  }),
} as const;

export const CONTENT_SWAP_VARIANTS = {
  enter: (direction: number) => ({
    opacity: 0,
    x: direction * DISTANCE.contentSwap,
    filter: `blur(${BLUR.contentSwap}px)`,
  }),
  center: { opacity: 1, x: 0, filter: "blur(0px)" },
  exit: (direction: number) => ({
    opacity: 0,
    x: direction * -DISTANCE.contentSwap,
    filter: `blur(${BLUR.contentSwap}px)`,
  }),
} as const;

/** The same content-swap recipe can travel through a local vertical list
 * without inventing a second timing or blur vocabulary. */
export const CONTENT_SWAP_VERTICAL_VARIANTS = {
  enter: (direction: number) => ({
    opacity: 0,
    y: direction * DISTANCE.contentSwap,
    filter: `blur(${BLUR.contentSwap}px)`,
  }),
  center: { opacity: 1, y: 0, filter: "blur(0px)" },
  exit: (direction: number) => ({
    opacity: 0,
    y: direction * -DISTANCE.contentSwap,
    filter: `blur(${BLUR.contentSwap}px)`,
  }),
} as const;

/** Request-deck exit and successor entry. Directional actions stay horizontal;
 * direction 0 drops the current card and raises its successor in place. */
export const DECK_VARIANTS = {
  exit: (direction: number) => (
    direction === 0
      ? {
          opacity: 0,
          x: 0,
          y: DISTANCE.deckDrop,
          scale: 0.96,
          filter: `blur(${BLUR.contentSwap}px)`,
        }
      : {
          opacity: 0,
          x: direction * DISTANCE.deckAction,
          y: 0,
          scale: 1,
          filter: `blur(${BLUR.contentSwap}px)`,
        }
  ),
  enter: (direction: number) => (
    direction === 0
      ? {
          opacity: 0,
          x: 0,
          y: DISTANCE.contentSwap,
          scale: 1,
          filter: `blur(${BLUR.contentSwap}px)`,
        }
      : {
          opacity: 0,
          x: -direction * DISTANCE.peekEnter,
          y: 0,
          scale: 1,
          filter: `blur(${BLUR.contentSwap}px)`,
        }
  ),
  center: { opacity: 1, x: 0, y: 0, scale: 1, filter: "blur(0px)" },
} as const;
export const DECK_EXIT_TRANSITION = { duration: MOTION.deckExit, ease: EASE_ACCELERATE } as const;
export const DECK_ENTER_TRANSITION = {
  duration: MOTION.deckEnter,
  delay: MOTION.deckHandoff,
  ease: PEEK_ENTRY_EASING,
} as const;

export const PAGE_ENTER_TRANSITION = { duration: MOTION.pageEnter, ease: EASE_OUT } as const;
export const PAGE_EXIT_TRANSITION = { duration: MOTION.exit, ease: EASE_DISSOLVE } as const;
export const CONTENT_ENTER_TRANSITION = { duration: MOTION.content, ease: EASE_OUT } as const;
export const CONTENT_EXIT_TRANSITION = { duration: MOTION.exit, ease: EASE_ACCELERATE } as const;
export const FURNITURE_TRANSITION = { duration: MOTION.furniture, ease: EASE_DISSOLVE } as const;
export const PEEK_EXIT_TRANSITION = { duration: MOTION.peekExit, ease: EASE_DISSOLVE } as const;
export const SHEET_EXIT_TRANSITION = { duration: MOTION.sheetExit, ease: EASE_DISSOLVE } as const;
/** Shared tab geometry settles separately from the visible indicator slide. */
export const TABS_LAYOUT_TRANSITION = { duration: MOTION.tabsLayout, ease: TABS_LAYOUT_EASING } as const;
export const TABS_SLIDE_TRANSITION = { duration: MOTION.tabsSliding, ease: EASE_OUT } as const;

/** Home disclosure lifecycle: opening panel, closing panel, then follower
 * FLIP settle. Kept distinct from viewport popovers. */
export const DISCLOSURE = {
  panel: { duration: MOTION.popover, ease: EASE_OUT },
  close: { duration: MOTION.popover, ease: EASE_OUT },
  followers: { duration: MOTION.popover, ease: EASE_OUT },
  poseIn: { opacity: 0, y: DISTANCE.popover, scale: 0.99, filter: `blur(${BLUR.dissolve}px)` },
  poseVisible: { opacity: 1, y: 0, scale: 1, filter: "blur(0px)" },
  poseOut: { opacity: 0, y: DISTANCE.popover, scale: 0.99, filter: `blur(${BLUR.dissolve}px)` },
} as const;

// ─── Spatial origin helpers ──────────────────────────────────

/** Returns the viewport-space center of an element. Used as the spatial
 *  origin for modal open animations — the modal then grows from this
 *  point, so users can see WHERE it came from. */
export function originFromEvent(
  target: Element | null | undefined,
): { x: number; y: number } | null {
  if (!target) return null;
  const rect = target.getBoundingClientRect();
  return { x: rect.x + rect.width / 2, y: rect.y + rect.height / 2 };
}

/** Given a trigger origin (in viewport coords), return the initial
 *  transform a modal should use to "grow from" that origin. We clamp
 *  the delta — a full FLIP would make a corner-triggered modal slide
 *  across the entire screen, which feels too much. ~64px is enough
 *  motion to encode origin without being theatrical. Returns null
 *  when origin is missing → caller falls back to neutral fade. */
export function modalOriginTransform(
  origin: { x: number; y: number } | null,
): { x: number; y: number } | null {
  if (!origin) return null;
  const cx = window.innerWidth / 2;
  const cy = window.innerHeight / 2;
  const dx = origin.x - cx;
  const dy = origin.y - cy;
  const MAX = 64;
  const clamp = (v: number) => Math.sign(v) * Math.min(Math.abs(v) * 0.18, MAX);
  return { x: clamp(dx), y: clamp(dy) };
}
