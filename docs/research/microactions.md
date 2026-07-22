# Microactions: A Reference for Modern UI Motion

> Microinteractions are tiny, single-purpose product moments — Trigger, Rules, Feedback, Loops/Modes — and the modern bar is short, spring-physics-driven, interruptible, GPU-cheap, and quiet enough that you barely notice it [1][6][13].

This is a working reference for the ARDEN desktop client. Every claim is sourced. Numbers are concrete (ms, cubic-bezier, stiffness/damping). Anti-patterns are called out explicitly.

---

## 1. What counts as a microinteraction

Dan Saffer's canonical definition in *Microinteractions* (O'Reilly, 2013): a microinteraction is "a contained product moment that revolves around a single use case" — one tiny piece of functionality that only does one thing [1][2]. Saffer decomposes every microinteraction into four parts [1][2][3]:

- **Trigger** — what starts it. Manual (click, hover, keystroke) or system (a condition becoming true).
- **Rules** — what can and cannot happen during the interaction; the state machine.
- **Feedback** — visual, auditory, or haptic confirmation of what the Rules are doing. Absence of feedback is also feedback.
- **Loops & Modes** — duration, repetition, evolution over time, and any conditional variants.

**Micro vs macro motion.** Macro motion handles route changes, modals, page-level choreography. Micro motion is the press-back on a button, the shimmer of a loading skeleton, the spring of a toggle. The user encounters micro motion thousands of times per session, so its budget for "interesting" is near zero — it must be *quiet, fast, and interruptible* [6][13]. Macro motion gets to be expressive because it happens rarely.

**Why they matter.** Microinteractions carry three loads: (a) *system state* — "I heard you, I'm working" — which collapses perceived latency below the 100 ms / 400 ms Doherty thresholds [12]; (b) *affordance* — they teach the Rules without copy; (c) *brand feel* — the cumulative texture of a thousand small moments is what users actually remember [6][13].

---

## 2. Motion principles

**Disney's 12 principles, the four that survive translation to UI** [10][14]:

1. **Anticipation** — a tiny inverse motion before the main one (a button dipping 1 px before lifting). Use sparingly; on UI it usually reads as lag.
2. **Follow-through / overshoot** — what makes spring physics feel alive. A panel that settles past its target then back is doing follow-through.
3. **Slow in / slow out (ease)** — almost always *ease-out* in UI: things start fast and settle gently [6][7].
4. **Squash & stretch** — read as scale-on-press / scale-on-release; never literal deformation in a productivity tool.

**Material 3 motion principles** [4][8]: *Expressive* (motion has personality), *Simple* (one clear idea per transition), *Coherent* (shared tokens across the system). Material codifies this as a token system rather than ad-hoc curves.

**Apple HIG motion** [5]: motion exists to (a) keep people *oriented*, (b) give *feedback*, (c) *teach* the interface. Apple's specific rules: prefer *quick and precise* over decorative; *strive for realism* (don't violate physical intuition); *avoid animating frequent actions*; make motion *optional* and respect Reduce Motion [5][16].

**SwiftUI philosophy.** SwiftUI defaults to spring animations (not eases) because direct-manipulation gestures carry velocity, and only springs can absorb that velocity into the resulting animation without a discontinuity [5][9].

---

## 3. Springs vs eases

Springs are physics simulations parameterised by **stiffness** (how snappy), **damping** (how much it resists oscillation), and **mass** (how heavy it feels) [9][11]. Motion's defaults: `stiffness: 100, damping: 10, mass: 1` for the legacy physics model; the newer duration-based model uses `bounce: 0.25` with a configurable `visualDuration` [11].

**Intuition** [9][11]:

- Stiffness up → snappier, more aggressive.
- Damping up → less wobble; at `damping² ≥ 4 · mass · stiffness` the spring is critically damped (no overshoot).
- Mass up → more sluggish acceleration *and* deceleration.

**When to reach for a spring** [6][7][9]:

