# Glass Material — Design Reference

**Summary.** Glass works as a *functional* layer that signals "this floats above content" — best on transient chrome (modals, sidebars, popovers, navigation), worst on dense reading surfaces. A credible glass recipe is a stack (blur + tint + saturation + noise + rim + shadow), not a single `backdrop-filter`, and every parameter must have a fallback for accessibility (`prefers-reduced-transparency`, increased contrast) and performance (blur is GPU-expensive and stalls compositors at large radii).

---

## 1. When Glass Works vs When It Doesn't

Glass exists to convey **depth, hierarchy, and context** — letting content peek through so the user keeps spatial orientation when an overlay appears. Apple frames it as "a new functional layer in the UI, floating above your content to bring structure and clarity, without ever stealing focus" [1][6]. Linear's recent rebuild echoes this: glass adds "translucency, depth, and physicality" but must remain "purpose-built, disciplined, and designed for sustained focus" [7].

**Works well on:**
- **Navigation chrome** — tab bars, toolbars, sidebars that overlay scrolling content [3][1].
- **Transient surfaces** — popovers, context menus, command palettes, tooltips, light-dismiss flyouts. Microsoft explicitly reserves Acrylic for these [3].
- **Modals/sheets/drawers** — where the user benefits from seeing the parent context behind the panel [8][6].
- **Small chips, tabs, badges** layered over rich backgrounds [8].

**Backfires on:**
- **Dense reading content** — paragraphs, docs, long lists. The variable contrast under blur fights legibility [9][14].
- **Data tables and forms** — small text and numeric alignment need stable, high-contrast surfaces; glass adds chromatic noise that kills scannability [8][14].
- **Vertical app shells / "main content area"** — Microsoft recommends solid opaque backgrounds for primary panes; only use translucent material on overlays [3]. Linear deliberately omitted Liquid Glass refraction from dense pro views because it "can make dense professional interfaces harder to read" [7].
- **Flat / single-color backdrops** — glass over a solid color shows nothing through; the effect collapses and reads as a decorative outline [14][13].

**Rule of thumb:** if the surface owns the user's attention for more than a few seconds, it should not be glass.

---

## 2. Parameters and Their Behavior

A real glass material is a **layered recipe**, not one property. Microsoft's published Acrylic recipe is the clearest published stack: *background → blur → exclusion blend → color/tint overlay → noise* [3]. Linear's SwiftUI version: *Gaussian blur base → gradient for structure → specular highlight* [7]. Setproduct's guide formalises this as *outer shell (rim + highlight) → inner stabilised plate (protects text) → locked light direction* [8].

### Blur radius
- **Perceptual sweet spot: 8–40 px.** Tailwind/shadcn community guidance: start at `blur(8px)` with `rgba(255,255,255,0.10)` and tune up [13]. web.dev examples sit at `0.5rem`–`1rem` (~8–16 px) for subtle frosted UI, `10 px` for modal overlays [10].
- **Cap on low-end devices: 12–16 px** to keep frame rate steady on full-screen backdrops [11].
- **Above ~40 px** the blur reads as "fog" — the backdrop becomes color and noise only; useful for sheets where you want context-as-color, not context-as-shape.
- liquid-glass-react's default `blurAmount` is `0.0625` (a normalized internal value) combined with `saturation: 140` and `aberrationIntensity: 2` [5].

### Tint (opacity of base color over backdrop)
- Typical light-mode tint: `rgba(255,255,255,0.10–0.30)`. Below ~10% the surface disappears; above ~40% it stops looking like glass and becomes opaque chrome [13][14].
- Dark mode usually needs **less white-tint / more black-tint and more rim contrast** to separate from the page (see §3).
- Setproduct warns against **per-screen opacity tweaks** — fix the recipe once instead of editing the alpha on every component [8].

