# Color Rules — A Reference for the ARDEN Design System

> A modern UI color system is built on **perceptual color spaces (OKLCH)**, **functional 10–12 step ramps** (Radix/Geist-style), **APCA-grade contrast**, and **luminance hierarchy** (not shadow) for dark mode. Brand identity comes from the *neutral* and the *one accent that survives every surface* — not from a six-color rainbow.

---

## 1. Color Spaces for UI

**sRGB / hex / `rgb()`** is a device-referred cube. It is not perceptually uniform: a 10% shift in `#0000ff` and a 10% shift in `#ffff00` are not perceived as equally bright [1]. `hsl()` is worse — its `L` channel is mathematical lightness, not perceived lightness, which is why Sass's `darken()` produces inconsistent results across hues [1][2].

**OKLCH** (and its rectangular twin **Oklab**) is the modern default. Components are:

- `L` — perceptual lightness, 0–1 (or 0–100%)
- `C` — chroma, 0–~0.4
- `H` — hue angle, 0–360°

Because `L` is perceptual, "lightness 0.7" looks roughly equally bright across blue, red, and green — making it trivial to build a ramp where every step has the same visual weight [1][8]. OKLCH also describes **P3 wide-gamut** colors that hex/HSL cannot represent; sRGB covers only ~35% of human-visible colors, and modern Apple/OLED screens add ~30% on top [1].

**Practical rule for ARDEN:** define palette tokens in `oklch()`, fallback to hex only for legacy targets. Tailwind v4 has already migrated its default palette to OKLCH for exactly this reason — same class names, more vivid output on P3 displays [8].

**Manipulation:** prefer `color-mix(in oklab, var(--accent) 12%, transparent)` over hand-tuned alphas, and prefer relative-color-syntax (`oklch(from var(--accent) calc(l - 0.1) c h)`) over Sass mixins. `oklab` is the gradient-interpolation default because it never passes through a desaturated "dead zone" [9]. Vercel and Linear both use perceptual spaces for theme generation for the same reason [4][10].

---

## 2. Palette Construction

A serviceable UI palette has **three layers**:

1. **Neutrals** — one 10–12 step ramp. Refactoring UI: "almost everything in an interface is grey, and you'll need more greys than you think — three or four shades might sound like plenty but it won't be long before you wish you had something a little darker than shade #2 but a little lighter" [11]. Plan for 8–10 minimum; Radix and Tailwind ship 11–12.
2. **One accent** — 10–12 step ramp matched to the neutral.
3. **Semantics** — success / warning / error / info, each as a smaller (5–10 step) ramp [11].

The canonical step-purpose mapping is **Radix Colors' 12-step scale** [3], which Vercel Geist has compressed to 10 [12]:

| Step | Radix purpose | Geist equivalent |
|------|---------------|------------------|
| 1–2  | App / subtle backgrounds | Background 1–2 |
| 3    | Component bg, normal | Color 1 |
| 4    | Component bg, hover | Color 2 |
| 5    | Component bg, pressed/selected | Color 3 |
| 6    | Subtle border (non-interactive) | Color 4 |
| 7    | Interactive border | Color 5 |
| 8    | Strong border, focus ring | Color 6 |
| 9    | Solid bg (highest chroma) | Color 7 |
| 10   | Solid bg hover | Color 8 |
| 11   | Low-contrast text (≥ Lc 60 APCA on step 2) | Color 9 |
| 12   | High-contrast text (≥ Lc 90 APCA on step 2) | Color 10 |

This is not arbitrary — every step has a job, and "you cannot rely purely on math to craft the perfect palette" [11]: the visual judgment goes into picking step 9 (the brand-defining solid) first, then filling steps 1–8 by interpolation and steps 11–12 by APCA target [3].

**The 60/30/10 rule** is a starter heuristic, not a law. It breaks for image-heavy apps, data-dense tools, and design systems that grow over time (color drift) [13]. ARDEN's actual ratio is closer to ~85% neutral / ~10% accent / ~5% semantic — typical of tools like Linear, Vercel, and Notion.

---

## 3. Light vs. Dark Mirroring

