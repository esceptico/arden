/**
 * Motion tokens — springs, eases, and durations for framer-motion and
 * JS-driven animation. Single source of truth; the CSS side lives in
 * styles.css (`--duration-*`, `--ease-*`) and must stay in sync with the
 * MOTION mirror below.
 *
 * Geist design system alignment:
 *   - Primary ease: cubic-bezier(0.175, 0.885, 0.32, 1.1)
 *   - State transitions: 150ms · Popovers: 200ms · Overlays: 300ms
 *   - Motion clarifies change, never decorates.
 */

// ─── Geist primary ease ─────────────────────────────────────

/** Geist design-system primary easing — slight overshoot that "lands". */
export const GEIST_EASE = [0.175, 0.885, 0.32, 1.1] as const;

// ─── Springs ────────────────────────────────────────────────
// Stiffness/damping pairs tuned to use case. Higher stiffness = faster
// settle; higher damping = less overshoot. `mass: 1` keeps the visual
// scale consistent with framer-motion defaults.

/** Modals, sheets, drawers. Confident settle, no perceptible bounce. */
export const SPRING_MODAL = { type: "spring", stiffness: 480, damping: 36, mass: 1 } as const;
/** Press/release feedback. Snappy so an interrupted release re-targets. */
export const SPRING_TAP = { type: "spring", stiffness: 520, damping: 34, mass: 1 } as const;
/** Layout / FLIP / list reorders. Slowest tier, but still settles ~250ms —
 *  a shared element should glide, not float. */
export const SPRING_LAYOUT = { type: "spring", stiffness: 380, damping: 34, mass: 1 } as const;
/** Popover / menu reveal — origin-anchored. Opened dozens of times a day,
 *  so it lands fast with barely-there overshoot, never a wobble. */
export const SPRING_POPOVER = { type: "spring", stiffness: 550, damping: 36, mass: 1 } as const;
/** Traveling proximity-hover highlight (ProximityHighlight). Tracks the
 *  pointer near-instantly — critically damped at ~80ms settle so it snaps
 *  to the hovered row with no floaty lag or overshoot. */
export const SPRING_PROXIMITY = { type: "spring", stiffness: 1200, damping: 50, mass: 0.5 } as const;
/** Row settles — Control Center–style spring for sibling-row entrances.
 *  Used by `TurnGroup`'s work-block stagger. Snappier than SPRING_MODAL. */
export const SPRING_ROW_ENTRY = { type: "spring", stiffness: 480, damping: 32, mass: 0.8 } as const;
/** Trace ticker rows. Heavier damping than ROW_ENTRY — zero overshoot so
 *  the most-fired animation in the app never wobbles mid-stream. */
export const SPRING_TRACE_ROW = { type: "spring", stiffness: 350, damping: 40, mass: 0.8 } as const;
/** Approval-stack cards (deck promotion, queue collapse). User-tuned:
 *  quick settle, no overshoot. */
export const SPRING_STACK = { type: "spring", stiffness: 340, damping: 32, mass: 0.9 } as const;

// ─── Eases (cubic-bezier tuples) ─────────────────────────────

/** Material 3 emphasized-decelerate — entries that should "land". */
export const EASE_DECELERATE = [0.05, 0.7, 0.1, 1] as const;
/** Snappy out-cubic for hover brightness/color shifts. */
export const EASE_HOVER = [0.16, 1, 0.3, 1] as const;
/** Sidebar / inspector route slides. Mirrors CSS `--ease-emphasized`. */
export const EASE_EMPHASIZED = [0.32, 0.72, 0, 1] as const;
/** Soft ease-out for popover landings and small scale-ins. Mirrors CSS
 *  `--ease-out-soft`. */
export const EASE_OUT = [0.2, 0.8, 0.2, 1] as const;

// ─── Durations (seconds) ─────────────────────────────────────
// Geist-aligned: state 0.15, popover 0.2, overlay 0.3. The MOTION map
// keeps the named-by-feel scale so existing call sites don't churn; the
// values now snap to the Geist grid.

export const MOTION = {
  fast: 0.1,
  check: 0.12,
  row: 0.15,
  palette: 0.2,
  trace: 0.2,
  panel: 0.2,
  route: 0.3,
} as const;

export const DURATION_POPOVER = MOTION.palette;
export const DURATION_PANEL = MOTION.panel;

// ─── Exit tweens ─────────────────────────────────────────────
// Enter = spring (the SPRING_* tokens, a little overshoot on the bigger
// tiers). Exit = a plain tween — NO bounce, and one duration-tier quicker
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
export const DURATION_RIGHT_PANEL_HIDE = 0.4;

// ─── Panel entry curve ───────────────────────────────────────

export const ENTRY_PANEL = SPRING_MODAL;

// ─── Blur-dissolve vocabulary ────────────────────────────────
// House enter/exit poses: content rises into focus, exits dissolve.
// Spread + override per surface (`{ ...RISE_IN, y: -4 }`); pick duration
// from MOTION per tier. Exits run FASTER than entrances (fast/row vs
// row/panel). Keep blur ≤4px — these are content-sized elements, and
// fill-rate cost scales with area.

/** Entrance pose — pair with `animate={RISE_SETTLED}`. */
export const RISE_IN = { opacity: 0, y: 6, filter: "blur(3px)" } as const;
export const RISE_SETTLED = { opacity: 1, y: 0, filter: "blur(0px)" } as const;
/** Exit pose for sections/banners/popunders — quicker, dissolving. */
export const DISSOLVE_OUT = { opacity: 0, scale: 0.97, filter: "blur(3px)" } as const;
/** Exit pose for popLayout list rows — the removed row dissolves while
 *  siblings FLIP up via their `layout` springs. */
export const ROW_EXIT = { opacity: 0, scale: 0.96, filter: "blur(4px)" } as const;

/** Shared modal panel pose — the single source for hand-rolled modal
 *  entrances (PageModal, ToolViewer, Mermaid) so the pose can't drift
 *  per call site. Pair with ENTRY_PANEL. */
export const POSE_MODAL = { opacity: 0, scale: 0.96, y: 6 } as const;

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
