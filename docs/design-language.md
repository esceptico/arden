# ntrp Design Language — "Soft Precision"

Working name; rename freely. **Vercel's statics, softer physics.** The
resting frame could pass for Linear/Geist — neutral ink ramp, hairline
shadows, dense rows. The moment anything moves it stops being Vercel:
surfaces settle on springs instead of snapping, content rises into focus
through a small blur instead of popping, exits dissolve. Precision in the
pixels, softness in the motion.

**Candidate thesis (being prototyped, not yet law):** a precision
instrument — *soft optics, mechanical works*.

Content is optics, and **focus is spatial — fog of war**: the boundary of
attention is always soft. Nothing hard-clips out of view; it falls out of
focus. Scroll edges fade progressively (ScrollFade), rolling digits soften
at their window's edge, the not-yet-resolved arrives by sharpening out of
blur, long work sharpens with progress. **Blur means defocus — never
grain, never glyph churn** (tried, rejected: too literal).

Feedback is physical but **smooth** — weight and momentum, never clicks.
Detents, ticks, recoils, and back-out "click" pulses were tried and
rejected (too aggressive): a charge fills continuously, a release drains
back on the smooth-out curve, a digit rolls on one ease and sharpens as
it lands. Feedback lives in the motion itself and in in-place receipts,
not in impulses. Always interruptible — a release mid-charge retargets
from wherever it is. The monochrome default is the same instinct applied
to color.

**Confirmed so far:** (1) the wind-up/charge — hold-to-arm drawn on **the
button's own border as the charge bar** (SVG path perimeter trace — a
`<path>`, not `<rect>`: Safari ignores pathLength on rects), filling
smoothly while held, draining back on release; the control is the
progress and the receipt — no external bar, no dialog; (2) in-place
button confirmation (icon morph + label roll — see Component idioms), the
user's own pre-existing pattern, now canon; (3) the rolling-number
formula — smooth roll + motion blur while rolling + a window exactly one
line tall (only the current digit visible at rest) with a tight edge fade
so a digit in motion dissolves instead of being cut. Other candidates
live in the motion-lab artifact; whichever survive taste get promoted and
this paragraph loses the word "candidate".

This doc is the contract for new UI — human or Claude. When a change
contradicts it, either fix the change or amend the doc; never silently fork
the language. Tokens live in `apps/desktop/src/styles.css` (@theme) and
`apps/desktop/src/lib/tokens/motion.ts`; those files are the source of
truth for values, this doc is the source of truth for intent.

**The ecosystem** (the personal-brand layer): three properties share one
throughline — *measurement*. ntrp (this doc): monochrome instrument, blur
optics, smooth feedback. `~/src/interaction-lab`: the motion sketchbook —
one file per study, Geist neutrals, a rationed vermilion accent; new
signature moves land there first as studies (BorderCharge,
InstrumentRuler, FocusProgress). `~/src/website`: blueprint identity —
grid paper, JetBrains Mono, WCAG-tuned grays, blue accent. Different
skins, same instinct: interfaces as precise instruments, measured rather
than decorated.

## Principles

1. **Calm surface, alive touch.** At rest the UI is quiet and neutral.
   Feedback happens on interaction, not as ambient decoration.
2. **Motion clarifies change, never decorates.** Every animation answers
   "what changed and where did it come from". No ring flashes, shakes,
   celebrations, or two-stage acknowledgements.
3. **Compactness is spacing, not font size.** Dense rows at a readable
   14px, with air *between* groups. Never shrink type to fake density.
4. **Tone over lines.** Grouping and selection read through tonal fills
   and elevation, not borders. Borders are for real edges (inputs, panels).
5. **One of everything.** One accent, one nested radius, one timing
   language, one primitive per pattern. A second variant needs a reason
   written down.

## Color

- **The default ntrp is monochrome.** Ink on paper; accent palettes are
  opt-in. Any new surface must read perfectly in pure black-and-white
  first — if a design only works with an accent, it's leaning on color to
  do hierarchy's job. (This is the most ntrp-specific color decision;
  protect it.)
- Neutral ink ramp for everything textual: `ink → ink-soft → muted →
  faint → whisper`. Pick the dimmest tier that still reads.
- **Single accent at most** (`--color-accent`; Geist blue as the stock
  palette) for links, focus rings, active states, primary actions.
  Surfaces, text, code, and status stay neutral — accent never tints
  containers. Verify accent UI in an accented theme (notion-dark leaves
  accent vars empty and renders it grey).