### Saturation boost ("vibrancy")
- Apple's vibrancy "pulls color forward from behind the material to enhance the sense of depth" [2]. Without it, blurred backdrops look gray and dead.
- CSS analog: `backdrop-filter: blur(20px) saturate(180%)`. liquid-glass-react defaults to `saturation: 140` [5]. Setproduct/Linear-style recipes commonly land in **140–200%**.

### Noise / grain
- Microsoft adds noise specifically to break up banding in the blur and to "ensure contrast and legibility" via an exclusion blend layer [3]. Without noise, large blurred areas posterize on 8-bit displays.
- Typical implementation: a static SVG / PNG noise texture at 2–4% opacity layered above the blur.

### Specular highlights / rims
- Apple's Liquid Glass is "semi-transparent and can reflect and refract its surroundings... with delicate specular highlights" that respond to motion [6][12].
- Linear models "a physical light source that moves through space" — highlights shift with scroll/tap, not statically baked [7].
- A **rim** (1 px inner stroke) is what makes glass look like glass; without it elements feel undefined [13]. Typical value: `inset 0 0 0 1px rgba(255,255,255,0.20–0.35)` in light mode, `rgba(255,255,255,0.08–0.15)` in dark.
- **Antipattern:** a uniform, decorative ring around every glass element — that's "physics of floating cards" applied to anchored sidebars, where it reads as shimmer rather than realism (your own lesson, codified in `feedback_specular_only_fits_floating_cards.md`).

### Edge treatment
- Linear applies **variable blur at scroll edges** — blur intensity ramps up where content meets the chrome — so you don't see a hard cut-off [7][1].
- Rauno: "fade out the bottom edge of the container to make the boundaries feel infinite" — gradient-mask the edge so the material dissolves into the page rather than terminating at a hard rectangle [4].

### Corner radius
- Apple Liquid Glass: shapes "concentric with the screen corner radius" [1]. On Electron desktop apps that means matching `border-radius` to the window's actual rounded corner.
- liquid-glass-react defaults `cornerRadius: 999` (pill) for chips; cards/sheets typically sit at **12–24 px** [5].

### Drop shadow
- Glass needs a soft, large, low-opacity shadow to sell the float — e.g. `0 24px 64px -16px rgba(0,0,0,0.35)` for a modal. Without a shadow, blur alone reads as "the page got fuzzy" rather than "a panel appeared above the page."

### Layering rules — "thin" vs "thick" tiers
Apple ships **five tiers**: `ultraThin → thin → regular → thick → ultraThick`, plus a special `chrome` material adaptive for system surfaces [2][12]. Guidance:
- **regular** is default and works in most cases [12].
- **thicker** when the content on the surface needs more contrast (e.g. text-heavy sheet).
- **thin / ultraThin** for lightweight transient interactions (tooltips, segmented controls).
- **chrome** for system bars / window-frame surfaces (adaptive blur effect, [13b]).

**Don't stack glass on glass.** Microsoft: avoid layering multiple Acrylic surfaces — "multiple layers of background acrylic can create distracting optical illusions" [3]. Apple: don't apply backdrop material more than once in an app for Mica [15]. Your own lesson `feedback_backdrop_filter_containing_block.md` captures the practical consequence: `backdrop-filter` inside another `backdrop-filter` samples the *parent surface*, not the page, and the inner pane looks dead. Hoist the inner glass to a sibling.

---

## 3. Light vs Dark Mode

The recipe changes meaningfully between modes:

| Parameter | Light mode | Dark mode |
|---|---|---|
| Base tint | `rgba(255,255,255,0.12–0.30)` | `rgba(20,20,22,0.45–0.65)` *or* `rgba(255,255,255,0.04–0.08)` for "thin dark glass" |
| Rim (inner stroke) | `rgba(255,255,255,0.25–0.35)` | `rgba(255,255,255,0.08–0.15)` + sometimes a bottom `rgba(0,0,0,0.4)` for shadowed lower rim |
| Saturation | 140–180% | 160–200% (dark backdrops desaturate more under blur) |
| Noise | 2–4% | 3–5% (banding is more visible on dark) |

