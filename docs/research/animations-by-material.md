# Animations by Material: Glass vs Linen

> Glass is a living, refractive medium — its motion is atmospheric (parallax, specular drift, tint pulse, no scale-from-zero); Linen is paper-on-paper — its motion is architectural (shadow lift, scale, FLIP reshuffles, no shimmer-of-light) [1][2][3][4]. The general principles in [`microactions.md`](./microactions.md) still apply; this doc is only what *changes* per material.

ARDEN ships both materials as user-selectable surfaces. Motion has to feel native to each — the same spring constants and durations, but different *what* moves and what doesn't. Anti-patterns differ too: a tint pulse that sells a glass card as alive will look like a bug on linen; a shadow lift that grounds a linen card will look flat and lifeless under glass.

---

## GLASS

Glass is Apple's Liquid Glass language: a digital meta-material that combines real-time blur, depth-based refraction, dynamic specular highlights and continuously shifting tint to keep the surface legible against whatever is beneath it [1][2][3]. The motion design problem is that the surface itself is doing visual work even when nothing is animating — the choreography has to *cooperate* with refraction instead of fighting it.

### 1. What works with translucency

- **Background parallax.** Because content behind the glass is visible (blurred, tinted, but visible), moving the *background* a few px while the glass surface stays put creates instant depth. Vision Pro app icons do exactly this — layers drift at different rates on gaze/hover [4][5]. ARDEN rule: on hover of a glass card over a scrollable region, translate the *backdrop content* by 2–4 px on the cursor axis (parallax factor 0.04–0.08).
- **Specular / rim drift.** A 1 px inner highlight whose angular position tracks the pointer (or device tilt) is the single highest-leverage glass micro-motion. Apple's spec: "highlights move with device motion, reinforcing realism and depth" [1][2]. Implement as a conic-gradient or SVG `feSpecularLighting` whose light source position is bound to pointer x/y [6][7]. Cap response at ~60 fps with `requestAnimationFrame`, ease the light position with a stiff spring (`stiffness: 220, damping: 28`) so it lags the cursor by ~80 ms — chasing 1:1 reads as jittery.
- **Subtle warp on press.** Liquid Glass "flexes and morphs… simulating a thicker material with deeper shadows and more pronounced lensing" [2]. On the web this is an SVG displacement map (`feTurbulence` + `feDisplacementMap`) ramped from 0 → ~6 on press, easing back over ~220 ms [6][8][9]. Keep the displacement scale single-digit; anything above ~10 reads as a broken filter.
- **Tint pulse on background change.** When the content behind the glass changes (route swap, image load), pulse the surface's internal tint by ±4 % luminance over ~400 ms ease-out. This sells the surface as *sampling* the new background rather than statically painted.

### 2. What to avoid

- **Never animate `backdrop-filter` itself.** Each frame triggers a separate rendering pass; the CSS Filter Effects spec describes it as roughly doubling render time per backdrop-filtered layer [10][11][12]. Concrete cost from shadcn-ui issue #327 and Mozilla bug 1718471: animating `backdrop-filter: blur(Npx)` collapses to single-digit FPS within a handful of stacked elements [13][14]. **Always** animate a sibling `opacity` on a pre-blurred layer instead [11][12].
- **Don't blur the blur.** Stacking two backdrop-filtered layers samples the parent, not the page — the inner one filters already-filtered pixels and produces mud [15]. Lift the second glass surface to a sibling of the first.
- **No hard cuts.** Glass is continuous matter; instant appearance/disappearance breaks the metaphor. Minimum 120 ms crossfade even on the fastest interactions.
- **No scale-from-zero entries.** At ~0.2× the blur reveals nothing meaningful — the surface looks like a smudge growing into something. Glass surfaces should enter at ≥ 0.92 scale or not scale at all.
- **No big blur radii on large surfaces.** Cost scales with radius × area; keep `blur()` ≤ 20 px on anything larger than a card [11][12].

### 3. Entry / exit choreography

The Apple pattern is "shoot through a surface" — content seems to emerge *from within* the glass rather than be drawn on top [1][2]. Recipe:

- **Modal in:** opacity 0 → 1 over 220 ms (`emphasized-decelerate`, `cubic-bezier(0.05, 0.7, 0.1, 1.0)` [16]), scale **0.96 → 1.0**, *no blur ramp*. The glass surface is rendered pre-blurred and faded in.
- **Content in:** child content opacity 0 → 1, y `+6 → 0`, *delayed by 80–120 ms* so the glass arrives first and the content surfaces through it.
- **Modal out:** opacity 1 → 0 over 140 ms `ease-out`, scale stays at 1.0 (no shrink) — glass should "evaporate," not "collapse."
- **Backdrop dim:** the scrim *behind* the glass animates separately: `rgba(0,0,0,0)` → `rgba(0,0,0,0.35)` over 260 ms. Dimming the scene is what creates the floating sensation [17].

```ts
const glassEnter = { duration: 0.22, ease: [0.05, 0.7, 0.1, 1.0] };
const glassExit  = { duration: 0.14, ease: [0.3, 0.0, 0.8, 0.15] };
```

### 4. Hover / press feedback

What changes on glass vs a generic surface:

- **Hover** — rim brightness shift (inner border opacity 0.4 → 0.7) + internal tint nudge (+3 % luminance), 140 ms ease-out. **No** scale on hover — desktop pointers don't need a hit-area cue, and scaling a refractive surface looks like a parallax error [18][19].
- **Press** — scale **0.98** + rim brightness *down* (the highlight "flattens"), 90 ms in / 160 ms spring out (`stiffness: 380, damping: 30`). Optionally: a one-shot specular sweep (highlight sweeps across the rim over ~280 ms) on success.
- **Focus** — 2 px outer ring at brand color, *synchronous* (no fade-in for a11y), with an additional 1 px inner glass-rim glow at 60 % opacity so the ring reads as part of the material, not glued to it.

The reference: Liquid Glass on hover "brightens"; on press the bevel "flattens and the shadow deepens" [7][9]. Translate that to: rim ↑ on hover, rim ↓ on press, shadow ↓ on press.

### 5. Loading / breathing

Spinner-on-glass reads as decoration on decoration — the surface is already moving (specular, tint). Use instead:

- **Tint pulse.** Surface tint cycles ±3 % luminance over 2.0 s ease-in-out, infinite. Maps to the "breathing rate" 0.4–0.6 Hz from microactions §7.
- **Rim drift.** The specular highlight slowly orbits the rim (one revolution per ~6 s) — communicates "alive, waiting."
- **Never shimmer.** A linear-gradient sweep across a translucent surface fights the backdrop; the sweep highlights *both* the surface and the content behind, producing a double-image artifact [20]. Save shimmer for linen.

### 6. Layout shifts behind glass

Because content *behind* the glass is partly visible, any layout reshuffle in the layer beneath is exaggerated by refraction — small changes look big. Rules:

- Animate the underlying layer's layout via Motion's `layout` prop with `transition={{ type: "spring", stiffness: 260, damping: 30 }}` so movement is *continuous* (springs absorb velocity; eases create discontinuities under blur) [21].
- **Stagger by 30–40 ms.** Through glass, simultaneous motion of multiple items looks like a single blurry shimmer; staggering separates them perceptually [17].
- Avoid layout shifts of size > ~30 % of the glass surface area mid-interaction — the user loses context. If you need a big reshuffle, *fade the glass briefly to higher opacity* (mask the chaos) during the move.

### 7. Glass references

Apple's WWDC25 "Meet Liquid Glass" [1], Apple Newsroom announcement [2], visionOS Materials and "Designing for visionOS" [4][5], `rdev/liquid-glass-react` [7], `liquidglassui.org` [9], kube.io's CSS+SVG refraction breakdown [6], LogRocket "Liquid Glass with CSS and SVG" [8], Frontend Masters "Liquid Glass on the Web" [22], Atlas Pup Labs "Liquid Glass but in CSS" [23], Apple HIG Materials [3]. For motion specifically, Apple HIG Motion [24] and Rauno's *Designing Depth* on dimming/parallax/stagger as depth signals [17].

---

## LINEN

Linen is the opposite metaphor: opaque, slightly-textured paper surfaces stacked on a desk. No backdrop sampling, no specular, no refraction. Visual hierarchy comes from shadow + tonal elevation. Material 3's "tonal surfaces over heavy shadows" + Vercel Geist's elevation-as-role model is the playbook [25][26][27].

### 1. What works with solid surfaces

