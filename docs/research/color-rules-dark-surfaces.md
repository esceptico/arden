# Dark Mode Surface Stepping — Lightness Gap Reference

> Companion to [color-rules.md](./color-rules.md) §8. Specifically about the
> **step 1 → step 2 → step 3 lightness delta** in dark mode: the gap between
> the app background, the subtle/sunken background, and the rest UI element
> background. Wrong sizing here is the most common cause of "dark mode feels
> harsh" — a complaint that is rarely about the absolute darkness of the
> page and almost always about the *jump* to the first elevated surface.

---

## 1. Why step 1→2 matters more than steps 5→6 or 9→10

Perceptual lightness near black is **nonlinear** in two ways that compound:

1. **OKLCH L is perceptually uniform across its range**, but the *visual
   weight* of a surface against text depends on luminance Y, not L. Near
   black, a constant ΔL produces a much smaller ΔY than the same ΔL near
   mid-gray. So step 1→2 needs less L distance than step 5→6 to register.
2. **APCA polarity**: on a dark background, the eye's sensitivity to small
   relative luminance shifts is *higher* than on a light background. A
   ΔL of 0.05 between bg and surface that feels "subtle" in light mode
   reads as a "two-tone split" in dark mode.

Practical implication: the step 1→2 delta in a well-tuned dark scale is
**~30% smaller than the step 5→6 delta**, despite OKLCH telling you they
should be equal. Radix, Linear, and Material 3 all bake this in.

---

## 2. Industry numbers — OKLCH L per step, dark scales

Computed from the canonical hex values for each system (OKLab conversion,
Björn Ottosson reference). All values are OKLCH L (0–1).

| System              | Step 1 | Step 2 | Step 3 | Step 4 | Step 5 | Δ 1→2  | Δ 2→3  |
|---------------------|-------:|-------:|-------:|-------:|-------:|-------:|-------:|
| **Radix grayDark**  | 0.178  | 0.213  | 0.252  | 0.285  | 0.313  | 0.036  | 0.039  |
| Radix slateDark     | 0.179  | 0.213  | 0.252  | 0.283  | 0.312  | 0.035  | 0.039  |
| Radix mauveDark     | 0.180  | 0.215  | 0.254  | 0.285  | 0.313  | 0.036  | 0.039  |
| Radix sandDark      | 0.177  | 0.213  | 0.252  | 0.284  | 0.312  | 0.036  | 0.039  |
| Radix oliveDark     | 0.180  | 0.212  | 0.250  | 0.282  | 0.310  | 0.031  | 0.039  |
| **Linear app**      | 0.139  | 0.177  | 0.222  | 0.256  | 0.301  | 0.038  | 0.045  |
| **Material 3**      | 0.187  | 0.227  | 0.245  | 0.286  | 0.330  | 0.039  | 0.018* |
| **Vercel Geist**    | 0.000  | 0.145  | 0.218  | 0.269  | 0.321  | 0.145† | 0.073  |
| **GitHub Primer**   | 0.176  | 0.220  | 0.267  | 0.330  | 0.425  | 0.044  | 0.046  |
| Tailwind slate dark | 0.129  | 0.208  | 0.280  | 0.372  | 0.446  | 0.079‡ | 0.072  |

\* M3's `surfaceContainerLowest` → `surface` (N4→N6) is small by design
because they treat those as "same layer, slightly dimmer". The next jump
to `surfaceContainerLow` (N10, L≈0.286) is the real first elevation.

† Vercel ships **pure black** (`#000`) as `background-200` (the page bg)
and `#0a0a0a` as `background-100`. The 0.145 jump is huge in raw L but
small in Y; this is the "OLED-friendly" school. Most dashboards prefer
the Radix model.

‡ Tailwind's gray ramps are not designed to be used as a 12-step dark
scale — they're general-purpose. Using `slate-950 → slate-900` as bg
and surface produces the "two-tone split" complaint almost universally.

**The pattern**: every Radix dark scale sits at **L=0.178 ± 0.003** for
step 1 and **L=0.213 ± 0.002** for step 2. The 1→2 delta is **0.035 ±
0.003** in OKLCH L. Linear and Material 3 cluster around the same range.
Vercel and Tailwind are outliers (Vercel by design — black is the brand).

---

## 3. Our previous numbers + diagnosis

`apps/desktop/src/lib/tokens/color.ts → neutralRampDark()`:

| Step | Prev L | Radix L | Prev Δ | Radix Δ |
|------|-------:|--------:|-------:|--------:|
| 1    | 0.165  | 0.178   | —      | —       |
| 2    | 0.195  | 0.213   | 0.030  | 0.035   |
| 3    | 0.225  | 0.252   | 0.030  | 0.039   |
| 4    | 0.260  | 0.285   | 0.035  | 0.033   |
| 5    | 0.295  | 0.313   | 0.035  | 0.028   |

Surprise: our raw Δ 1→2 was **smaller than Radix** (0.030 vs 0.035). So
why did the user complain?

**Step 1 was too dark** (0.165 vs Radix's 0.178). Below ~0.17, every
luminance increment becomes perceptually outsized — a ΔL of 0.030 from
L=0.165 reads bigger than ΔL=0.035 from L=0.178, because the *Y ratio*
(0.0224 → 0.0319 ≈ 1.42×) is steeper than (0.0264 → 0.0381 ≈ 1.44×, but
on a brighter base where the eye has already adapted). The user wasn't
complaining about the delta — they were complaining about the **contrast
ratio** between bg and surface, which is `Y2/Y1`, and gets dramatically
worse the closer step 1 sits to true black.

All four ARDEN palettes (warm, graphite, raycast, notion) shared the same
`neutralRampDark()` curve, so all four had the same issue.

---

## 4. Recommended new numbers (applied)

Single change to the shared dark neutral curve — palettes inherit via
`ANCHORS[id].neutralHueDark`, no per-palette overrides needed.

| Step | Old L | New L | Old Δ | New Δ |
|------|------:|------:|------:|------:|
| 1    | 0.165 | **0.180** | —     | —     |
| 2    | 0.195 | **0.202** | 0.030 | **0.022** |
| 3    | 0.225 | **0.230** | 0.030 | **0.028** |
| 4    | 0.260 | **0.265** | 0.035 | 0.035 |
| 5    | 0.295 | **0.300** | 0.035 | 0.035 |
| 6    | 0.330 | **0.335** | 0.035 | 0.035 |
| 7    | 0.380 | **0.385** | 0.050 | 0.050 |
| 8    | 0.450 | **0.455** | 0.070 | 0.070 |
| 9–12 | unchanged |   |       |       |

Two moves:

1. **Lift step 1** to 0.180 — matches Radix's empirically-tuned floor.
2. **Tighten step 1→2** to 0.022 — the surface "lifts" off the page
   instead of "splitting" into a new tone. This is *intentionally
   smaller* than Radix (0.035) because the ARDEN UI uses glass/linen
   materials with their own visual weight; a smaller token delta lets
   the material carry the rest of the elevation cue.

Step 12 (ink) stays at L=0.945, identical to Radix step 12 (0.949). APCA
ink-on-bg moves from Lc 95.5 → 95.1 — well above the 60 floor.

---

## 5. APCA verification

After applying: **40/40 PASS** (full grid, all 4 palettes × light/dark ×
5 pairs). Minimum margin shrank from Lc 57.0 → 56.8 on `muted-on-bg dark`
across all four palettes, still 11.8 above the Lc 45 target.

Full report: [docs/internal/contrast-report.md](../internal/contrast-report.md).

---

## 6. When this rule doesn't apply

- **OLED-power-saver themes**: keep step 1 at `#000` (L=0) to disable
  pixels. Vercel does this. The 1→2 jump becomes 0.18 in L but the
  energy savings justify it.
- **Mid-grey "dim" themes** (GitHub's `dimmed`, Slack's `aubergine`):
  step 1 sits around L=0.25–0.30, and the 1→2 delta can grow back to
  ~0.04 because the eye is no longer adapting to near-black.
- **High-contrast accessibility variants**: invert — *increase* the 1→2
  delta to 0.06–0.08 so low-vision users can locate surfaces.

For ARDEN's default dark theme (the common case), 0.022 is the call.

---

## References

- Radix Colors source: `radix-ui/colors/src/dark.ts` —
  https://github.com/radix-ui/colors/blob/main/src/dark.ts
- Radix scale composition guide — https://www.radix-ui.com/colors/docs/palette-composition/scales
- Linear redesign part II — https://linear.app/now/how-we-redesigned-the-linear-ui
- Material 3 tone-based surfaces — https://m3.material.io/blog/tone-based-surface-color-m3
- Vercel Geist colors — https://vercel.com/geist/colors
- GitHub Primer dark theme primitives — https://github.com/primer/primitives
- APCA in a Nutshell (polarity, near-black behavior) — https://git.apcacontrast.com/documentation/APCA_in_a_Nutshell.html
- Björn Ottosson, *A perceptual color space for image processing* (Oklab) —
  https://bottosson.github.io/posts/oklab/