- Status = `ok / warn / bad` + their `-soft` washes, conveyed through a
  small dot, badge, or pip. Never a colored rail, stripe, or full-row tint.
- Fills that must survive both themes are ink-derived:
  `color-mix(in oklab, var(--color-ink) N%, transparent)` (hover ≈ 4–7%,
  selection ≈ 7–10%). Theme-aware by construction — no dark overrides.
- Verify color work by hex/computed style, not by eye — light-mode token
  collisions (line-soft on sunken) have shipped invisible borders before.

## Surfaces & elevation

- FF 8-level ladder (`--surface-N` paired 1:1 with `--shadow-N`); dark
  climbs additively from `#0f0f0f` — never a plain color flip.
- **Decouple tone from shadow-tier**: anchored chrome (sidebars, rails)
  sits low with a deep shadow; floating surfaces (modals, popovers) lift.
- Shadows are hairline ring + soft drops at ≤8% opacity. If a shadow is
  visible as a shadow, it's too strong.
- No specular/gradient rims on anchored surfaces — that physics belongs to
  floating cards only. No glass-on-glass nesting (backdrop-filter samples
  the parent; lift overlays to siblings).

## Type, space, radius

- Geist Variable / mono. Base body 14px; the full scale is 8 tiers in
  `--text-*` — new sizes go in the scale, never inline.
- Tabular numerals for anything that counts or ticks.
- Radius: **8px (`rounded-lg`) is the house radius**, nested concentrically
  (outer 16 → inset 8). Full capsule is reserved for the segmented-control
  track/pill idiom (transitions.dev) — don't spread it further.
- Icon-column grids for label alignment (`.settings-nav-row` pattern):
  labels align by construction, not by eyeballed padding.

## Motion

The core differentiator. Statics say Vercel; motion says softer.

- **Enters spring, exits tween.** Entrances use the `SPRING_*` tokens
  (confident settle, at most a hint of overshoot). Exits are plain tweens
  one duration tier *quicker* than their entrance, no bounce — dismissal
  reads crisp and final (`withExit`, `EXIT_FAST/EXIT_ROW`).
- **Blur-dissolve vocabulary** for content: `RISE_IN` (+6px, blur 3px) in,
  `DISSOLVE_OUT`/`ROW_EXIT` out. Blur ≤4px, content-sized elements only.
  Blur bridges crossfades (BlurSwap, synchronized — never `mode="wait"`
  gaps); intentional slides stay sharp.
- **Blur is always continuous — no exceptions.** Blur must ramp in and out
  over time (≥150ms each way, eased); it never snaps on, never cuts off,
  never appears pre-applied on an element the user watched arrive. If a
  state change needs blur, it needs a blur *transition*. (User-decreed
  hard rule, 2026-07-04.)
- **Traveling indicators are CSS tweens**, not springs: one persistent
  element, measured offsets written inline, 250ms
  `cubic-bezier(0.22, 1, 0.36, 1)` on transform+width (transitions.dev).
  Never transition height. A fresh element snaps — it never animates in
  from 0×0.
- **Duration grid**: 150 state · 200 popover/panel · 250 traveling
  indicator/page slide · 300 route. UI motion stays ≤300ms.
  *Pacing lesson (2026-07-04, motion lab — confirmed twice):* the user
  reads grid-fast motion as "too fast" even after a first slowdown. For
  expressive moments (swaps, rolls, reveals) the comfort zone is a
  **400–600ms settle** — springs around stiffness 170–220 / damping
  22–26, optics tweens 380–450ms. The fast tiers are for chrome
  (hover tints, focus rings, dismissals), not for moments meant to be
  felt. When in doubt, start slow and let taste speed it up.
- **Ease roles**: `--ease-out-soft` (0.2, 0.8, 0.2, 1) is the default for
  micro-transitions; smooth-out (0.22, 1, 0.36, 1) drives traveling
  indicators and slides; `--ease-emphasized` for route/pane slides;
  `--ease-back-out` only for physical toggles. Never `ease-in`, never
  `transition: all`.
- **Frequency ladder**: actions used dozens of times a day get minimal or
  no animation; keyboard-initiated actions get none, ever. Delight is for
  rare moments.
- GPU properties only (`transform`, `opacity`, small `filter: blur`).
  Grid-rows reveals for collapses, popLayout for list removals. If it
  animates layout properties per frame, redesign it.
- Every animated surface honors `prefers-reduced-motion` (springs →
  duration 0, CSS guard blocks).
- Motion is verified in the running app, frame-timed if in doubt —
  never from static screenshots.