- **Shadow lift.** The signature linen interaction: hover/press shifts elevation. Material 3 cards use ambient + key shadow pairs at named elevation tokens; Geist encodes elevation in a `Material type` (`base`, `small`, `large`, `tooltip`, `menu`, `modal`, `fullscreen`) [27]. ARDEN rule: hover transitions `box-shadow` from elevation-1 → elevation-2 over 140 ms ease-out.
- **Clean scale transforms.** Solid cards take scale gracefully — there's no refraction to misalign. Press scale 0.97, hover scale 1.00 (no growth on hover on a list; reserve hover-scale 1.02 for isolated cards) [18][19].
- **Container transform.** Material 3's flagship pattern: a tappable element seamlessly morphs its bounds into the destination container [25][28]. Works on linen because the surface is opaque — the morphing rectangle reads as a single object reshaping. (On glass, the same animation looks like two filters cross-fading.) Duration 300–400 ms `emphasized` easing `cubic-bezier(0.2, 0.0, 0, 1.0)` [16][25].
- **Tonal background shift.** Hover lightens the surface fill by one tonal step (e.g., `--surface` → `--surface-hover`, +3–5 % luminance). This is the linen equivalent of glass's tint pulse — but it's a *static* state change, not a breathing pulse.

### 2. What to avoid

- **No fake translucency.** A `linear-gradient` fade-in to mimic blur reads as "almost glass, not quite" — and breaks the linen mental model. If you want depth, use shadow.
- **No specular / rim-light pulses.** Linen has a *hairline border* (1 px inner ring at low opacity), not a light-catching rim. Pulsing it as if light were moving over the surface reads as a glitch.
- **No breathing.** Linen is inert; tint-pulse loops look like a sync-failed shimmer. Loading states are skeleton-based, not surface-based.
- **No backdrop parallax.** Nothing is visible behind a linen surface — moving the layer beneath has no payoff.
- **Don't overuse shadow.** Geist's guidance: "favor the lowest elevation that still reads as elevated against its background; over-elevating is a common source of visual noise" [27].

### 3. Entry / exit choreography

- **Modal in:** scale **0.95 → 1.0** + opacity 0 → 1, 260 ms `emphasized` `cubic-bezier(0.2, 0.0, 0, 1.0)` [16]. Scale-from-small works here because a small opaque rectangle still reads as a panel.
- **Modal out:** scale 1.0 → 0.97 + opacity 1 → 0, 150 ms `emphasized-accelerate` `cubic-bezier(0.3, 0.0, 0.8, 0.15)` [16][29]. Out is ~40 % faster than in.
- **Container transform** instead of fade for navigations between two anchored surfaces (e.g., card → detail). 320 ms, shared element via View Transitions API or Motion's `layoutId` [21][25][28].
- **Drawer / sheet:** iOS-style 500 ms with the Ionic easing curve `cubic-bezier(0.32, 0.72, 0, 1)`, drag-dampened past edges (Vaul pattern) [30]. The same applies to glass drawers but the drag rubber-band is a *linen* affordance — paper resists, glass would deform.

### 4. Hover / press feedback

- **Hover** — shadow elevation +1 step (e.g., elevation-1 → elevation-2) + tonal +3 % background, 120 ms ease-out. No scale on dense lists; +2 px translateY on isolated cards if and only if the card is a primary CTA.
- **Press** — scale 0.97 + shadow elevation **-1 step** (the card "presses into" the surface), 80 ms in / 180 ms spring out (`stiffness: 380, damping: 30`).
- **Focus** — 2 px outer ring, brand color, synchronous, no glow.

The shadow direction matters: on hover the card *rises* (shadow softens + extends), on press it *settles* (shadow tightens + shortens). This is the "ambient + key light" pair shifting — a real-world physics cue [25][26].

### 5. Loading / breathing

