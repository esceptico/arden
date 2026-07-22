# Glass Settings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose per-variant control over the glass material framework via a new Settings tab; add Smoke + Milk variants; codify the layering rule.

**Architecture:** CSS variants consume `--gp-{variant}-{knob}` custom properties with hardcoded fallbacks. A new React effect writes those properties to `:root` from `prefs.glass`. A new `GlassTab` in SettingsModal renders 4 sliders × 6 variants with a live preview tile.

**Tech Stack:** React 19, TypeScript, Tailwind v4 + global stylesheet, zustand-style store via `useStore`.

---

## Task 1: CSS — add Smoke/Milk variants, convert all variants to custom properties, add layering doc

**Files:**
- Modify: `apps/desktop/src/styles.css`

This is one CSS edit that does three things:
1. Add `.glass-smoke` and `.glass-milk` rules.
2. Rewrite every variant (`.glass-surface` base + frosted/heavy/static/clear/smoke/milk) to consume `var(--gp-{variant}-{knob}, fallback)` for tint, blur, saturate, rim. Fallback values = current hardcoded values, so nothing changes visually until settings start writing properties.
3. Add a doc-comment block above `@layer components { … }` codifying the layering rule.

- [ ] **Step 1: Add Smoke variant**

Inside `@layer components { … }` in `apps/desktop/src/styles.css`, after `.glass-static` and its dark override, add:

```css
.glass-smoke {
  background-color: var(--gp-smoke-tint, rgba(10, 10, 30, 0.55));
  backdrop-filter:
    blur(var(--gp-smoke-blur, 20px))
    saturate(var(--gp-smoke-saturate, 120%));
  -webkit-backdrop-filter:
    blur(var(--gp-smoke-blur, 20px))
    saturate(var(--gp-smoke-saturate, 120%));
  box-shadow:
    var(--glass-drop, 0 4px 14px -4px rgba(0, 0, 0, 0.32)),
    inset 0 1px 0 rgba(255, 255, 255, var(--gp-smoke-rim, 0.06));
  /* Always dark — ignores host theme. Use for floating surfaces over
     saturated/photo backdrops where ink-text contrast matters. */
  color: rgba(255, 255, 255, 0.95);
}
```

- [ ] **Step 2: Add Milk variant**

After `.glass-smoke`:

```css
.glass-milk {
  background: var(--gp-milk-tint, rgba(255, 255, 255, 0.5));
  backdrop-filter:
    blur(var(--gp-milk-blur, 24px))
    saturate(var(--gp-milk-saturate, 140%));
  -webkit-backdrop-filter:
    blur(var(--gp-milk-blur, 24px))
    saturate(var(--gp-milk-saturate, 140%));
  box-shadow:
    var(--glass-drop, 0 8px 24px -12px rgba(31, 38, 135, 0.25)),
    inset 0 1px 0 rgba(255, 255, 255, var(--gp-milk-rim, 0.9));
}
:root.dark .glass-milk,
.glass-milk[data-tone="dark"] {
  background: var(--gp-milk-tint-dark, rgba(255, 255, 255, 0.18));
}
```

- [ ] **Step 3: Convert `.glass-surface` (frosted base) to custom properties**

Read the current `.glass-surface` rule (in the file, search for `.glass-surface {`). Its hardcoded values are roughly: background `rgba(255, 255, 255, 0.35)`, `backdrop-filter: blur(20px) saturate(180%)`. Replace the literal values with `var(--gp-frosted-{knob}, currentValue)` keeping the current values as fallbacks. Same for the dark override block.

If the surface uses inset top-edge specular shadows, replace the literal opacity in `inset 0 1px 0 rgba(255,255,255, X)` with `rgba(255,255,255, var(--gp-frosted-rim, X))`.

If the rule uses `--glass-tint` and `--glass-blur` internally already, keep those but reset them from the new `--gp-frosted-*` props.

- [ ] **Step 4: Convert `.glass-heavy` to custom properties**

Same pattern. Locate `.glass-heavy {` and its dark override `:root.dark .glass-heavy, .glass-heavy[data-tone="dark"] {`. Replace tint/blur/saturate/rim literals with `var(--gp-heavy-{knob}, currentValue)`.

- [ ] **Step 5: Convert `.glass-static` to custom properties**

Same pattern. `.glass-static` has `backdrop-filter: none` — the blur prop is moot here, but include `var(--gp-static-blur, 0px)` consistency. Tint goes through.

- [ ] **Step 6: Convert `.glass-clear` to custom properties**

Same pattern.

- [ ] **Step 7: Add layering doc comment**

Above the `@layer components { … }` block (right after the existing "Glass framework" header comment), insert:

