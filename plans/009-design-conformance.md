# Plan 009: Design-language conformance pass — typography scale, dividers, radii, loading states

> **Executor instructions**: Follow this plan step by step. Run every
> verification command before moving on. On any STOP condition, stop and
> report. Update this plan's row in `plans/README.md` when done.
>
> **Drift check (run first)**: compare the "Current state" excerpts against
> the live code. Written against a dirty working tree at commit `57ec2d10`
> (branch `codex/memory-ledger-v2`). On mismatch, STOP. Plans 004/006/007
> touch some of the same files — run those first and re-verify line numbers.

## Status

- **Priority**: P2
- **Effort**: M
- **Risk**: LOW
- **Depends on**: 004, 006, 007 (same files; execute after)
- **Category**: tech-debt / design
- **Planned at**: commit `57ec2d10`, 2026-07-13

## Why this matters

The repo has a written design contract (`docs/design-language.md`): an
8-step type scale ("new sizes go in the scale, never inline"), "compactness
is spacing, not font size", "tone over lines" (borders only for real edges),
one house radius, skeleton loading with crossfade, and all motion values via
tokens. The memory view drifts on all of these — it reads at a different
scale than every other surface and several of its hairline dividers are
near-invisible in the light theme.

## Current state

All paths under `apps/desktop/`. The type scale (`src/styles.css:30-38`):

```css
--text-2xs: 11px;  --text-xs: 12.5px;  --text-sm: 13px;  --text-base: 14px;
--text-md: 15px;   --text-lg: 16.5px;  --text-xl: 18px;  --text-2xl: 20px;
```

Light-theme neutrals (`styles.css:46-52`): `--color-surface-sunken: #ebebeb`,
`--color-line: #e6e6e6`, `--color-line-soft: #ebebeb` — note line-soft equals
surface-sunken; at 55-60% opacity on white it is ~invisible (a previously
shipped bug class in this repo).

Findings, each with its exact site:

1. **Off-scale 29.6px titles**: `MemoryNote.tsx:50` and `MemoryEditor.tsx:130`
   both use `text-[1.85rem] font-semibold leading-[1.15] tracking-[-0.025em]`.
   Largest scale tier is 20px; other features title with tokens
   (`Home.tsx:86` uses `text-2xl`).
2. **Hand-written 15px body**: `MemoryNote.tsx:65`
   `className="max-w-none text-[15px] leading-[1.7]"` and `styles.css` rule
   `.memory-wysiwyg .ProseMirror { font-size: 15px; }` (~line 784) — the
   token `--text-md: 15px` already exists.
3. **Sub-scale 10px text**: `MemoryTimelineDisclosure.tsx:39`
   `<span className="ml-1.5 whitespace-nowrap text-[10px] text-faint">` —
   density-by-shrinking, the named anti-pattern.
4. **Near-invisible dividers**: reduced-opacity `line-soft` used as
   intra-content separators — `MemoryNote.tsx:71` (`border-t border-line-soft/60`
   on Properties), `MemoryNote.tsx:89` (metadata footer), `MemoryEditor.tsx:181`
   (editor footer), `MemoryInspector.tsx:93` (LinkRow `border-b border-line-soft/55`),
   `NotebookRail.tsx:89` (nested directory `border-l border-line-soft/60`).
5. **Radius sprawl**: five arbitrary radii in the feature — `rounded-[6px]`,
   `[7px]`, `[8px]`, `[9px]`, `[10px]` (e.g. `ArtifactMemoryView.tsx:1359`
   toolbar `rounded-[9px]`, buttons `rounded-[6px]`; `NotebookRail.tsx:242`
   `rounded-[7px]`; rows `rounded-[10px]`). House radius is 8px.
6. **Plain-text loading states**: `MemoryInspector.tsx:206,233,292` render
   `Loading links…` / `Loading evidence…` / `Loading page events…` as bare
   `<p>` where the same feature uses `ListSkeleton` (`NotebookRail.tsx:167`)
   and `Skeleton` (`MemoryNote.tsx:59-62`).
7. **Raw duration utility**: `MemoryTimelineDisclosure.tsx:17` uses
   `transition-transform duration-150` where the semantic token exists —
   `NotebookRail.tsx:219` does the identical chevron with `duration-check`.

## Commands you will need

| Purpose   | Command (run from `apps/desktop/`) | Expected on success |
|-----------|------------------------------------|---------------------|
| Typecheck | `bun run typecheck`                | exit 0              |
| Tests     | `bun test tests/`                  | all pass            |
| Lint      | `bun run lint`                     | exit 0              |

## Scope

**In scope**:
- `apps/desktop/src/features/memory/components/`: `MemoryNote.tsx`,
  `MemoryEditor.tsx`, `MemoryInspector.tsx`, `MemoryTimelineDisclosure.tsx`,
  `NotebookRail.tsx`, `ArtifactMemoryView.tsx` (class strings only)
- `apps/desktop/src/styles.css` (two changes only: the `.memory-wysiwyg
  .ProseMirror` font-size, and — Step 1 only if chosen — a new `--text-3xl`
  token)
- Test assertion updates in `tests/memory*.test.tsx` if any assert classes

**Out of scope**:
- Any file outside the memory feature — radius sprawl exists app-wide; this
  plan fixes memory only (a codebase-wide radius pass is a separate,
  deliberate effort).
- Behavior, markup structure, motion logic.
- `ScheduleChip.tsx` (`automations`) has the same 10px issue — out of scope
  here; noted for a future pass.

## Steps