- **Skeletons + shimmer.** Shimmer works on linen because the surface is opaque — the linear-gradient sweep doesn't fight a backdrop, and `background-attachment: fixed` keeps multiple skeleton blocks in phase [20]. Recipe: surface fill `--skeleton`, overlay a 180°-rotated transparent → 8 % white → transparent gradient, animate `background-position` 1.6 s ease-in-out infinite.
- **Skeleton-of-shape**, not generic boxes (Emil's rule [18][19]): the skeleton should match the final layout's bounding boxes so the FLIP from skeleton → real content is geometric, not a swap.
- **Progress bar** for known durations; linear easing is correct here.

### 6. Layout shifts

Solid surfaces tolerate large reshuffles because every card retains its own outline through the animation — the user can track individual items. This is why Linear's list reorders, project switches, and inline expansions feel calm: FLIP under `transform`, springs for the interpolation, no opacity tricks [21][31].

- **Motion `layout` prop** on every list item. Spring `stiffness: 260, damping: 30, mass: 1`.
- **Stagger 20–30 ms** between siblings — shorter than glass (no refraction blur means individual motion reads instantly).
- **View Transitions API** for route-level shared elements; Motion's `layoutId` for in-page shared element [21][28].
- Linear's pattern: when an item is created mid-list, push siblings *outward* with a spring; new item fades in from opacity 0 at its final layout position (don't translate-from-edge, which fights the FLIP).

### 7. Linen references

Material 3 Motion overview & transitions [16][25][28][29], MaterialContainerTransform reference [32], Geist material/elevation [27], Vercel design philosophy (spring + damping 200) [33], Emil Kowalski's *Good vs Great Animations* and *Building a Drawer Component* [18][19][30], Vaul (Ionic curve, 500 ms drawer, drag damping) [30], Motion layout animations docs [21], Sam Selikoff's `AnimatePresence` + `layout` tutorials referenced throughout the field.

---

## CROSS-CUTTING

### The same interaction on both materials

| Interaction | Glass | Linen |
|---|---|---|
| Button hover | Rim brightness +30 %, tint +3 % luminance, 140 ms | Shadow elevation +1, fill +3 % luminance, 120 ms |
| Button press | Scale 0.98 + rim brightness ↓ + 1-shot specular sweep | Scale 0.97 + shadow elevation -1 |
| Card lift on hover | *Don't* — parallax the backdrop content instead | Translate Y -2 px + shadow elev +1 |
| Modal enter | Opacity 0→1, scale 0.96→1, content delayed 100 ms | Opacity 0→1, scale 0.95→1, no content delay |
| Modal exit | Opacity 1→0, scale stays at 1.0 (evaporate) | Opacity 1→0, scale 1→0.97 (collapse) |
| Loading | Tint pulse 2 s + rim drift 6 s | Skeleton + shimmer 1.6 s |
| Background change | Tint pulse acknowledging new content | No-op (no backdrop visible) |
| Route transition | Crossfade glass surface, swap content behind | Container transform / shared element |
| List reorder | FLIP with extra stagger (30–40 ms) | FLIP with tight stagger (20–30 ms) |

### Shared primitives

These come straight from [`microactions.md`](./microactions.md) §3-4 and apply identically to both materials:

```ts
// Both materials
const press         = { type: "spring", stiffness: 380, damping: 30 };
const layoutSpring  = { type: "spring", stiffness: 260, damping: 26 };
const hoverEase     = { duration: 0.14, ease: [0.16, 1, 0.3, 1] };   // ease-out-expo
const exitEase      = { duration: 0.15, ease: [0.3, 0, 0.8, 0.15] }; // m3 accelerate

// Material-specific entry curves
const glassEnter    = { duration: 0.22, ease: [0.05, 0.7, 0.1, 1.0] }; // m3 decelerate, slower
const linenEnter    = { duration: 0.26, ease: [0.2, 0.0, 0, 1.0]   }; // m3 emphasized
```

Material 3 easing tokens [16] are the shared vocabulary; only entry duration and scale-from differ.

### When to deviate

Deviate from shared primitives when the material's character is being violated:

- **Glass + scale-from-0.7 modal** — looks like a smudge growing. Deviate to scale-from-0.96.
- **Glass + skeleton shimmer** — fights backdrop. Deviate to tint pulse.
- **Glass + hover scale** — refraction parallax error. Deviate to backdrop parallax.
- **Linen + breathing pulse** — looks like a sync bug. Deviate to a static hover state and skeleton-on-load.
- **Linen + rim brightness pulse** — looks like a render glitch. Deviate to shadow elevation change.
- **Linen + 80 ms content-delayed entry** — feels lagged (nothing visual is happening in the gap; glass at least has its surface). Deviate to a single 260 ms entry of surface+content together.

If a single animation has to work across both (a shared component), drive the *property* from the material token: `--motion-feedback` = `tint-pulse` on glass, `shadow-lift` on linen, with the same `transition` timing token but different target properties. Don't try to write motion that's "neutral" — neutrality usually means it sells neither material.