Why: dark glass with too much tint becomes a flat slab; you need the rim to define edges, and you need more saturation to keep backdrop color alive [16][13]. Microsoft's Mica light vs dark previews show the same surface adopting a near-white wash in light mode and a near-black wash in dark, both tinted from wallpaper [15]. Apple materials adapt automatically per system appearance; some are pinned light/dark by design (`systemChromeMaterial`) [2][12].

For **dark glassmorphism** specifically, the rim is what separates "glass" from "gradient overlay" — without it the surface visually merges with the page [16].

---

## 4. Accessibility & Readability

This is where most glass implementations fail.

**Contrast.** NN/g's primary critique: translucent components placing text over multi-colored backgrounds drop below WCAG contrast and become unreadable [9]. Liquid Glass critiques note contrast can fall under WCAG 2.2 AA 4.5:1 on busy wallpapers when material thickness is too thin [14]. Strategies:
1. **Stabilised plate** — Setproduct's pattern: inside the outer translucent shell, place an *inner mostly-opaque plate* that holds text/icons. Background still shows around the edges; content sits on stable contrast [8].
2. **Counter-intuitively, more blur is safer** — NN/g: "more background blur is better, especially with intricate backgrounds" because it reduces the entropy of the backdrop [9].
3. **No accent-colored text on glass** — Microsoft explicitly: hyperlink-blue or accent-tinted text usually fails contrast on Acrylic at default 14px [3].

**`prefers-reduced-transparency`.** Honor it. Chrome 118+, Firefox, Safari all ship this media query [17][18]. Recipe — additive transparency:
```css
.glass {
  background: rgba(255,255,255,0.85);                /* solid baseline */
  border: 1px solid rgba(255,255,255,0.20);
}
@media (prefers-reduced-transparency: no-preference) {
  .glass {
    background: rgba(255,255,255,0.15);
    backdrop-filter: blur(24px) saturate(180%);
  }
}
```
This "additive mentality" gives users with the setting on a fully functional, high-contrast UI by default and only layers glass when the system allows it [18].

**Increased contrast.** Apple/Linear both collapse glass to solid outlines with strong rims in Increased Contrast mode [7][14]. Mirror that in your Electron app: detect via `prefers-contrast: more` and swap to opaque background + 1.5–2 px solid rim.

**Reduced motion.** If the specular highlight animates with cursor/scroll, kill that motion under `prefers-reduced-motion: reduce`. Liquid Glass critique recommends ≤6 px specular travel even when motion is allowed [14].

---

## 5. Performance

`backdrop-filter` is GPU-bound and one of the most expensive CSS properties at scale [19][20]. Specific findings:

- **Cap blur radius at 12–16 px** on full-screen backdrops for low-end devices [11]. Cost scales superlinearly with radius because the filter is a convolution.
- **Avoid multiple blurred surfaces in the same scroll area** — each one re-samples the backdrop on every frame [11].
- **Don't animate `blur()` directly** — animate `opacity()` *inside* `backdrop-filter`, or fade the element's own opacity, both of which the compositor can promote [11].
- **Stacking context traps.** Any parent with `transform`, `filter`, `overflow:hidden` (in some configurations), or `will-change` creates a new stacking context that becomes the new "backdrop root." The child's `backdrop-filter` then samples that empty/transparent parent surface and the blur appears to do nothing [11][21]. This is the bug behind your own lesson `feedback_backdrop_filter_containing_block.md`.
- **Microsoft explicitly notes** Acrylic is "GPU-intensive, which can increase device power consumption and shorten battery life. Acrylic effects are automatically disabled when a device enters Battery Saver mode" [3]. Mica was introduced *specifically* because it samples the wallpaper *once* and caches, while Acrylic re-blurs continuously [15].
- **Electron caveats.** Multiple long-standing Electron issues document `backdrop-filter` breaking when combined with `vibrancy:` BrowserWindow options, and scroll glitches with fixed-position blurred surfaces [19][22][23]. Test with vibrancy off first; only opt in to native vibrancy when you've reproduced clean rendering.
- **Chromium vs WebKit.** Chromium (and thus Electron) has had more reported issues with backdrop-filter scroll jank than Safari/WebKit; Firefox is CPU-based for blur and has different cost characteristics [11][20].