```css
/* Glass layering rule:
   When stacking glass surfaces, each elevation step up = +4px blur,
   +5% tint, +0.05 rim opacity. Don't break this without reason —
   it's what makes depth read as depth instead of as visual noise. */
```

- [ ] **Step 8: Typecheck**

```sh
cd /Users/escept1co/src/arden/apps/desktop && bun run typecheck
```

Expected: clean exit. No CSS-affecting TS changes.

- [ ] **Step 9: Visual sanity check**

The app should look identical before and after this task. All fallback values match current defaults. If you spot any visual regression after loading the app, that means a value was transcribed wrong — fix it.

- [ ] **Step 10: Commit**

```sh
cd /Users/escept1co/src/arden
git add apps/desktop/src/styles.css
git commit -m "Add Smoke/Milk variants, parameterize glass via CSS custom properties

Every variant now consumes --gp-{variant}-{tint|blur|saturate|rim}
with the current hardcoded value as fallback. Visually invisible
until the upcoming GlassTab starts writing properties. Smoke and
Milk are new variants per the demo-stealing pass. Layering rule
codified as a doc comment above the framework block."
```

---

## Task 2: Store types + defaults + effect

**Files:**
- Modify: `apps/desktop/src/store/types.ts`
- Modify: `apps/desktop/src/store/prefs.ts`
- Modify: `apps/desktop/src/lib/theme.ts` (or create sibling `apps/desktop/src/lib/glass.ts`)
- Modify: `apps/desktop/src/components/App.tsx` (wire the new effect)

- [ ] **Step 1: Add types in `apps/desktop/src/store/types.ts`**

Find where other style/preference types live (search for `GlassDensity` or `PaletteId` — actually `GlassDensity` was reverted, so look near `PaletteId`). Insert:

```ts
export type GlassVariantId =
  | "frosted"
  | "heavy"
  | "static"
  | "clear"
  | "smoke"
  | "milk";

export interface GlassParams {
  /** Tint opacity 0–100 (% white for light mode; framework derives dark). */
  tint: number;
  /** Backdrop-filter blur in px. */
  blur: number;
  /** Backdrop-filter saturate %. */
  saturate: number;
  /** Top-edge specular rim opacity 0–100 (%). */
  rim: number;
}

export type GlassPrefs = Record<GlassVariantId, GlassParams>;
```

Find the `Prefs` interface and add the field:

```ts
export interface Prefs {
  // ... existing fields
  glass: GlassPrefs;
}
```

- [ ] **Step 2: Add defaults in `apps/desktop/src/store/prefs.ts`**

Find `DEFAULT_PREFS`. Above it, add:

```ts
export const DEFAULT_GLASS_PREFS: GlassPrefs = {
  frosted: { tint: 35, blur: 20, saturate: 180, rim: 60 },
  heavy:   { tint: 18, blur: 40, saturate: 180, rim: 75 },
  static:  { tint: 86, blur: 0,  saturate: 100, rim: 60 },
  clear:   { tint: 4,  blur: 2,  saturate: 160, rim: 35 },
  smoke:   { tint: 55, blur: 20, saturate: 120, rim: 6 },
  milk:    { tint: 50, blur: 24, saturate: 140, rim: 90 },
};
```

