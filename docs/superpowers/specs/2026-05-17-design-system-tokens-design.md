# Design system tokens — spec

**Date**: 2026-05-17
**Status**: Draft pending user review
**Research basis**: `docs/research/{glass-design,linen-design,color-rules,microactions,animations-by-material}.md`

This spec codifies the application of the 5 research docs into the ARDEN desktop app. Approach **A** chosen (tokens-first, surfaces-second): build a design-token layer that names every research finding as a primitive, then sweep consumers to replace inline literals with token references.

---

## Section 1 — Architecture & scope

### What we're building

A token layer plus a consumer sweep.

**Three token modules** (TS for JS consumers, mirrored CSS custom properties for stylesheets):

1. **`lib/tokens/color.ts` + `:root` CSS vars** — OKLCH-defined neutral ramps (12 steps, Radix-style), accent (light/normal/strong/soft), semantic (ok/warn/bad/info). Light + dark variants. APCA-validated for body/heading targets. Replaces the current scattered `--color-*` vars with a unified system.

2. **`lib/tokens/motion.ts`** — named springs + eases + durations matched to use case (modal, card, popover, tap, hover, route). Per-material overrides where research justifies it (glass entry decelerates, linen entry uses Material emphasized). Replaces `SPRING_SMOOTH` / `SNAPPY` / `BOUNCY` (which don't map to any documented use case).

3. **`lib/tokens/elevation.ts` + CSS vars** — shadow recipes (rest, hover, popover, modal) for linen, ring alphas (light/dark, ink-derived), glass rim/drop recipes. Replaces inline shadow strings in `styles.css`.

**Consumer sweep** (Phase 2+) — `styles.css` and components stop using literal values, start using `var(--shadow-rest)`, `import { SPRING_MODAL }`, etc. Also introduces small new utility classes (`.surface-rail`, `.surface-popover`, `.surface-card`) that wrap a recurring inline pattern + tokens into one named handle. No new features; just substitution + naming.

### Migration discipline

Every token replacement is a no-op visually *unless* the research dictates a different value. Changes that ARE visible (e.g. ink-derived borders in light mode — already shipped) are called out explicitly.

### Out of scope

- **Replacing the 8 palettes with new ones** — we keep the palette list; we re-derive each in OKLCH.
- **Rewriting any animation logic** — we change the constants, not the call sites.
- **Touching the Glass vs Linen toggle UX** — that just shipped.
- **Building a `prefers-reduced-transparency` additive fallback** — research says do it; we'll note it as a future-phase task.
- **New libraries** — no APCA dependency added in scope; we use a one-shot validator script and inline the results. APCA tooling lives in a separate future task.

---

## Section 2 — Token catalog

Three modules; values come from the research docs (see citations there).

### 2.1 — Color tokens (`lib/tokens/color.ts` + CSS vars on `:root` and `:root.dark`)

**Structure**: one neutral ramp + one accent ramp + four semantic ramps. Each ramp is 12 steps in OKLCH. Light + dark variants. The 8 existing palettes (graphite/warm/vercel/raycast/github/linear/notion/catppuccin) each define their own neutral + accent in OKLCH; semantic ramps are palette-agnostic.

- **Neutral** (`--color-neutral-1` … `--color-neutral-12`): app bg, surface, surface-soft, line-soft, line, line-strong, faint, muted, ink-soft, ink, ink-strong. Replaces today's ad-hoc `--color-bg`, `--color-surface`, etc.
- **Accent** (`--color-accent-1` … `--color-accent-12`): subtle (1–3), interactive (4–6), bold (7–9), text (10–12). Replaces `--color-accent`, `--color-accent-soft`, `--color-accent-strong`.
- **Semantic** (`--color-{ok,warn,bad,info}-{soft,base,strong,fg}`): smaller 4-stop ramp each. Replaces `--color-bad-soft`, etc.
- **Aliases** kept for compatibility (`--color-ink` → `--color-neutral-12`, `--color-line-soft` → `--color-neutral-5`) so existing code doesn't break in one big-bang sweep. Aliases retired in cleanup phase.

**Dark mode**: not a value-inversion. Accent typically desaturates ~20–40%; neutrals re-derive from a darker base; elevated surfaces get *lighter*, not darker (Material rule via Linear).

**APCA validation**: one-shot script (`scripts/validate-contrast.ts`) computes APCA Lc for `ink-on-bg`, `ink-soft-on-bg`, `muted-on-bg`, `accent-on-bg`, `accent-fg-on-accent`. Targets: body Lc ≥ 60, large text Lc ≥ 45, UI/icons Lc ≥ 45. Output is checked into the repo as `docs/internal/contrast-report.md` per palette × theme. If a palette fails, we fix the value at the token level, not in components.

### 2.2 — Motion tokens (`lib/tokens/motion.ts`)

**Springs** (replace `SPRING_SMOOTH`/`SNAPPY`/`BOUNCY` — names don't describe use case):

| Token | Stiffness | Damping | Use case |
|---|---|---|---|
| `SPRING_MODAL` | 380 | 32 | Modals, sheets, drawers |
| `SPRING_CARD` | 260 | 26 | Cards lifting on hover, smaller surfaces |
| `SPRING_TAP` | 380 | 30 | Press/release |
| `SPRING_LAYOUT` | 220 | 28 | Layout / FLIP / list reorders |

**Eases** (cubic-bezier):

| Token | Curve | Use case |
|---|---|---|
| `EASE_STANDARD` | `[0.2, 0, 0, 1]` | Material 3 emphasized — default for transitions |
| `EASE_DECELERATE` | `[0.05, 0.7, 0.1, 1]` | Material 3 emphasized-decelerate — entries |
| `EASE_HOVER` | `[0.16, 1, 0.3, 1]` | Snappy out-cubic for hover brightness/color |
| `EASE_EMPHASIZED` | `[0.32, 0.72, 0, 1]` | Kept — sidebar slides |

**Durations** (seconds):

| Token | Value | Use case |
|---|---|---|
| `DURATION_TAP` | 0.1 | Tap feedback |
| `DURATION_HOVER` | 0.14 | Hover transitions |
| `DURATION_POPOVER` | 0.18 | Popover, picker open/close |
| `DURATION_PANEL` | 0.24 | Panels, dialog body |
| `DURATION_ROUTE` | 0.36 | Sidebar/inspector slides, route changes |

**Per-material entry curves** (the one place motion differs by material):

| Token | Spring/Ease | Duration | Notes |
|---|---|---|---|
| `ENTRY_GLASS` | `EASE_DECELERATE` | 0.22 | Scale 0.96→1; content fades in delayed 100 ms |
| `ENTRY_LINEN` | `SPRING_MODAL` | — | Scale 0.95→1; content and surface together |

**Migration**: keep `SPRING_SMOOTH` re-exported as `SPRING_MODAL` alias starting Phase 1; sweep call sites in Phase 2; delete the alias in Phase 6.

### 2.3 — Elevation tokens (`lib/tokens/elevation.ts` + CSS vars)

**Ring alphas** (universal — used by linen ring, glass inset ring, focus rings):

```
--ring-light: rgba(20, 24, 28, 0.10);
--ring-light-soft: rgba(20, 24, 28, 0.06);
--ring-light-strong: rgba(20, 24, 28, 0.14);
--ring-dark: rgba(255, 255, 255, 0.08);
--ring-dark-soft: rgba(255, 255, 255, 0.05);
--ring-dark-strong: rgba(255, 255, 255, 0.14);
```

**Linen shadow recipes** (stacked Tailwind pattern, light mode):

```
--shadow-linen-rest:    0 1px 2px rgb(0 0 0 / .04),  0 4px 12px rgb(0 0 0 / .06);
--shadow-linen-hover:   0 2px 4px rgb(0 0 0 / .05),  0 8px 20px rgb(0 0 0 / .08);
--shadow-linen-popover: 0 2px 4px rgb(0 0 0 / .06),  0 12px 32px rgb(0 0 0 / .12);
--shadow-linen-modal:   0 4px 8px rgb(0 0 0 / .08),  0 20px 48px rgb(0 0 0 / .16);
```

**Dark mode** (Linear-style single contained shadow):

```
--shadow-linen-rest:    0 2px 4px rgba(0,0,0,.4);
--shadow-linen-hover:   0 2px 4px rgba(0,0,0,.4),  0 4px 12px rgba(0,0,0,.3);
--shadow-linen-popover: 0 4px 12px rgba(0,0,0,.5);
--shadow-linen-modal:   0 8px 32px rgba(0,0,0,.6);
```

**Glass rim + drop** (tokenizes what already lives in `.glass-surface`):

```
--glass-drop-light: 0 8px 24px -12px rgba(31, 38, 135, 0.25);
--glass-drop-dark:  0 4px 14px -4px rgba(0, 0, 0, 0.32);
--glass-rim-light:  inset 0 1px 0 0 rgba(255, 255, 255, var(--gp-rim, 0.6));
--glass-rim-dark:   inset 0 1px 0 0 rgba(255, 255, 255, calc(var(--gp-rim, 0.6) / 6)),
                    inset 0 0 0 1px rgba(255, 255, 255, 0.03);
```

**Focus ring**:

```
--focus-ring: 0 0 0 2px var(--color-accent-soft),
              0 0 0 3px var(--color-accent);
```

Drives `:focus-visible` across all interactive primitives.

---

## Section 3 — Surface migration

The token layer alone is invisible. This section maps every surface in the app to the tokens it should consume, and notes the visible vs no-op nature of each migration.

### 3.1 — Inventory

| Surface | File(s) | Current literal-laden patterns |
|---|---|---|
| Modal scrim | `styles.css .modal-scrim` | hardcoded rgba color + 8 px blur |
| Modal slab | `styles.css .glass-surface` | inline rim, drop shadow, ring |
| Sidebar | `styles.css .glass-surface` (shared) | same |
| Agent right sidebar | `AgentRightSidebar.tsx` | same |
| Composer | `styles.css .composer-card` | duplicate glass recipe with `:focus-within` overrides |
| Command palette | `commandPalette/*` | shares `.glass-surface` |
| Popovers (ComposerSelectors, CommandPicker) | inline classes | inline `rounded-[10px] border …` |
| Toggles (`.glass-toggle`) | `styles.css` | inline ink/white rgba already partly tokenized |
| Switches (`.glass-switch`) | `styles.css` | same |
| Settings rails | `AppearanceTab.tsx` | inline `rounded-[12px] border border-line-soft bg-bg-main/30` |
| Cards (ReadinessCard, etc.) | inline | inline shadow strings |
| Scroll fades | `styles.css .scroll-fade-*` | mask gradients, no shadow tokens needed |
| Approval banner | `ApprovalBanner.tsx` | inline shadow / border |
| Markdown viewer | `MarkdownViewer.tsx` | shares PageModal slab |

### 3.2 — Token consumption table

| Surface | New tokens applied |
|---|---|
| `.glass-surface` (Glass mode) | `--glass-drop-{light,dark}`, `--glass-rim-{light,dark}`, `--ring-light/dark-soft` |
| `.glass-surface` (Linen mode) | `--shadow-linen-modal` (default tier), `--ring-light/dark` |
| `.composer-card` | same as `.glass-surface` for material; focus uses `--focus-ring` |
| `.modal-scrim` | unchanged literal for now (research note: keep 8 px scrim blur, dim tint) |
| Popovers (ComposerSelectors, CommandPicker, sidebar context menu) | `--shadow-linen-popover` + `--ring-light/dark`; in Glass mode they share `.glass-surface` |
| Settings rails | `--ring-light/dark-soft` (replaces `border-line-soft`) |
| Cards (ReadinessCard) | `--shadow-linen-rest` rest, `--shadow-linen-hover` on hover |
| Approval banner | `--shadow-linen-hover` + `--ring-light/dark` |
| Toggles / Switches | `--ring-light/dark` for track border, `--shadow-linen-rest` for pill/knob |
| Focus-visible (every button, input, slider) | `--focus-ring` |

### 3.3 — Motion consumption

| Call site | New token |
|---|---|
| `PageModal` enter/exit | material-aware: `ENTRY_GLASS` if material=glass, `ENTRY_LINEN` otherwise |
| `ApprovalReviewModal` | same as PageModal |
| `ToolViewer` | same |
| Sidebar slide (`App.tsx`) | `DURATION_ROUTE` + `EASE_EMPHASIZED` (unchanged) |
| Popover open (CommandPalette, ComposerSelectors) | `DURATION_POPOVER` + `EASE_DECELERATE` |
| Card hover | `DURATION_HOVER` + `EASE_HOVER` |
| Press feedback (buttons, app-row) | `SPRING_TAP` |
| Layout shifts (Motion `layout` props) | `SPRING_LAYOUT` |
| Thinking-indicator breathing | unchanged (timing already tuned) |

### 3.4 — Visible deltas (not no-ops)

These migrations *change appearance* on purpose; everything else is a substitution:

1. **Linen modal/popover shadows** become the documented stacked recipe instead of inline approximations. May feel slightly deeper.
2. **Light-mode settings rail border** moves from `border-line-soft` to `--ring-light-soft` — likely identical, but if `border-line-soft` was slightly off-spec it'll snap to the spec.
3. **Focus ring** unified — currently each input has its own focus treatment. Some lose their custom ring; all gain a consistent `--focus-ring`.
4. **Press scale** on app-rows / buttons / switches gains a consistent `SPRING_TAP` springiness if they don't already use one. Subtle.
5. **Glass entry decelerates** — `ENTRY_GLASS` replaces the spring on PageModal in Glass mode. Modal scale-up reads as "settling" instead of "bouncing".
6. **APCA-driven palette fixes** — if any palette × theme fails the body Lc ≥ 60 check, that color shifts. Likely small adjustments in the warm and notion palettes.

### 3.5 — Surfaces explicitly NOT migrated in this spec

- Chat message bubbles, tool cards, reasoning rows — these have unique shadow/border logic tied to message state, not material. Re-tokenizing them is a separate pass.
- Mermaid diagrams, code blocks — they have their own visual language.
- Loading / skeleton states — research recommends shimmer on linen only, but our current skeleton implementation isn't material-aware. Future task.

---

## Section 4 — Phases / sequencing

Six commits, each independently shippable. Each phase ends with `npx tsc --noEmit` clean and a visual diff that matches the "expected delta" column.

### Phase 1 — Token modules land, zero consumers

Create the three token modules and CSS vars. Existing code untouched. Visually a no-op.

- `apps/desktop/src/lib/tokens/color.ts` (8 palettes × 2 themes × ramps in OKLCH)
- `apps/desktop/src/lib/tokens/motion.ts` (springs/eases/durations/entry curves)
- `apps/desktop/src/lib/tokens/elevation.ts` (no runtime exports yet — just types)
- `styles.css`: add `--ring-*`, `--shadow-linen-*`, `--glass-*`, `--focus-ring` blocks at top of `:root` / `:root.dark`
- `scripts/validate-contrast.ts` (Bun script, prints APCA Lc per palette × theme)
- `docs/internal/contrast-report.md` (generated, committed)

**Expected delta**: none visually. Diff is purely additive.

### Phase 2 — Motion sweep

Rename + reroute every spring/ease/duration usage.

- Add `SPRING_MODAL` alias (= `SPRING_SMOOTH` body) so existing imports compile.
- Sweep `SPRING_SMOOTH` consumers (PageModal, ApprovalReviewModal, ToolViewer, …) to import the new name.
- Wire `ENTRY_GLASS` / `ENTRY_LINEN` selection into PageModal based on `useStore((s) => s.prefs.material)`.
- Replace inline `[0.2, 0.8, 0.2, 1]` with `EASE_DECELERATE`/`EASE_STANDARD` per use site.
- Delete `SPRING_SNAPPY`, `SPRING_BOUNCY` if unused after sweep; otherwise keep + document.

**Expected delta**: Glass mode modals decelerate softly instead of springing; Linen mode unchanged. Press feedback on buttons gains spring if missing.

### Phase 3 — Elevation sweep

Replace inline shadow strings.

- `.glass-surface` consumes `var(--glass-drop-*)` and `var(--glass-rim-*)`.
- `.composer-card` deduped against `.glass-surface` where possible; focus state uses `--focus-ring` overlay.
- Linen `.glass-surface[data-material=linen]` consumes `var(--shadow-linen-modal)` + `var(--ring-light/dark)`.
- Popover surfaces (ComposerSelectors, CommandPicker, sidebar context menu): explicit `.surface-popover` class with `var(--shadow-linen-popover)`.
- Settings rails: replace `border border-line-soft bg-bg-main/30` with a single `.surface-rail` class that uses `--ring-light/dark-soft` + bg.
- Cards: `.surface-card` class with `--shadow-linen-rest`, hover-state uses `--shadow-linen-hover`.

**Expected delta**: linen shadows look slightly more layered; popovers feel less flat in light mode. Focus rings unify.

### Phase 4 — Color sweep

Re-derive palettes in OKLCH; validate; alias old vars.

- Convert all 8 palettes to OKLCH literals in `color.ts`. Aliases on `:root` map `--color-bg` → `--color-neutral-1`, etc.
- Run `scripts/validate-contrast.ts`. For any palette × theme that fails Lc ≥ 60 on body, adjust the corresponding ramp step until it passes.
- Dark mode accent desaturation: pass through OKLCH chroma reduction (e.g. accent chroma 0.18 in light → 0.12 in dark) for every palette.
- Generate `docs/internal/contrast-report.md`.

**Expected delta**: subtle palette shifts in warm + notion (likely failing palettes). Dark-mode accents read less neon. Light-mode body text reads more confident.

### Phase 5 — Per-material differentiation polish

The "what makes glass feel like glass" and "what makes linen feel like linen" interaction details. All token-driven.

- Glass press: rim brightens (`--gp-rim` interpolates +0.15 for 80 ms via CSS transition on the rim alpha).
- Linen press: shadow depth shift (rest → hover token via inline transition).
- Glass hover on cards: rim drift (small `--gp-rim` bump on hover) — no `filter` (would composite separately from the backdrop and look detached) and no scale (research: glass hover is in-place luminance change, not motion).
- Linen hover on cards: shadow `rest → hover`, no rim animation (linen has a static ring, not a specular highlight).
- Glass enter: content fades 100 ms after slab (delayed children via Motion).
- Linen enter: content + slab together.

**Expected delta**: subtle but distinct "feel" between materials. The two modes become an actual style choice, not just a backdrop-filter on/off.

### Phase 6 — Cleanup

- Delete compatibility aliases (`--color-bg`, `SPRING_SMOOTH`, etc.) and sweep stragglers.
- Update `tasks/lessons.md` with anything that surfaced.
- Update `docs/internal/contrast-report.md` final.
- Optionally: add `lessons.md` entry naming the token files as the canonical source — anything inline is a regression.

**Expected delta**: none. Pure code cleanup.

### Phase ordering rationale

- Phase 1 is risk-free; lands first.
- Phase 2 + 3 are mechanically large but visually small — do them next while the token names are fresh.
- Phase 4 carries the most visible risk (palette shifts) — by the time we land it, motion + elevation are already on the new system, so the contrast diff is the only variable.
- Phase 5 is the "design" payoff phase — it depends on every prior phase but doesn't gate anything else.
- Phase 6 is cleanup.

**Estimated commit count**: 6 (one per phase). **Estimated reviewable diff**: each phase 200–800 lines; Phase 4 likely largest.

---

## Risks / known unknowns

1. **OKLCH browser support** — Electron Chromium is current; OKLCH and `color-mix(in oklab, …)` work. If we ever ship a web build, fallbacks needed.
2. **Catppuccin's 14-accent system** — current palette ships multi-accent. We'll re-derive in OKLCH but preserve the 14 accents as a separate `palette-catppuccin-extras` ramp; semantic ramps still apply.
3. **APCA cutoffs are opinion, not law** — Lc 60 is what Radix targets; we adopt it. If a palette starts feeling washed-out, we can lower to Lc 55 for body and re-validate.
4. **Per-material entry differences only land on PageModal** in Phase 2 — the other modals (ApprovalReviewModal, ToolViewer) compose their own portal/animation. They'd need a small refactor to share PageModal's material-aware entry, or we wire ENTRY_GLASS/LINEN into each manually. Decided per-modal in Phase 2.
5. **`prefers-reduced-transparency`** — out of scope, but if MacOS users with that setting open the app today, glass mode is unusable. Listed as a future task; not a blocker for this spec.

## Future tasks (not in this spec)

- APCA tooling integrated into the dev loop (currently one-shot script).
- `prefers-reduced-transparency` additive fallback.
- Skeleton/shimmer per material.
- Chat-specific token pass (message bubbles, tool cards, reasoning).
- Sound design tokens (research not commissioned yet).
- **Color alias retirement** (deferred from Phase 6): the `--color-*` legacy
  aliases in `:root` still resolve to `--color-neutral-N` / `--color-accent-N`.
  Per-palette `:root.palette-*` blocks continue to override them directly, so
  the aliases are load-bearing until those overrides are rewritten to consume
  the ramp tokens. Out of scope for this spec.

---

## Implementation status

Landed in six commits on `main`:

| Phase | Commit     | Notes                                                                                                                                                |
|-------|------------|------------------------------------------------------------------------------------------------------------------------------------------------------|
| P1    | `a65cae58` | Token modules + APCA report script. Zero behavior change.                                                                                            |
| P2    | `675e1b88` | Motion sweep — components migrated to `SPRING_MODAL` / `SPRING_CARD` / etc. Old `SPRING_SMOOTH/SNAPPY/BOUNCY` left in place with retiring comments.   |
| P3    | `e0dd896b` | Elevation sweep — `--shadow-{sm,md,lg}` tokens replace inline shadow strings outside chat surfaces.                                                  |
| P4    | `9251671a` | Color sweep — OKLCH ramps wired through, APCA fixes per palette. Final contrast: **80/80 PASS** across all 8 (palette × light/dark) combos.          |
| P5    | `3d55e36c` | Per-material interaction polish — glass rim drift via `@property`-registered custom prop; linen settle via spring.                                   |
| P6    | _(this)_   | Cleanup. Deleted `SPRING_SMOOTH/SNAPPY/BOUNCY` from `lib/motion.ts` and the back-compat re-export from `lib/tokens/motion.ts`.                       |

### Final state

- **Motion**: `lib/tokens/motion.ts` is canonical. `lib/motion.ts` retains only what isn't duplicated there: `MOTION` durations dict, `originFromEvent` / `modalOriginTransform` helpers, and the `SPRING_POPOVER` / `SPRING_TAP_RELEASE` / `SPRING_ROW_ENTRY` springs that have no token-module equivalent yet (still single-consumer each — promote when a second appears).
- **Color**: ramps live in `styles.css` `:root` and per-palette blocks. Semantic aliases (`--color-ink`, `--color-line`, etc.) resolve to ramp steps. Legacy `--color-*` aliases retained — see "deferred" item above.
- **Elevation**: `--shadow-{sm,md,lg}` tokens consumed via `shadow-[var(--shadow-N)]` Tailwind arbitrary values or direct `box-shadow` in CSS. A handful of bespoke chat-bubble shadows (Composer send button, Messages anchor pill) remain inline by design — content-overlay specific, not surface elevation.
- **Per-material entries**: only `PageModal` consumes `ENTRY_GLASS` / `ENTRY_LINEN`. `ApprovalReviewModal` and `ToolViewer` still compose their own portal animation via `modalOriginTransform` — listed as a future polish task.

### Verification (Phase 6)

- `npx tsc --noEmit` from `apps/desktop/`: clean.
- `bun run apps/desktop/scripts/validate-contrast.ts`: 80/80 PASS.
- No remaining importers of `SPRING_SMOOTH` / `SPRING_SNAPPY` / `SPRING_BOUNCY` anywhere in the source tree.
- No inline `[0.2, 0.8, 0.2, 1]` / `[0.32, 0.72, 0, 1]` ease tuples outside the motion modules themselves.