---

## References

1. Apple — *Meet Liquid Glass*, WWDC25 session 219. https://developer.apple.com/videos/play/wwdc2025/219/
2. Apple Newsroom — *Apple introduces a delightful and elegant new software design*, 2025-06. https://www.apple.com/newsroom/2025/06/apple-introduces-a-delightful-and-elegant-new-software-design/
3. Apple HIG — *Materials*. https://developer.apple.com/design/human-interface-guidelines/materials
4. Apple HIG — *Designing for visionOS*. https://developer.apple.com/design/human-interface-guidelines/designing-for-visionos
5. LinkedIn / Amos Gyamfi — *Making a visionOS App Icon and 3D Parallax Effects*. https://www.linkedin.com/pulse/creating-layered-images-parallax-artwork-visionos-amos-gyamfi-kf5if
6. kube.io — *Liquid Glass in the Browser: Refraction with CSS and SVG*. https://kube.io/blog/liquid-glass-css-svg/
7. GitHub — `rdev/liquid-glass-react`. https://github.com/rdev/liquid-glass-react
8. LogRocket — *How to create Liquid Glass effects with CSS and SVG*. https://blog.logrocket.com/how-create-liquid-glass-effects-css-and-svg/
9. *Liquid Glass UI* component library. https://liquidglassui.org/
10. CSS Filter Effects Module Level 2 — backdrop-filter rendering. https://drafts.fxtf.org/filter-effects-2/
11. F22 Labs — *How CSS Properties Affect Website Performance*. https://www.f22labs.com/blogs/how-css-properties-affect-website-performance/
12. Roberto Moreno Celta — *The Real Cost of Animations: Performance Budget vs. User Delight* (2025-10). https://robertcelt95.medium.com/the-real-cost-of-animations-performance-budget-vs-user-delight-227199cf5d27
13. GitHub — `shadcn-ui/ui` issue #327 *CSS Backdrop filter causing performance issues*. https://github.com/shadcn-ui/ui/issues/327
14. Mozilla Bugzilla 1718471 — *backdrop-filter: blur is laggy when many elements are rendered*. https://bugzilla.mozilla.org/show_bug.cgi?id=1718471
15. ARDEN project lessons — *backdrop-filter containing block*. (internal memory)
16. Material Design 3 — *Easing and duration tokens*. https://m3.material.io/styles/motion/easing-and-duration/tokens-specs
17. Rauno Freiberg — *Designing Depth*. https://rauno.me/craft/depth
18. Emil Kowalski — *Good vs Great Animations*. https://emilkowal.ski/ui/good-vs-great-animations
19. Emil Kowalski — *Great Animations*. https://emilkowal.ski/ui/great-animations
20. Neciu Dan — *Build your own shimmer skeleton that never goes out of sync*. https://neciudan.dev/lets-build-dynamic-shimmer-skeletons
21. Motion — *React layout animations (FLIP & shared element)*. https://motion.dev/docs/react-layout-animations
22. Frontend Masters — *Liquid Glass on the Web*. https://frontendmasters.com/blog/liquid-glass-on-the-web/
23. Atlas Pup Labs — *Liquid Glass, but in CSS*. https://atlaspuplabs.com/blog/liquid-glass-but-in-css
24. Apple HIG — *Motion*. https://developer.apple.com/design/human-interface-guidelines/motion
25. Material 3 — *Transitions*. https://m3.material.io/styles/motion/transitions
26. Material 3 — *Motion research: container transform*. https://m3.material.io/blog/motion-research-container-transform
27. Vercel Geist — *Material* (elevation roles, shadow tokens). https://vercel.com/geist/material
28. MDN — *View Transitions API*. https://developer.mozilla.org/en-US/docs/Web/API/View_Transition_API
29. Material 3 — *Motion overview / specs*. https://m3.material.io/styles/motion/overview/specs
30. Emil Kowalski — *Building a Drawer Component* (Vaul). https://emilkowal.ski/ui/building-a-drawer-component
31. Motion — *React transitions*. https://motion.dev/docs/react-transitions
32. Android Developers — *MaterialContainerTransform reference*. https://developer.android.com/reference/com/google/android/material/transition/MaterialContainerTransform
33. Vercel — *Geist Style* showcase (spring damping 200 default). https://style-psi.vercel.app/