**Candidate — instrument graphics** (lifeline.evilrabbit.com is the
reference the user loves): time and quantity render as monochrome rulers
and graduation ticks — hairline marks, mono tabular numerals, events as
ink marks — never bar charts. **Density is not precision**: a dotted line
carries the measurement texture; real ticks exist only where labels are
(dense multi-rank tick ladders read as a piano roll — tried, rejected). Combined with fog-of-war it becomes ours:
tick brightness and labels resolve near the pointer, soften at the
periphery. Ticks are geometry, not impulses — precision lives in the
statics while motion stays smooth. Candidate homes: session activity,
automation run history, memory timeline, context usage.

## Component idioms

- **Selection travels; hover is proximity.** Selected state is a quiet
  tinted pill/underline that slides between options; list hover is the
  shared traveling proximity highlight — never per-row background flips.
- **Dense marks get a liquid proximity field.** When rows shrink to marks
  (a minimap rail, a tape of ticks), the hover highlight becomes a 2D
  gaussian field: vertical falloff shapes the bell, and the cursor's
  horizontal approach scales its amplitude — the marks begin to swell
  BEFORE the pointer arrives and relax as it drifts away, with a hard
  engagement limit (anchored where the marks anchor) so far cursors cost
  nothing. Per-frame writes, transition disabled while live so the tween
  never fights the pointer; eased ramp back on leave. ONE label travels
  between marks, blur-ramping in and out, and it commits only near full
  field strength (with hysteresis) — never a per-mark tooltip flip. The
  label rides on the app's popover surface (a pill, not bare glyphs), so
  it stays legible over whatever it floats above. At rest the marks carry
  a brightness ladder that mirrors the viewport: reading position = ink,
  on-screen = mid, off-screen history recedes — the rail is a minimap,
  not just a position dot. Reference: ChatRail (ntrp), Rail Nav (lab).
- **Focus** is a visible accent ring (`focus-visible`), concentric with
  the control's radius.
- **Every list designs its empty, loading, error, and searching states.**
  Skeletons pulse subtly and reveal via crossfade+blur; errors offer Retry.
- Buttons press (`scale ~0.97` spring-tap); labels never shift weight in a
  way that moves siblings (reserve width with a ghost if they must).
- **The control is the confirmation.** Actions that complete at the point
  of click confirm in place, never with a toast: the button's own glyph
  morphs to a check (crossfade + scale, in place) while the label rolls
  ("Copy" → "Copied") inside a fixed-width clip with a soft mask — one
  coordinated signal, holds ~1.2s, reverts. Toasts are reserved for
  effects that land somewhere else on screen.
- Modals grow from their trigger's direction (clamped ~64px, `POSE_MODAL`);
  popovers are origin-aware; tooltips fade fast and skip delay once one is
  open.
- Headers with two title mechanisms share ONE grid row (aligned by
  construction); modal header geometry has a single source
  (`.modal-header`).

## Voice

Casual but polite microcopy. Sentence case. No jargon the user didn't
introduce. Hints explain consequences ("Stored locally; encrypted with
safeStorage when available"), not mechanisms.

## Process rules (for any author, human or Claude)

1. **Grep before building.** The primitive probably exists (Tabs, Badge,
   IconButton, PageModal, ProximityHighlight, ListColumn, Collapse…).
   Extend it; never fork a parallel one.
2. **Fix the class, not the instance.** A flaw you can see is a symptom —
   audit the whole codebase for the pattern, fix it in the shared
   primitive/token, then verify each site.
3. **New values go through tokens.** No inline hex, no hand-written
   `{ duration }`, no bespoke ease. If the scale lacks a tier, add the
   tier.
4. **Faithful reproduction.** When adopting an external component
   (transitions.dev, FF), port its mechanism at its crafted bar —
   token-map the colors, keep the tuned values verbatim.
5. **Verify against pixels** in the running app, both themes, before
   calling it done.

## Anti-patterns (instant reject)

`transition: all` · `ease-in` · animating from `scale(0)` or `0×0` ·
height/width/padding tweens per frame · colored status rails · accent-tinted
surfaces · borders as grouping · shadows >8% · two "smooth-out" curves for
the same role · decorative shimmer on anchored chrome · animation on
keyboard-repeatable actions · shrinking fonts for density · watered-down
ports of crafted components · click/detent impulses (scale pulses,
recoils, stepped ratchets — smoothness won) · grain or glyph-churn as
"noise" (noise is blur) · blur that snaps instead of ramping · toasts for
actions that complete at the click point.