### Step 1: Title size onto the scale

Decision (pre-made): add ONE new top tier to the scale rather than shrink the
note title — a 20px H1 over a 760px reading column is too small, and the doc
allows growing the scale ("new sizes go in the scale"). In `styles.css`
after `--text-2xl: 20px;` add `--text-3xl: 28px;`. Then in `MemoryNote.tsx:50`
and `MemoryEditor.tsx:130` replace `text-[1.85rem]` with `text-3xl` (keep
`font-semibold leading-[1.15] tracking-[-0.025em]`).

**Verify**: `grep -rn "1.85rem" apps/desktop/src` → no matches; `bun run typecheck` exit 0.

### Step 2: Body text onto the token

`MemoryNote.tsx:65`: `text-[15px]` → `text-md` (keep `leading-[1.7]`).
`styles.css` `.memory-wysiwyg .ProseMirror`: `font-size: 15px` →
`font-size: var(--text-md)`.

**Verify**: `grep -rn "text-\[15px\]" apps/desktop/src/features/memory` → no matches.

### Step 3: Kill the 10px text

`MemoryTimelineDisclosure.tsx:39`: `text-[10px]` → `text-2xs` (11px). The
de-emphasis is already carried by `text-faint`; size stays on-scale.

### Step 4: Dividers — real edges get real lines, grouping goes tonal

- `MemoryNote.tsx:71,89` and `MemoryEditor.tsx:181`: `border-line-soft/60` →
  `border-line` (full opacity; these are real content edges — Properties,
  footer). The Evidence disclosure (`MemoryTimelineDisclosure.tsx:15`)
  already uses plain `border-line` — that's the exemplar.
- `MemoryInspector.tsx:93` (LinkRow separators): replace
  `border-b border-line-soft/55 … last:border-b-0` with spacing-only rows —
  the parent `<ul>` already has `gap-1.5`; delete the border classes and keep
  `py-2` (tone-over-lines: list grouping needs no rules).
- `NotebookRail.tsx:89` (nested directory `ml-2 border-l border-line-soft/60 pl-2`):
  drop the `border-l border-line-soft/60`, keep `ml-2 pl-2` indentation
  (the design doc bans decorative rails; indentation carries nesting).
  NOTE: if plan 006 converted this region to `<details>`, apply to the new
  element.

**Verify**: `grep -rn "line-soft/5\|line-soft/6" apps/desktop/src/features/memory` → no matches.

### Step 5: Radii to the house values

Across the six in-scope components: `rounded-[8px]`/`[9px]`/`[10px]` →
`rounded-lg` (8px); `rounded-[6px]`/`[7px]` (inset icon buttons inside an
8px container) → `rounded-[5px]`? NO — use the concentric rule: inner radius
= outer radius − padding. The toolbar (`ArtifactMemoryView.tsx:1359`) is
`rounded-[9px] … p-1` with `rounded-[6px]` buttons — make it `rounded-lg p-1`
with `rounded-[4px]` buttons (8 − 4 = 4). Elsewhere, standalone size-7 icon
buttons (`NotebookRail.tsx:242,251`) → `rounded-md` if the Tailwind theme maps
it near 6px, else `rounded-[6px]` consistently. The invariant to enforce:
after this step the feature uses exactly TWO radii — `rounded-lg` for
cards/rows/containers and one small value for nested/inset controls.

**Verify**: `grep -rEn "rounded-\[(7|9|10)px\]" apps/desktop/src/features/memory` → no matches.

### Step 6: Inspector loading skeletons

`MemoryInspector.tsx:206,233,292`: replace the bare `<p>Loading …</p>` with
the `Skeleton` primitive used in `MemoryNote.tsx:59-62`
(`import { Skeleton } from "@/components/ui/Skeleton"`), e.g.
`<div role="status" aria-label="Loading links…" className="grid gap-1.5"><Skeleton lines={3} height={12} /></div>`.
Keep the `role="status"` + aria-label so existing a11y queries still match —
check `tests/memoryInspector.test.tsx` for `Loading links` queries and update
to the aria-label form if they queried text.

### Step 7: Duration token + gate

`MemoryTimelineDisclosure.tsx:17`: `duration-150` → `duration-check`.

**Verify**: from `apps/desktop/`: `bun run typecheck && bun run lint && bun test tests/` → all exit 0.

## Done criteria

- [ ] All greps in Steps 1–5 return no matches
- [ ] Feature uses tokens: `text-3xl`/`text-md`/`text-2xs`, `border-line`, two radii, `duration-check`
- [ ] Inspector loading states render skeletons with `role="status"`
- [ ] `bun run typecheck && bun run lint && bun test tests/` exit 0
- [ ] `plans/README.md` updated

## STOP conditions

- Excerpts don't match and the mismatch isn't explained by plans 004/006/007
  having landed (drift).
- `rounded-md`'s computed value is not ~6px in this Tailwind config — report
  the actual ramp before inventing a value.
- Adding `--text-3xl` conflicts with an existing utility or breaks another
  surface (grep `text-3xl` first — if it's already used somewhere expecting
  Tailwind's default 30px, report).

## Maintenance notes

- Pixel-verify both themes after landing (the light-theme line-soft collision
  is exactly how invisible borders shipped before): in the running app,
  inspect a Properties divider and a rail directory group in light AND dark,
  by computed hex, not by eye.
- Follow-ups deliberately not done here: app-wide radius normalization;
  `ScheduleChip.tsx` 10px text; a stylelint/eslint guard against `text-[Npx]`
  arbitrary values.