Mitigations to budget for:
- A `glass-disabled` mode (toggle via setting or `prefers-reduced-transparency`) that swaps every glass surface to a solid + rim recipe.
- **Cap the number of concurrent glass layers** in your design system. Linear/Apple effectively budget one or two per view. Microsoft says "Don't apply backdrop material more than once in an application" for Mica [15].
- For Electron specifically: prefer native `vibrancy:` on macOS for the window-level chrome where possible, and use `backdrop-filter` only for in-page popovers/modals.

---

## 6. Common Mistakes (Antipatterns)

1. **Glass on a flat color.** No backdrop variance = nothing to blur. The glass disappears and you're left with a decorative ring [13][14].
2. **Hard / opaque border.** A `1px solid rgba(255,255,255,1)` ring breaks the "light catches the edge" illusion. Use semi-transparent inner strokes (0.2–0.35 alpha) [13].
3. **Over-blurring.** Above ~50 px blur on small elements you lose all backdrop information and it looks like a Gaussian box, not glass.
4. **Under-tinting.** No tint + heavy blur = "the page got fuzzy" instead of "a panel appeared." Need 8–20% alpha to register as a surface.
5. **Nested backdrop-filters.** The inner pane samples the outer pane (already blurred) instead of the page, producing a dead/gray surface. Hoist to a sibling layer [your `backdrop_filter_containing_block` lesson, supported by 21].
6. **Uniform decorative ring.** Specular rim that doesn't follow a coherent light direction reads as shimmer/sticker, not material — especially on anchored sidebars where the "floating card" physics doesn't apply (your `specular_only_fits_floating_cards` lesson, supported by Setproduct's "locked light direction" rule [8]).
7. **Stacking edge-to-edge acrylic panes.** Creates a visible seam where two blurred regions meet — Microsoft explicitly calls this out [3].
8. **Accent-colored text on glass.** Brand-blue links over translucent material reliably fail contrast at body sizes [3][9].
9. **No reduced-transparency fallback.** Glass that depends on blur to be readable, with no solid alternative, is broken for a non-trivial slice of users [8][17][18].
10. **Per-screen opacity tweaks.** Tuning alpha on each component instead of fixing the recipe creates incoherence across the app [8].

---

## 7. Reference Materials Compared

**Apple — Liquid Glass / Materials (iOS 26, macOS Tahoe, 2025).** A "digital meta-material" that bends/refracts light in real-time, with five thickness tiers + chrome [1][2][6][12]. Vibrancy pulls color forward from behind the material to keep foreground content lively [2]. Adapts automatically to Reduced Transparency, Increased Contrast, Reduced Motion [1]. Concentric corner radii [1]. Comes with explicit "don't decorate, signal depth" guidance.

**Apple — older Materials / NSVisualEffectView / UIBlurEffect.** Pre-Liquid-Glass system: `ultraThin / thin / regular / thick / ultraThick / chrome` blur effect styles with vibrancy variants for labels, fills, separators [2][12]. macOS uses NSVisualEffectView; iOS uses UIBlurEffect. Set the philosophical baseline (thickness tiers, vibrancy as separate concept from blur).

**Microsoft — Fluent Design / Acrylic.** Explicit published recipe: *background → blur → exclusion blend → tint → noise* [3]. Two variants: **background-acrylic** (samples desktop wallpaper) and **in-app acrylic** (samples app behind). Reserved for **transient surfaces** (menus, popovers, light-dismiss). Falls back to solid color when transparency disabled, in Battery Saver, on low-end hardware, or when window inactive [3].

**Microsoft — Mica / Mica Alt.** Opaque material that samples wallpaper *once* and caches — designed for performance on long-lived windows. Tints with theme + wallpaper; goes neutral when window inactive [15]. Rule: max **one** backdrop material per app. The "performance-safe" version of glass; the inverse of Acrylic's per-frame blur.

**Google — Material 3 / Tonal Elevation.** The *opposite* philosophy: no transparency, no blur. Depth is conveyed by **tonal color shifts** — higher elevation = stronger tint of the primary color — plus optional shadow [24][25]. Useful as a counterpoint: if your audience is mostly Android-leaning or you're budget-constrained on GPU, tonal elevation is the cheap-and-accessible alternative to glass. Material 3 Expressive added some background blur in 2025 but still leans on tone first [24].

**Rauno Freiberg — "Designing Depth."** Core ideas: composite layered objects to make depth feel narrative; blur background to push it back on Z; fade container bottom edges so boundaries feel infinite; opacity ≠ blur — opacity-preserving offscreen elements still feel interactive [4]. Translates directly to glass edge-fade and variable-blur patterns.

**Linear — "A Linear spin on Liquid Glass" (2025).** Rebuilt from scratch in SwiftUI rather than adopting Apple's APIs to keep iOS 18 support and design flexibility [7]. Recipe: *Gaussian blur base (UIVisualEffectView) → subtle gradient for structure → specular highlight modeled as a moving physical light → variable blur at scroll edges with color mask*. Deliberately **omitted refraction** because it harms dense pro-tool readability. Mirrors Apple's Increase Contrast behavior with solid outlines.

**rdev/liquid-glass-react.** Open-source React component aimed at recreating Apple's Liquid Glass in the browser [5]. Useful as a parameter dictionary: `displacementScale: 70`, `blurAmount: 0.0625`, `saturation: 140`, `aberrationIntensity: 2`, `elasticity: 0.15`, `cornerRadius: 999`. Modes: `standard / polar / prominent / shader`. **Caveat: displacement only renders fully in Chromium**; Safari/Firefox skip it. Not a turnkey solution for Electron-on-other-platforms but a reasonable starting recipe.

**setproduct — "Liquid glass design explained: a practical guide."** Strongest practitioner framework: glass = *outer shell + inner stabilised plate + locked light direction*. Insists on a same-shape solid fallback recipe ("if your system collapses without blur, it was never a system") [8].

**NN/g — Glassmorphism.** Empirical: glass fails when text contrast varies across backdrops; recommends more blur (not less) as backgrounds get intricate, plus user control over transparency [9].

**Raycast.** Adopted Liquid Glass in AI Chat post-macOS Tahoe; renders popovers as native windows (not DOM) so they can extend beyond window bounds — relevant if ARDEN needs popover work that escapes the Electron BrowserWindow [26].

---

## Practical Recipe for ARDEN (starting point)

```css
/* Light mode */
.glass {
  background: rgba(255, 255, 255, 0.18);
  backdrop-filter: blur(24px) saturate(180%);
  -webkit-backdrop-filter: blur(24px) saturate(180%);
  border: 1px solid rgba(255, 255, 255, 0.30);
  box-shadow:
    0 24px 64px -16px rgba(0, 0, 0, 0.30),
    inset 0 1px 0 rgba(255, 255, 255, 0.40);  /* top specular */
  border-radius: 16px;
}

/* Dark mode */
@media (prefers-color-scheme: dark) {
  .glass {
    background: rgba(22, 22, 25, 0.55);
    border: 1px solid rgba(255, 255, 255, 0.10);
    box-shadow:
      0 24px 64px -16px rgba(0, 0, 0, 0.55),
      inset 0 1px 0 rgba(255, 255, 255, 0.08);
  }
}

/* Accessibility fallback */
@media (prefers-reduced-transparency: reduce) {
  .glass {
    background: var(--surface-solid);
    backdrop-filter: none;
  }
}

/* Performance fallback */
@media (prefers-reduced-motion: reduce) {
  .glass-specular { animation: none; }
}
```

Add a noise PNG/SVG overlay at 3% opacity if you see banding. Reserve this recipe for **one** layer per view (modal *or* sidebar *or* popover — not all three composited).

---

## References

1. Apple Developer — *Liquid Glass*: https://developer.apple.com/documentation/TechnologyOverviews/liquid-glass
2. Apple Developer — *Materials (HIG)*: https://developer.apple.com/design/human-interface-guidelines/materials
3. Microsoft Learn — *Acrylic material*: https://learn.microsoft.com/en-us/windows/apps/design/style/acrylic
4. Rauno Freiberg — *Designing Depth*: https://rauno.me/craft/depth
5. rdev/liquid-glass-react — README: https://github.com/rdev/liquid-glass-react/blob/master/README.md
6. Apple Newsroom — *Apple introduces a delightful and elegant new software design* (2025): https://www.apple.com/newsroom/2025/06/apple-introduces-a-delightful-and-elegant-new-software-design/
7. Linear — *A Linear spin on Liquid Glass*: https://linear.app/now/linear-liquid-glass
8. Setproduct — *Liquid glass design explained: a practical guide*: https://www.setproduct.com/blog/liquid-glass-design-explained-a-practical-guide
9. Nielsen Norman Group — *Glassmorphism: Definition and Best Practices*: https://www.nngroup.com/articles/glassmorphism/
10. web.dev — *Create OS-style backgrounds with backdrop-filter*: https://web.dev/articles/backdrop-filter
11. Copyprogramming — *CSS Backdrop Filter Blur: Complete 2026 Guide with Fallbacks & Best Practices*: https://copyprogramming.com/howto/css-workaround-to-backdrop-filter
12. createwithswift — *Using Materials with SwiftUI*: https://www.createwithswift.com/using-materials-with-swiftui/
13. wpdean — *44 CSS Glassmorphism Examples You Can Actually Use*: https://wpdean.com/css-glassmorphism/
14. designedforhumans — *Apple's New Liquid Glass Design: Practical Guidance for Designers*: https://designedforhumans.tech/blog/liquid-glass-smart-or-bad-for-accessibility
15. Microsoft Learn — *Mica material*: https://learn.microsoft.com/en-us/windows/apps/design/style/mica
16. MustBeWebCode (Medium) — *Dark Glassmorphism: The Aesthetic That Will Define UI in 2026*: https://medium.com/@developer_89726/dark-glassmorphism-the-aesthetic-that-will-define-ui-in-2026-93aa4153088f
17. Chrome for Developers — *CSS prefers-reduced-transparency*: https://developer.chrome.com/blog/css-prefers-reduced-transparency
18. LogRocket — *Using CSS prefers-reduced-transparency and light-dark()*: https://blog.logrocket.com/using-css-prefers-reduced-transparency-light-dark/
19. GitHub — *shadcn-ui/ui Issue #327: CSS Backdrop filter causing performance issues*: https://github.com/shadcn-ui/ui/issues/327
20. Mozilla Bugzilla 925025 — *CSS blur filter is order of magnitude slower than Chrome*: https://bugzilla.mozilla.org/show_bug.cgi?id=925025
21. Copyprogramming — *Backdrop-Filter Blur Not Working with Overflow Hidden Parent*: https://copyprogramming.com/howto/transitioning-backdrop-filter-blur-on-an-element-with-overflow-hidden-parent-is-not-working
22. GitHub — *electron/electron Issue #39529: vibrancy + backdrop-filter doesn't render properly*: https://github.com/electron/electron/issues/39529
23. GitHub — *electron/electron Issue #12906: Scrolling with backdrop-filter failure*: https://github.com/electron/electron/issues/12906
24. Material Design — *Tone-based Surface Color in M3*: https://m3.material.io/blog/tone-based-surface-color-m3
25. Material Design — *Elevation*: https://m3.material.io/styles/elevation/applying-elevation
26. Raycast Blog — *A Technical Deep Dive Into the New Raycast*: https://www.raycast.com/blog/a-technical-deep-dive-into-the-new-raycast