(Tint for `.glass-smoke` is 55% darkness — interpretation: tint is "fill opacity" regardless of color. The CSS variable receives the opacity component; the color is part of the variant's fallback recipe. UI labels just say "Tint".)

Import `GlassPrefs` from `./types`.

Add to `DEFAULT_PREFS`:

```ts
glass: DEFAULT_GLASS_PREFS,
```

- [ ] **Step 3: Create the glass effect**

Create `apps/desktop/src/lib/glass.ts`:

```ts
import { useEffect } from "react";
import { useStore } from "../store";
import type { GlassParams, GlassPrefs, GlassVariantId } from "../store";

const VARIANT_IDS: GlassVariantId[] = [
  "frosted",
  "heavy",
  "static",
  "clear",
  "smoke",
  "milk",
];

/** Write the user's glass prefs onto :root as CSS custom properties.
 *  Every .glass-{variant} rule reads these via `var(--gp-{variant}-X, fallback)`,
 *  so absent props fall back to the variant's hardcoded defaults. */
function applyGlassPrefs(glass: GlassPrefs): void {
  const root = document.documentElement;
  for (const id of VARIANT_IDS) {
    const p = glass[id];
    if (!p) continue;
    setVariant(root, id, p);
  }
}

function setVariant(
  root: HTMLElement,
  id: GlassVariantId,
  p: GlassParams,
): void {
  // Tint is a 0–100% value the user adjusts. We resolve it to an alpha
  // and inject as a full rgba() so variants don't have to compose it.
  const alpha = clamp(p.tint, 0, 100) / 100;
  root.style.setProperty(`--gp-${id}-tint`, tintFor(id, alpha));
  root.style.setProperty(`--gp-${id}-blur`, `${clamp(p.blur, 0, 60)}px`);
  root.style.setProperty(`--gp-${id}-saturate`, `${clamp(p.saturate, 0, 400)}%`);
  root.style.setProperty(`--gp-${id}-rim`, String(clamp(p.rim, 0, 100) / 100));
}

/** Each variant's base color differs (white vs ink-blue for smoke). The
 *  user-controlled alpha is mixed onto that base. */
function tintFor(id: GlassVariantId, alpha: number): string {
  if (id === "smoke") return `rgba(10, 10, 30, ${alpha})`;
  return `rgba(255, 255, 255, ${alpha})`;
}

function clamp(n: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, n));
}

/** Subscribes to prefs.glass and applies the params to :root every change. */
export function useGlassEffect(): void {
  const glass = useStore((s) => s.prefs.glass);
  useEffect(() => {
    applyGlassPrefs(glass);
  }, [glass]);
}
```

- [ ] **Step 4: Wire `useGlassEffect` in `App.tsx`**

Find where `useThemeEffect()` is called in `apps/desktop/src/components/App.tsx`. Right after that call, add:

```tsx
useGlassEffect();
```

Add the import at the top:

```tsx
import { useGlassEffect } from "../lib/glass";
```

- [ ] **Step 5: Typecheck**

```sh
cd /Users/escept1co/src/arden/apps/desktop && bun run typecheck
```

Expected: clean.

- [ ] **Step 6: Manual smoke test**

Run the app, open dev tools, evaluate:

```js
document.documentElement.style.getPropertyValue('--gp-frosted-tint')
```

Expected: `"rgba(255, 255, 255, 0.35)"`. App still looks the same.

- [ ] **Step 7: Commit**

```sh
cd /Users/escept1co/src/arden
git add apps/desktop/src/store/types.ts apps/desktop/src/store/prefs.ts apps/desktop/src/lib/glass.ts apps/desktop/src/components/App.tsx
git commit -m "Glass prefs: types, defaults, and useGlassEffect

Adds prefs.glass (per-variant tint/blur/saturate/rim) seeded from the
current styles.css defaults. useGlassEffect writes the values onto :root
as --gp-{variant}-{knob} CSS custom properties on every change."
```

---

## Task 3: RangeField primitive

**Files:**
- Create: `apps/desktop/src/components/settings/RangeField.tsx`

A slider input matching the visual language of the existing `Field`/`NumberField`/`PercentField` in `apps/desktop/src/components/settings/Field.tsx`. Single-purpose: a labeled `<input type="range">` with value display.

- [ ] **Step 1: Create the file**

```tsx
import type { CSSProperties } from "react";

interface RangeFieldProps {
  label: string;
  value: number;
  onChange: (next: number) => void;
  min: number;
  max: number;
  step?: number;
  /** Suffix shown after the value (e.g. "px", "%"). */
  unit?: string;
  help?: string;
}

export function RangeField({
  label,
  value,
  onChange,
  min,
  max,
  step = 1,
  unit,
  help,
}: RangeFieldProps) {
  return (
    <div className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-4 gap-y-1">
      <div className="grid gap-0.5">
        <label className="text-sm font-medium text-ink">{label}</label>
        {help && <span className="text-xs text-faint leading-[1.4]">{help}</span>}
      </div>
      <div className="flex items-center gap-2 w-[200px]">
        <input
          type="range"
          value={value}
          min={min}
          max={max}
          step={step}
          onChange={(e) => onChange(Number(e.target.value))}
          className="flex-1 accent-accent cursor-pointer"
        />
        <span className="w-12 text-right text-sm text-ink-soft tabular-nums font-mono">
          {Math.round(value)}{unit ?? ""}
        </span>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Typecheck**

```sh
cd /Users/escept1co/src/arden/apps/desktop && bun run typecheck
```

Expected: clean.

- [ ] **Step 3: Commit**

```sh
cd /Users/escept1co/src/arden
git add apps/desktop/src/components/settings/RangeField.tsx
git commit -m "Add RangeField primitive (labeled slider with value display)"
```

---

## Task 4: GlassTab + SettingsModal wire

**Files:**
- Create: `apps/desktop/src/components/settings/GlassTab.tsx`
- Modify: `apps/desktop/src/components/SettingsModal.tsx`

- [ ] **Step 1: Create `GlassTab.tsx`**

```tsx
import { useState } from "react";
import clsx from "clsx";
import { useStore } from "../../store";
import type { GlassParams, GlassVariantId } from "../../store";
import { DEFAULT_GLASS_PREFS } from "../../store/prefs";
import { RangeField } from "./RangeField";

const VARIANTS: { id: GlassVariantId; label: string; desc: string }[] = [
  { id: "frosted", label: "Frosted", desc: "Default — readable foreground, lively background." },
  { id: "heavy", label: "Heavy", desc: "Focused-attention popovers; thicker material, stronger blur." },
  { id: "static", label: "Static", desc: "Solid surface, no blur. Use when backdrop bleed-through hurts." },
  { id: "clear", label: "Clear", desc: "Minimal material; near-transparent." },
  { id: "smoke", label: "Smoke", desc: "Always-dark glass; ignores theme. For high-contrast over photo/video." },
  { id: "milk", label: "Milk", desc: "Content-safe tier between Frosted and Static." },
];

export function GlassTab() {
  const glass = useStore((s) => s.prefs.glass);
  const setPrefs = useStore((s) => s.setPrefs);
  const [selected, setSelected] = useState<GlassVariantId>("frosted");

  function update(variant: GlassVariantId, patch: Partial<GlassParams>): void {
    setPrefs({
      glass: {
        ...glass,
        [variant]: { ...glass[variant], ...patch },
      },
    });
  }

  function resetVariant(variant: GlassVariantId): void {
    setPrefs({
      glass: { ...glass, [variant]: DEFAULT_GLASS_PREFS[variant] },
    });
  }

  function resetAll(): void {
    setPrefs({ glass: DEFAULT_GLASS_PREFS });
  }

  const variant = VARIANTS.find((v) => v.id === selected)!;
  const params = glass[selected];

  return (
    <div className="grid gap-4">
      <div className="flex items-center justify-between gap-3">
        <p className="m-0 text-sm text-muted leading-[1.45] max-w-[520px]">
          Tune each glass material independently. Changes apply live to every
          surface using that variant — including this settings window itself.
        </p>
        <button
          type="button"
          onClick={resetAll}
          className="text-xs font-medium text-muted hover:text-ink transition-colors"
        >
          Reset all
        </button>
      </div>

      <div className="grid grid-cols-[180px_minmax(0,1fr)] gap-4">
        {/* Left rail — variant picker */}
        <ul className="m-0 p-0 list-none grid gap-1">
          {VARIANTS.map((v) => (
            <li key={v.id}>
              <button
                type="button"
                onClick={() => setSelected(v.id)}
                data-active={v.id === selected ? "true" : undefined}
                className={clsx(
                  "w-full text-left px-3 py-2 rounded-md transition-colors",
                  v.id === selected
                    ? "bg-surface-soft text-ink"
                    : "text-ink-soft hover:bg-surface-soft/60 hover:text-ink",
                )}
              >
                <div className="flex items-center gap-2.5">
                  <span
                    className={`glass-surface glass-${v.id} glass-radius-sm`}
                    style={{ width: 28, height: 18, borderRadius: 6 }}
                    aria-hidden
                  />
                  <span className="text-sm font-medium">{v.label}</span>
                </div>
              </button>
            </li>
          ))}
        </ul>

        {/* Right pane — controls + preview */}
        <div className="grid gap-4">
          <div className="flex items-baseline justify-between gap-2">
            <div>
              <h3 className="m-0 text-base font-medium text-ink">{variant.label}</h3>
              <p className="m-0 mt-0.5 text-xs text-faint leading-[1.4]">{variant.desc}</p>
            </div>
            <button
              type="button"
              onClick={() => resetVariant(selected)}
              className="text-xs font-medium text-muted hover:text-ink transition-colors"
            >
              Reset
            </button>
          </div>

          {/* Live preview tile */}
          <GlassPreview variant={selected} />

          {/* Sliders */}
          <div className="grid gap-3">
            <RangeField
              label="Tint"
              value={params.tint}
              onChange={(v) => update(selected, { tint: v })}
              min={0}
              max={100}
              unit="%"
            />
            <RangeField
              label="Blur"
              value={params.blur}
              onChange={(v) => update(selected, { blur: v })}
              min={0}
              max={60}
              unit="px"
            />
            <RangeField
              label="Saturate"
              value={params.saturate}
              onChange={(v) => update(selected, { saturate: v })}
              min={0}
              max={250}
              unit="%"
            />
            <RangeField
              label="Rim"
              value={params.rim}
              onChange={(v) => update(selected, { rim: v })}
              min={0}
              max={100}
              unit="%"
            />
          </div>
        </div>
      </div>
    </div>
  );
}

/** Live preview tile. A glass card sits over a constrained "busy backdrop"
 *  (mesh gradient + a single line of scrolling text) so the user can see
 *  exactly what their adjustments do without leaving Settings. */
function GlassPreview({ variant }: { variant: GlassVariantId }) {
  return (
    <div
      className="relative overflow-hidden rounded-[12px] border border-line-soft"
      style={{ height: 160 }}
    >
      <div
        className="absolute inset-0"
        style={{
          background: `
            radial-gradient(at 15% 30%, #ff3b8d 0px, transparent 45%),
            radial-gradient(at 85% 25%, #6a5af9 0px, transparent 45%),
            radial-gradient(at 70% 80%, #00d4ff 0px, transparent 45%),
            radial-gradient(at 25% 75%, #ffa84d 0px, transparent 45%),
            #08081a
          `,
        }}
        aria-hidden
      />
      <div
        className="absolute inset-0 flex items-center"
        style={{
          fontFamily: "'Times New Roman', serif",
          fontSize: 96,
          fontWeight: 700,
          color: "rgba(255,255,255,0.6)",
          letterSpacing: "-0.04em",
          whiteSpace: "nowrap",
          mixBlendMode: "overlay",
        }}
        aria-hidden
      >
        <span style={{ animation: "glassPreviewScroll 22s linear infinite" }}>
          DESIGN · GLASS · LIGHT · DESIGN · GLASS · LIGHT ·{" "}
        </span>
      </div>
      <div
        className={`glass-surface glass-${variant}`}
        style={{
          position: "absolute",
          top: "50%",
          left: "50%",
          transform: "translate(-50%, -50%)",
          width: 240,
          height: 100,
          borderRadius: 16,
          display: "grid",
          placeItems: "center",
          fontSize: 13,
          fontWeight: 500,
        }}
      >
        Preview surface
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Add the preview keyframe to `styles.css`**

Add near other keyframes (look for `@keyframes pulseSoft` or similar):

```css
@keyframes glassPreviewScroll {
  from { transform: translateX(0); }
  to   { transform: translateX(-50%); }
}
```

- [ ] **Step 3: Wire `GlassTab` into `SettingsModal.tsx`**

Read `apps/desktop/src/components/SettingsModal.tsx` first to see the tab pattern. The existing tabs are likely defined as an array or render block. Find where `AppearanceTab` is imported/rendered. Add `GlassTab` next to it.

Three concrete edits:
1. Add an import: `import { GlassTab } from "./settings/GlassTab";`
2. Add an entry to whatever tab list defines the available tabs (label "Glass", id `"glass"`).
3. Add a render branch: `{active === "glass" && <GlassTab />}`.

If the tab labels are defined as a typed union (e.g. `type SettingsTabId = "appearance" | "models" | ...`), extend it to include `"glass"`.

- [ ] **Step 4: Typecheck**

```sh
cd /Users/escept1co/src/arden/apps/desktop && bun run typecheck
```

Expected: clean.

- [ ] **Step 5: Manual test**

Run the app, open Settings → Glass.
- Pick "Frosted". Drag Tint to 80. Confirm: the preview tile AND the surrounding SettingsModal chrome both update in real time.
- Pick "Smoke". Confirm the new variant renders with a dark fill.
- Click "Reset". Sliders snap back.
- Click "Reset all". All variants reset.
- Close and reopen Settings. Values persist.

- [ ] **Step 6: Commit**

```sh
cd /Users/escept1co/src/arden
git add apps/desktop/src/components/settings/GlassTab.tsx apps/desktop/src/styles.css apps/desktop/src/components/SettingsModal.tsx
git commit -m "Add Glass tab to Settings — per-variant tuning + live preview

Six variants (Frosted, Heavy, Static, Clear, Smoke, Milk) each
expose four sliders: Tint, Blur, Saturate, Rim. Changes apply live
to every surface using that variant — including the SettingsModal
itself, giving immediate WYSIWYG feedback. Per-variant Reset and
global Reset all buttons. Values persist via prefs."
```

---

## Verification across all tasks

After every task: `cd apps/desktop && bun run typecheck` must exit clean.

End-to-end manual test after task 4:
1. Open the app fresh, open Settings → Glass. Tab loads with 6 variants in the left rail.
2. Click each variant — controls swap correctly.
3. Drag every slider end-to-end on Frosted — no crash, preview updates smoothly.
4. Reload the app — variant values persist.
5. Click Reset all — all 24 values restore. Reload — defaults persist.