**Dark mode is not value inversion.** Naively flipping `L` of every token produces muddy, low-contrast results because perception of contrast on dark backgrounds is asymmetric (APCA's "polarity" property) [5][6].

The Radix convention: step 1 in light mode is `#fdfdfc`; step 1 in dark mode is a *near-black* like `#111`, not `#020202`. Both serve the same role (app background), so they invert role for role — but the values are tuned independently. Linear rebuilt their theme generator around this insight: light mode darkens text/icons, dark mode lightens them, but the relationships between step 9 (solid) and step 12 (text) are tuned for each polarity [4].

**Accent often does *not* mirror.** A pure `oklch(0.55 0.22 250)` blue that pops on white will vibrate on near-black. Apple HIG, Material 3, and Linear all desaturate accents 20–40% in dark mode to reduce chromatic aberration on OLED [7][14].

**Surface hierarchy in each mode:**

- Light: bg gets darker as you elevate (or you tint with accent — Material 3 "tonal elevation" [14]).
- Dark: bg gets *lighter* as you elevate (the Material 3 rule [14]), because shadows have nothing to subtract from a near-black surface [15][16].

---

## 4. Contrast and Accessibility

**WCAG 2.x** computes a luminance ratio with a single fixed formula. It is mathematically simple but perceptually wrong: it does not account for font weight, text size beyond two thresholds, polarity (light-on-dark vs dark-on-light), or surrounding brightness [5][6]. A thin 10px font and a bold 10px font score identically.

**APCA** (Advanced Perceptual Contrast Algorithm) is the candidate replacement in WCAG 3 [5]. It returns an `Lc` value 0–106 where polarity matters — swapping text and background gives a different score — and font weight/size enter the calculation. Radix guarantees:

- **Step 11 on step 2** → ≥ Lc 60 (body text minimum) [3]
- **Step 12 on step 2** → ≥ Lc 90 (high-emphasis body text) [3]

Rough APCA targets in practice [5][6]:

| Use | Min Lc |
|-----|--------|
| Body text (400 weight, ~14–16px) | 75 |
| Body text (500–600 weight) | 60 |
| Large text / headers | 45 |
| Non-text UI (icons, borders) | 30 |
| Focus rings | 45 against both adjacent regions |

**Common pitfalls** [11]:

- "Hint" text at gray-400 on white — passes WCAG AA, fails APCA Lc 60.
- Gray text on a colored surface. *Don't use neutral gray on colored backgrounds; tint the text with the same hue at lower chroma* — Refactoring UI's single best tip [11].
- Disabled state at 30% opacity in dark mode — invisible.

GitHub's Primer team audited 100+ token pairs across default / dimmed / high-contrast / colorblind themes by automating contrast checks in CI — the only sane way to keep a multi-palette system honest [17].

---

## 5. Tinting & Elevation via Color

**Material 3** abandoned opacity overlays in favor of **tonal elevation**: at each elevation level, the surface is mixed with the primary color at increasing strength, producing `surfaceContainerLowest → surfaceContainerLow → surfaceContainer → surfaceContainerHigh → surfaceContainerHighest` [14][16]. In a green-primary M3 theme, every elevated card has a faint green wash.

**Linear and Vercel reject this.** Their elevated surfaces are *pure neutral steps* — a modal is "step 2" of the gray ramp, not "step 1 + 8% accent tint" [4][12]. The rationale: tint reads as accidental on a tool you stare at all day, and accent-tinted surfaces ruin neutral hierarchy.

**When tint works:** brand-forward consumer products (M3 reference apps), where the surface tint reinforces brand identity. **When it fails:** information-dense workspaces (Linear, Notion, ARDEN) where neutrality is the point. Linear's redesign explicitly moved *away from* a cool blue tint toward "a warmer gray that still feels crisp but less saturated" [4].

**ARDEN's call:** elevation through neutral steps, not accent tint. The "glass" and "linen" materials carry texture; they should not carry brand color.

---

## 6. Accents

Choosing **one** accent is harder than choosing six. The constraint: it must be legible on every neutral step, in both light and dark mode, with no muddy zones.

Construction recipe (Radix-style):

1. Pick step 9 first — the brand color. `oklch(0.62 0.19 H)` is a safe starting point.
2. Generate step 10 (hover) by reducing `L` by ~0.04. Reducing `L` perceptually = reducing brightness; in HSL this would also shift hue [1][8].
3. Steps 3–5 are accent-tinted backgrounds (alpha on accent or low-chroma high-L variants) — used for selected rows, active tabs.
4. Steps 11–12 are accent text colors — used sparingly, only when the accent needs to be the foreground (e.g., link color).

**Hover/active darkening:** use `oklch(from var(--accent) calc(l - 0.05) c h)`, not `filter: brightness(0.9)`. The relative-color approach preserves chroma; `brightness()` desaturates.

**Single accent vs functional palette:** Linear, Vercel, and Notion all run a single dominant accent with semantic colors reserved strictly for status. Catppuccin runs 14 named accents (Rosewater, Flamingo, Pink, Mauve, Red, Maroon, Peach, Yellow, Green, Teal, Sky, Sapphire, Blue, Lavender) [18][19] — but that is a *theme* for editors/terminals, not a product design system. For an app: one accent, plus semantics.

---

## 7. Semantic Colors

The cross-cultural canon:

- **Red** — error, destructive
- **Amber/Yellow** — warning, attention
- **Green** — success, confirmation
- **Blue** — info, neutral notification

Each should be its own 10–12 step ramp, not just `#f00 / #ff0 / #0f0` [11]. Geist ships exactly this: Blue, Red, Amber, Green, Teal, Purple, Pink — each as a full ramp with the same step-purpose mapping as gray [12].

**Status states** (running / queued / failed / cancelled) are the trap. Don't add a new ramp per state — overload existing semantics:

- queued → neutral step 9
- running → blue step 9 (info)
- success → green step 9
- failed → red step 9
- cancelled → neutral step 8 (de-emphasized)
- warning → amber step 9

If you need more distinction, vary *form* (dot vs ring vs solid pill), not hue.

**Cultural caveats:** green = "go/safe" in Western contexts, but green can mean "infidelity" in parts of East Asia, and red is celebratory (not error) in Chinese contexts. For an English-language dev tool the Western convention dominates, but document this if you ship globally.

---

## 8. Dark Mode Specifics

**Pure black (`#000`) vs near-black:** OLED screens turn `#000` pixels off completely, which sounds great but produces "smearing" when scrolling — adjacent lit pixels appear to trail because there is no gradient buffer. Linear, Vercel, Notion, and GitHub all use *near-black* (typically `oklch(0.18–0.22 0 0)` ≈ `#111–#1a1a1a`) for the base surface [4][12][17]. Reserve `#000` for OLED-specific power-saver themes.

**Elevation = lighter, not darker:** Material 3 rule. Shadow on dark is mostly invisible; the depth cue must come from luminance [14][15][16]. Token names like `surface / surface-elevated / surface-overlay` should each be 4–8 perceptual lightness points apart.

**Desaturated accents:** Apple HIG, Material 3, and most modern systems reduce accent chroma by 20–40% in dark mode to prevent vibration on OLED [7]. Example: light-mode accent `oklch(0.62 0.19 250)` → dark-mode accent `oklch(0.72 0.13 250)`. Lightness goes *up* (better contrast on dark bg) and chroma goes *down* (less vibration).

**Shadows in dark mode:** if you keep them, increase alpha to ~0.4–0.6 (vs 0.1–0.15 in light) and combine with a 1px lighter "rim" (`inset 0 1px 0 oklch(1 0 0 / 0.06)`) to fake the highlight from an imagined light source [15][16].

---

## 9. The Eight Palettes — What Makes Each Distinctive

**Graphite** — Pure neutral, zero chroma. `oklch(L 0 0)` across the ramp. The "no opinion" palette; lets content carry all color. Apple's default macOS accent. Useful as a fallback / accessibility-first option.

**Warm** — Neutral with hue rotated to ~60–80° (yellow-orange) at very low chroma (~0.005–0.015). Notion's signature: their secondary background `#E3E2DE` is a warm gray that "replaces harsh blacks, keeping the reading experience soft" [20]. Linear's recent refresh moved this direction explicitly [4].

**Vercel (Geist)** — High-contrast cool-neutral. 10-step scale (vs Radix's 12), explicit P3 gradient support, two background levels (Background 1 / Background 2) with three component levels on top [12]. Identity: maximum contrast, minimum chroma, near-black `#000` background in dark mode. Brand accent is pure-black-on-white inversion, not a hue.

**Raycast** — Theme-as-data: shared via URL-encoded `themes.ray.so` links [21]. Single accent + neutral + a small set of dynamic role colors that adjust per-theme for guaranteed contrast. Identity: highly customizable but with a strict role contract.

**GitHub (Primer)** — Functional tokens (`fgColor.success`, `bgColor.danger`) over raw scale values, with custom interpolated steps (5.5 between 5 and 6) tuned to hit WCAG AA on every theme [17][22]. Ships *six* themes per mode: default, dimmed, high contrast, Protanopia/Deuteranopia, Tritanopia, plus light/dark variants of each. Identity: enterprise accessibility scale.

**Linear** — Generated from 3 inputs (base, accent, contrast) in LCH space, replacing 98 hand-tuned variables [4]. Warm-gray neutral, single accent (blue by default), strict surface hierarchy: background / foreground / panels / dialogs / modals. Identity: programmatic theme generation, perceptual uniformity.

**Notion** — 10 named colors (Default, Gray, Brown, Orange, Yellow, Green, Blue, Purple, Pink, Red) [20][23]. Each has separate hex values for text, background, and icon — icons more saturated than text. Warm-gray neutral. Identity: content tagging palette, not a UI accent system; the UI itself is near-monochrome.

**Catppuccin** — Pastel theme system. Four flavors: Latte (light), Frappé, Macchiato, Mocha (dark) [18][19]. 26 colors per flavor: 14 named accents + 12 UI roles (`base`, `mantle`, `crust`, `surface0/1/2`, `overlay0/1/2`, `subtext0/1`, `text`). Mocha base: `#1e1e2e`; text: `#cdd6f4`. Identity: hue-rich pastels with explicit role naming; designed for editor/terminal use where syntax highlighting consumes 14 hues simultaneously.

---

## 10. Practical Recipes

**Shadow tokens** (alpha values that work on both polarities):

```css
/* Light mode */
--shadow-sm: 0 1px 2px oklch(0 0 0 / 0.06);
--shadow-md: 0 2px 8px oklch(0 0 0 / 0.08), 0 1px 2px oklch(0 0 0 / 0.04);
--shadow-lg: 0 10px 32px oklch(0 0 0 / 0.12), 0 2px 6px oklch(0 0 0 / 0.06);

/* Dark mode — increase alpha, add rim highlight */
--shadow-sm: 0 1px 2px oklch(0 0 0 / 0.4), inset 0 1px 0 oklch(1 0 0 / 0.04);
--shadow-md: 0 2px 8px oklch(0 0 0 / 0.5), inset 0 1px 0 oklch(1 0 0 / 0.06);
--shadow-lg: 0 12px 40px oklch(0 0 0 / 0.6), inset 0 1px 0 oklch(1 0 0 / 0.08);
```

**Border alphas** (tint borders with the surface, not pure black):

```css
--border-subtle: oklch(from var(--surface) calc(l - 0.08) c h);   /* light */
--border-subtle: oklch(from var(--surface) calc(l + 0.08) c h);   /* dark */
```

Or use neutral step 6/7 from the ramp.

**Focus ring** — 2 colors required for "two-tone" technique that works on any background [24][25]:

```css
:focus-visible {
  outline: 2px solid var(--accent-9);
  outline-offset: 2px;
  box-shadow: 0 0 0 4px var(--accent-9-alpha-20);
}
```

Target ≥ 3:1 (WCAG 2.4.7) or ≥ Lc 45 (APCA) against *both* the focused element and the adjacent background [24].

**Selection background:**

```css
::selection {
  background: color-mix(in oklab, var(--accent-9) 30%, transparent);
  color: var(--text-12);
}
```

30% alpha in oklab keeps the underlying text legible; HSL/RGB mixing at the same percentage washes it out [9].

**Scroll thumb** — neutral step 7 at rest, step 9 on hover, with a 1–2px transparent border so it doesn't kiss the track edge:

```css
::-webkit-scrollbar-thumb {
  background: var(--neutral-7);
  border: 2px solid transparent;
  background-clip: padding-box;
  border-radius: 999px;
}
::-webkit-scrollbar-thumb:hover { background: var(--neutral-9); background-clip: padding-box; }
```

**Accent hover/active** (perceptual darkening):

```css
.button { background: var(--accent-9); }
.button:hover  { background: oklch(from var(--accent-9) calc(l - 0.04) c h); }
.button:active { background: oklch(from var(--accent-9) calc(l - 0.08) c h); }
```

**Tinted text on colored backgrounds** (Schoger's rule [11]):

```css
.alert-info {
  background: var(--blue-3);
  color: var(--blue-11);   /* NOT var(--neutral-11) */
}
```

---

## References

1. Evil Martians — *OKLCH in CSS: why we moved from RGB and HSL.* https://evilmartians.com/chronicles/oklch-in-css-why-quit-rgb-hsl
2. Evil Martians — *OK, OKLCH: a color picker made to help think perceptively.* https://evilmartians.com/chronicles/oklch-a-color-picker-made-to-help-think-perceptively
3. Radix Colors — *Understanding the scale.* https://www.radix-ui.com/colors/docs/palette-composition/understanding-the-scale
4. Linear — *How we redesigned the Linear UI (part II).* https://linear.app/now/how-we-redesigned-the-linear-ui
5. Dan Hollick — *WCAG 3 and APCA.* https://typefully.com/DanHollick/wcag-3-and-apca-sle13GMW2Brp
6. APCA — *APCA in a Nutshell / Why APCA.* https://git.apcacontrast.com/documentation/APCA_in_a_Nutshell.html · https://git.apcacontrast.com/documentation/WhyAPCA.html
7. Apple — *Human Interface Guidelines: Dark Mode.* https://developer.apple.com/design/human-interface-guidelines/dark-mode
8. Tailwind CSS — *Colors (v4, OKLCH).* https://tailwindcss.com/docs/colors · *Tailwind CSS v4.0 announcement.* https://tailwindcss.com/blog/tailwindcss-v4
9. Adam Argyle — *CSS color-mix() in oklab.* https://x.com/argyleink/status/1620125485626966016 · CSS-Tricks Almanac, `color-mix()`: https://css-tricks.com/almanac/functions/c/color-mix/
10. Evil Martians — *Better dynamic themes in Tailwind with OKLCH color magic.* https://evilmartians.com/chronicles/better-dynamic-themes-in-tailwind-with-oklch-color-magic
11. Adam Wathan & Steve Schoger — *Refactoring UI: Building Your Color Palette.* https://refactoringui.com/previews/building-your-color-palette
12. Vercel — *Geist Colors.* https://vercel.com/geist/colors
13. Hype4 Academy — *60-30-10 Colors in UI Design.* https://hype4.academy/articles/design/60-30-10-rule-in-ui · LogRocket — *Master UI design: 60-30-10.* https://blog.logrocket.com/ux-design/60-30-10-rule/
14. Material 3 — *Learn About Tone-based Surfaces in Material 3.* https://m3.material.io/blog/tone-based-surface-color-m3 · *Applying elevation.* https://m3.material.io/styles/elevation/applying-elevation
15. Parker — *Good dark mode shadows & elevation.* https://www.parker.mov/notes/good-dark-mode-shadows
16. Muzli — *Dark Mode Design Systems: Patterns, Tokens, Hierarchy.* https://muz.li/blog/dark-mode-design-systems-a-complete-guide-to-patterns-tokens-and-hierarchy/
17. GitHub Blog — *Unlocking inclusive design: how Primer's color system is making GitHub.com more inclusive.* https://github.blog/engineering/user-experience/unlocking-inclusive-design-how-primers-color-system-is-making-github-com-more-inclusive/
18. Catppuccin — *Palette.* https://catppuccin.com/palette/
19. Catppuccin GitHub — *catppuccin/catppuccin README.* https://github.com/catppuccin/catppuccin
20. Matthias Frank — *Notion Colors: All Hex Codes for Text, Backgrounds & Icons.* https://matthiasfrank.de/en/notion-colors/
21. Raycast — *Themes Manual.* https://manual.raycast.com/themes · *Raycast API Colors.* https://developers.raycast.com/api-reference/user-interface/colors
22. Primer — *Colors / Primitives README.* https://primer.style/primitives/colors/ · https://github.com/primer/primitives
23. Notion Avenue — *Notion Color Code Hex Palette.* https://www.notionavenue.co/post/notion-color-code-hex-palette
24. Sara Soueidan — *A guide to designing accessible, WCAG-conformant focus indicators.* https://www.sarasoueidan.com/blog/focus-indicators/
25. Deque — *How To Design Useful and Usable Focus Indicators.* https://www.deque.com/blog/give-site-focus-tips-designing-usable-focus-indicators/