- Anything triggered by **direct manipulation** (drag, swipe, pinch). Springs absorb release velocity continuously; an ease introduces a velocity discontinuity that feels broken.
- Anything that should **feel like an object** with weight: modals, sheets, drawers, picked-up cards.
- Interruptible animations — a spring re-targeted mid-flight keeps its velocity; an ease has to restart.

**When eases win** [6][7]:

- Hover/color transitions and other 100–200 ms property tweens. Springs at short durations either look like a hard ease or wobble.
- Anything **looping** (skeletons, pulses) — needs deterministic timing.
- Animations where the *exact duration* matters (sync to audio, sync to a progress event).

**Practical ARDEN defaults (Motion)**:

```ts
// Direct manipulation, panels, sheets
const sheetSpring = { type: "spring", stiffness: 380, damping: 32, mass: 1 }; // snappy, no overshoot

// Soft "settle" — picked-up cards
const cardSpring  = { type: "spring", stiffness: 260, damping: 26, mass: 1 }; // tiny overshoot

// Bouncy, attention-getting — use rarely
const bouncySpring = { type: "spring", bounce: 0.35, visualDuration: 0.45 };

// Hover / color / opacity
const hoverEase = { duration: 0.15, ease: [0.16, 1, 0.3, 1] }; // ease-out-expo
```

Stiffness ≥ 300 + damping ~30 is Emil Kowalski's and Rauno's typical territory for "snappy but not nervous" [6][7][13].

---

## 4. Timing & easing

**Duration buckets** (converging values across Material 3, Apple, Emil, Rauno) [4][6][7][8]:

| Use | Duration |
|---|---|
| Tap-back / hover / focus | 80–150 ms |
| Tooltip, popover open | 150–200 ms |
| Dropdown, menu, small overlay | 180–250 ms |
| Modal, sheet, drawer | 250–400 ms |
| Page / route transition | 350–500 ms |
| Ambient (skeleton pulse, breathing) | 1200–2400 ms loops |

Rule of thumb: **nothing under user control should exceed ~300 ms** [6]. Above that, perceived performance collapses even if the actual work is instant.

**Material 3 easing tokens** (cubic-bezier) [4][8]:

- `emphasized` → `cubic-bezier(0.2, 0.0, 0, 1.0)` — default for elements both entering and leaving (Material 3's all-purpose curve).
- `emphasized-decelerate` → `cubic-bezier(0.05, 0.7, 0.1, 1.0)` — incoming elements [8].
- `emphasized-accelerate` → `cubic-bezier(0.3, 0.0, 0.8, 0.15)` — outgoing.
- `standard` → `cubic-bezier(0.2, 0.0, 0, 1.0)`.

**iOS/SwiftUI defaults** [5][9]: a spring with `response: 0.5, dampingFraction: 0.825` for default; system-wide leans on ease-out for everything time-based.

**"Out is faster than in."** Outgoing elements (closing a modal, dismissing a toast) should be ~30–50 % shorter than the entrance. The reason: the user has already decided to dismiss; making them wait equals "the app is slow" [6][8]. Material encodes this as separate accelerate/decelerate tokens.

**Linear is almost always wrong** for *interactive* animation — nothing in the physical world moves linearly. Reserve `linear` for indeterminate progress bars and crossfades [6].

---

## 5. Hover / press / focus states

These are three different things and should respond differently [13]:

- **Hover** — cheap signal of "this is interactive." Brightness / background shift, ~120 ms ease-out. Avoid scale on hover — desktop pointers don't have hit-area uncertainty, and scale-on-hover reads as a child's toy [6][13].
- **Press / active** — confirmation of input received. Scale-down to **0.96–0.98** (smaller for larger surfaces, ~0.96 for big cards, ~0.98 for small buttons), ~80 ms. Apple's tap-back is ~0.97 [5][6]. The animation should *spring back* on release, not ease.
- **Focus** — keyboard accessibility. Visible ring, **2 px offset** outside the element, brand color at high contrast, *no animation in* (must appear synchronously so screen-reader and Tab users aren't disoriented). Fade out is optional [5][15].

**Brightness vs scale.** Brightness shift is the safest default for hover on a dense grid (lists, tables) — scaling causes neighbors to feel like they're moving. Reserve scale for isolated card-like targets [13].

---

## 6. State transitions

Three transition mechanisms in modern React, ordered by effort [11][17]:

1. **`AnimatePresence` (Motion)** — wrap exit-animating elements. Critical: pair with stable `key`s, and use `mode="wait"` when subsequent content must wait for exit. Use `mode="popLayout"` to let layout snap immediately while exit runs — feels faster.
2. **Layout animations (`layout` prop)** — Motion's auto-FLIP. Mark a node `layout` and any layout change (resize, reorder, reparent) animates smoothly. This is the single highest-leverage Motion feature [11][17].
3. **View Transitions API** — native browser shared-element + crossfade [17]. Tag elements with `view-transition-name`; the browser snapshots old/new and crossfades. As of 2026 it's broadly supported in Chromium and Safari. Use for route-level transitions in Electron; Motion's `layout` is still better for in-page choreography because it's interruptible mid-flight.

**Choreography rules** [6][14]:

- Enter and exit should *not* be mirror images. Enter ~250 ms with overshoot; exit ~150 ms straight ease-out.
- Stagger sibling reveals by **20–40 ms** — enough to read as ordered, short enough not to feel slow.
- The **transform-origin matters**: dropdowns scale from the trigger edge, not center. Radix exposes `--radix-popover-content-transform-origin` for this; replicate the pattern in custom popovers [7].

---

## 7. Loading + progress

The **100 ms / 400 ms / 1 s** thresholds (Doherty et al., Nielsen) [12]:

- **< 100 ms** — show nothing. Adding a spinner makes the operation *feel* slower.
- **100 ms – 1 s** — show an in-place state (button press held, subtle pulse). No modal blocker.
- **1 s – 10 s** — show a skeleton or determinate progress. Skeletons beat spinners because they convey *shape* of result [6][12].
- **> 10 s** — show progress + cancel.

**Skeleton vs spinner vs progress bar** [6][12]:

- **Skeleton** — content has predictable shape (list, card, conversation). Pulse opacity 0.5 ↔ 1.0 over ~1.5–2 s with ease-in-out, infinite.
- **Spinner** — unknown duration, unknown shape, but you owe the user a signal. Avoid for known-fast operations.
- **Progress bar** — only when you actually know progress. Linear easing here is correct.

**Optimistic UI**: apply the change locally, send the request in the background, reconcile on response. Critical for *every* sub-300 ms feeling interaction over a network [12][13]. Motion's layout animations make the reconciliation visually smooth even when the server returns a different ID/order.

**Premium feel** comes from breathing rates: ambient pulses around **0.4–0.6 Hz** (the resting breath cycle) read as "alive but calm." Anything faster than ~1 Hz reads as agitation [6][13].

---

## 8. Acknowledgement patterns

**What "yes I got that" should feel like in 2026** [6][13]:

- A single spring on **scale** (1 → 0.97 → 1) timed ~120 ms in, ~180 ms out, OR
- A subtle **shadow lift** (the element rises 1–2 px on the z-axis) on success, OR
- A brief **opacity/brightness bump** (1.0 → 1.08 → 1.0) on the affected element, OR
- For destructive confirms: a *single* color crossfade to the destructive state, no oscillation.

Always pick **one** of the above per action. Stacking them is what creates the cheap, chiropractic feel.

**Explicit anti-patterns** (this is the bar the project is enforcing) [6][7][13]:

- **Ring flashes.** A pulsing focus ring or "ripple" on every successful action — reads as a CAPTCHA, not feedback. Material's ripple is a defensible *trigger* affordance, not an *acknowledgement*.
- **Shakes.** Reserve for *errors only*, and even then prefer a single horizontal nudge over a 3× oscillation. Shake-on-success is hostile.
- **Two-stage acknowledgements.** "Did it" → "yes really did it" sequences. One signal, one spring, done.
- **Color flashes that "chirp."** Yellow → original → yellow. Sample-rate that bounce: a single soft ease.
- **Animating frequently-triggered actions** at all. If the user does it 100×/session (keyboard shortcuts, command menus, scroll), no animation [6][13]. Rauno: high-frequency + low-novelty = cognitive cost, not delight.

The modern signature: **one spring, minimal exit, no chrome**.

---

## 9. Reduced motion

`@media (prefers-reduced-motion: reduce)` exposes a user-level setting (macOS Accessibility → Display → Reduce Motion; Windows Animation effects). Roughly 1–3 % of users have it set, and the consequence of ignoring them is vestibular nausea, not just preference [15][16].

**The rule is not "no motion"** — it is "no *motion that simulates motion*" [15]:

- Replace **scale / translate / rotate / parallax** with **opacity crossfades**.
- Keep **color transitions, opacity fades, and tiny (< 4 px) translations** — these don't trigger vestibular response.
- Keep functional motion that conveys state change; cut decorative motion entirely.

**Motion config** [11]:

```ts
import { MotionConfig } from "motion/react";

<MotionConfig reducedMotion="user">
  {/* uses prefers-reduced-motion automatically */}
</MotionConfig>
```

Options: `"user"` (respect OS), `"always"` (force on, for testing), `"never"`. ARDEN should default to `"user"`. The `useReducedMotion()` hook lets components opt into custom replacements (crossfade fallback for a slide) [11].

**CSS fallback**:

```css
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: 0.01ms !important;
    scroll-behavior: auto !important;
  }
}
```

Test by toggling System Settings → Accessibility → Display → Reduce Motion, and via DevTools → Rendering → "Emulate CSS media feature prefers-reduced-motion."

---

## 10. Performance

The web compositor accelerates exactly two property categories cheaply [6][18]:

- **`transform`** (translate, scale, rotate)
- **`opacity`**

Everything else (top/left/width/height/margin/padding/box-shadow/filter) triggers layout or paint, which on a 120 Hz panel gives you ~8 ms to recompute the world. Don't do it.

**`will-change`**: a hint, not a guarantee. Use *just before* an animation starts and clear after; permanent `will-change: transform` on hundreds of nodes hogs GPU memory [18].

**`backdrop-filter` is expensive.** Per the CSS Filter Effects spec it requires a *separate rendering pass* — roughly doubling render time and GPU bandwidth where used [18][19]. Concrete ARDEN rules:

- Cap `backdrop-filter: blur()` to **one or two layers** simultaneously visible.
- Avoid blur radii above ~20 px on large surfaces; cost scales with radius × area.
- Never animate `backdrop-filter` itself; animate `opacity` on a pre-blurred layer.
- Beware containing-block traps: a backdrop-filter inside another backdrop-filtered ancestor samples the *parent*, not the page — lift it to a sibling.

**Profiling**: Chrome DevTools → Performance → record a 5 s interaction → look for green (paint) and purple (layout) bars during the animation. Anything but yellow (composite) on a transform/opacity animation means you're animating the wrong property [6][18]. Also: DevTools → Rendering → "Paint flashing" + "Layer borders."

**Frame budget**: target 60 fps (16.6 ms/frame) baseline, 120 fps (8.3 ms) on ProMotion/120 Hz displays. Electron inherits the host refresh rate — design to the highest [6].

---

## 11. References from the field

A condensed pointer list of voices to read, with the angle each is strongest on:

- **Rauno Freiberg** — *Invisible Details of Interaction Design* and *Designing Depth* [13][20]. Best on metaphor, momentum-aware gestures, when *not* to animate, layering and dimensionality.
- **Emil Kowalski** — *Great Animations* and *Good vs Great Animations* [6][7]; runs animations.dev. Best on concrete defaults (ease-out, custom curves, transform-origin), spring vs ease decision rules, anti-patterns.
- **Pasquale D'Silva** — *Transitional Interfaces* (2013) [10]. The foundational essay on motion as functional, not decorative; informs every modern motion system.
- **Material Design 3 Motion** — m3.material.io/styles/motion [4][8]. Token-level rigor: named curves, named durations, named patterns. Steal the tokens; ignore the personality.
- **Apple HIG — Motion** [5]. The "quick and precise; avoid frequent animation; make it optional" axis. Pair with WWDC sessions on SwiftUI animation for the spring-defaults philosophy.
- **Motion docs** (motion.dev) [11][17]. Authoritative on the React API, layout animations, `MotionConfig`, `useReducedMotion`.
- **Linear release notes** and the Vercel/Linear design-engineer cohort — observe, don't quote: the bar for "tasteful spring + nothing else" comes from there [13].
- **Sam Selikoff / Build UI** — pragmatic Motion tutorials, especially layout animations and `AnimatePresence` gotchas.
- **Jordan Singer, Soren Iverson, Brad Frost** — Twitter/X feeds for the *taste* axis: what's currently considered overdone vs restrained in 2025–2026.

---

## References

1. ZURB / Prototypr — *The 4 Components of a Microinteraction*. https://blog.prototypr.io/the-4-components-of-a-microinteraction-836732173c7c
2. Dan Saffer — *Microinteractions: Designing with Details* (O'Reilly, 2013). https://www.oreilly.com/library/view/microinteractions/9781449342760/
3. Cieden — *Structure of microinteractions*. https://cieden.com/book/sub-atomic/microinteractions/structure-of-microinteractions
4. Material Design 3 — *Easing and duration tokens*. https://m3.material.io/styles/motion/easing-and-duration/tokens-specs
5. Apple — *Human Interface Guidelines: Motion*. https://developer.apple.com/design/human-interface-guidelines/motion
6. Emil Kowalski — *Great Animations*. https://emilkowal.ski/ui/great-animations
7. Emil Kowalski — *Good vs Great Animations*. https://emilkowal.ski/ui/good-vs-great-animations
8. Material Design 3 — *Motion overview / specs*. https://m3.material.io/styles/motion/overview/specs
9. Maxime Heckel — *The physics behind spring animations*. https://blog.maximeheckel.com/posts/the-physics-behind-spring-animations/
10. Pasquale D'Silva — *Transitional Interfaces* (2013). https://medium.com/@pasql/transitional-interfaces-926eb80d64e3
11. Motion — *React transitions / spring config / reducedMotion*. https://motion.dev/docs/react-transitions
12. Laws of UX — *Doherty Threshold*. https://lawsofux.com/doherty-threshold/
13. Rauno Freiberg — *Invisible Details of Interaction Design*. https://rauno.me/craft/interaction-design (mirror: https://every.to/p/invisible-details-of-interaction-design)
14. Uxcel — *12 Principles of Animation for Motion Design*. https://uxcel.com/blog/12-principles-of-animation-a-guide-to-motion-design-133
15. MDN — *@media/prefers-reduced-motion*. https://developer.mozilla.org/en-US/docs/Web/CSS/@media/prefers-reduced-motion
16. web.dev — *prefers-reduced-motion: Sometimes less movement is more*. https://web.dev/articles/prefers-reduced-motion
17. Motion — *React layout animations (FLIP & shared element)*. https://motion.dev/docs/react-layout-animations  ·  MDN — *View Transition API*. https://developer.mozilla.org/en-US/docs/Web/API/View_Transition_API
18. dev.to — *Costly CSS Properties and How to Optimize Them*. https://dev.to/leduc1901/costly-css-properties-and-how-to-optimize-them-3bmd
19. CSS Filter Effects Module Level 2 — backdrop-filter rendering pass cost. https://drafts.fxtf.org/filter-effects-2/
20. Rauno Freiberg — *Designing Depth*. https://rauno.me/craft/depth
