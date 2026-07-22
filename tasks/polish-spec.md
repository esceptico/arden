# ARDEN Desktop — Production Polish Spec

Shared contract for the multi-agent polish pass on `apps/desktop`. Every agent
reads this file first. The goal is **Linear/Vercel-level craft**: minimal,
precise, quiet, dense-where-useful, strong alignment, restrained color, crisp
typography. No generic AI-SaaS fluff.

This codebase is already mature and disciplined (OKLCH ramps, motion/spring/ease
tokens, a full surface framework, reduced-motion handling, zero `transition:
all`). **This is a consistency-and-craft pass, not a redesign.** Do not repaint,
do not re-architect, do not add dependencies. Preserve the existing identity.

---

## 0. Hard constraints (violating any of these is a failure)

- **Preserve all existing animations.** Refine timing / easing / stagger /
  transform-origin only where it *clearly* improves feel. Never delete an
  animation unless it demonstrably hurts UX (and say why).
- **No token or palette repaint.** Do not change the OKLCH ramps, palette
  anchors, or the four palettes. Color work = fixing contrast / using the right
  existing token, never inventing colors.
- **Use the design tokens, never literals.** Durations → `--duration-*` /
  `MOTION.*`. Eases → `--ease-*` / the `EASE_*` exports. Springs → `SPRING_*`.
  Shadows/rings → `--shadow-linen-*` / `--ring-*` / the `elevation.ts` exports.
  Surfaces → `.surface-*` classes. Colors → `--color-*` / Tailwind `*-ink`,
  `*-muted`, `*-line`, `*-accent`, etc. If you find a hardcoded `200ms`,
  `cubic-bezier(...)`, or hex that duplicates a token, replace it with the token.
- **Surgical & evidence-based.** Only make changes you can justify against the
  checklist in §3. When unsure, leave it and note it. Behavior must not change.
- **No new files, no new deps, no renames.** Edit in place.
- **TypeScript must still compile.** `cd apps/desktop && bunx tsc --noEmit` clean.
- **File ownership is exclusive.** Only edit files in your assigned partition
  (§4). Surface agents must NOT edit `styles.css`, `src/lib/tokens/*`, or any
  foundation primitive — those belong to the Foundation agent, which runs first.
  If you need a shared change, list it as a "deferred shared change" in output.

## 1. Design North Star (Linear / Vercel)

- **Alignment & rhythm.** Everything aligns to a consistent grid. Optical
  alignment of icons to text baselines. Consistent gaps from the spacing scale
  (4/8/12/16/24/32). Kill one-off `gap-[7px]`, `mt-[3px]` magic numbers that
  break rhythm — round to the scale unless the offset is intentional optical
  correction (then comment it).
- **Density where useful.** Lists, rows, tables: tight, scannable, no wasted
  vertical space. Generous where it's a focal surface (composer, empty states).
- **Restraint.** One accent, used sparingly (focus, active, primary CTA). No
  decorative gradients, no glass-as-default, no shimmer where it adds nothing.
- **Typography.** Hierarchy via size + weight from the existing `--text-*`
  scale. Tight tracking on larger text. Muted text must still pass contrast
  (see §3). No all-caps body; uppercase only for ≤4-word labels.
- **Crisp edges.** Hairline borders, the sub-pixel inner ring. Radii from the
  `--surface-radius-*` / `rounded-*` scale — never one-off huge radii.

## 2. Motion rules (Emil Kowalski's framework)

- **Frequency gates animation.** Actions done 100+×/day (keyboard shortcuts,
  command palette toggle) → little/no animation. Occasional (modals, drawers,
  toasts) → standard. Rare/first-run → can delight. Never make a
  keyboard-initiated action wait on an animation.
- **Easing:** entering/exiting → ease-out (`--ease-out-soft`, `EASE_OUT`, or
  `EASE_HOVER`); moving/morphing on-screen → ease-in-out (`--ease-emphasized`,
  `EASE_EMPHASIZED`); hover/color → `ease`/`EASE_HOVER`; constant → linear.
  **Never `ease-in` on UI.** Never `transition: all` (specify properties).
- **Duration:** press 100–160ms; tooltip/small popover 125–200ms; dropdown/select
  150–250ms; modal/drawer 200–500ms. UI interactions stay <300ms. Use the
  `--duration-*` scale.
- **Origin-aware popovers.** Popovers/menus scale from their trigger, not
  center. Modals are the exception — they stay centered (or grow from the
  `modalOriginTransform` origin already wired in `motion.ts`).
- **Never scale from 0.** Enter from `scale(0.95–0.98)` + opacity, not
  `scale(0)`.
- **Press feedback.** Pressable elements get a subtle `scale(0.96–0.98)` active
  state (the app already does this via `.app-row` / `.sidebar button` — make
  sure new/standalone buttons match).
- **Interruptible UI → transitions, not keyframes.** Rapidly-retriggered things
  (toasts, toggles, hover) use CSS transitions / springs so they retarget.
- **Only animate transform & opacity** for anything that runs hot. No animating
  width/height/margin/padding/top/left in hot paths.
- **Reduced motion** is already globally handled — don't regress it; if you add
  a transform-based entrance, confirm the reduced-motion block neutralizes it.
- **Stagger** list entrances 30–80ms between items; never block interaction on it.

## 3. Production checklist (apply per component)

For each component in your partition, verify and fix:

1. **Contrast** — body text ≥4.5:1, large text ≥3:1, placeholders ≥4.5:1. The
   common failure is muted gray on a tinted surface. If `text-faint` is used for
   readable content (not decorative chrome), bump to `text-muted`/`text-ink-soft`.
   Cross-check `docs/internal/contrast-report.md`.
2. **Focus states** — every interactive element has a visible `:focus-visible`
   (the global ring covers `button`/`[role=button]`/`[role=switch]`/`[role=tab]`;
   inputs own theirs). Flag anything focusable that shows nothing.
3. **Hierarchy** — clear primary/secondary/tertiary. No flat walls of same-weight
   same-size text. Section headers consistent (use `SectionHeader`).
4. **Spacing & alignment** — from the scale; consistent within and across sibling
   components; icons optically aligned to text.
5. **Empty / loading / error states** — present and on-brand. Empty states use
   `EmptyState`. Loading uses `.skeleton` or existing spinners, not layout jank.
   Errors are legible (not raw), actionable where possible.
6. **Interaction detail** — hover, active, disabled, selected all defined and
   consistent. Hit targets ≥28px. Disabled = `opacity:.45 cursor:not-allowed`
   (global default exists; don't re-roll it).
7. **Responsiveness** — the window min is 920×600; panels resize. Check nothing
   overflows or clips at narrow widths (composer already uses container queries).
8. **Motion** — per §2.
9. **Copy** — button labels are verb+object; no em dashes; no buzzwords; errors
   say what happened and what to do.
10. **Consistency with siblings** — a row in your surface should match rows in
    other surfaces (same padding, radius, hover treatment via `.app-row`).

## 4. File ownership (exclusive partitions)

**FOUNDATION** (runs first; owns all shared/global): `src/styles.css`,
`src/lib/tokens/{color,elevation,motion,index}.ts`, and primitives:
`Badge.tsx`, `Chip.tsx`, `IconButton.tsx`, `SegmentedControl.tsx`,
`SwitchControl.tsx`, `PageModal.tsx`, `SectionHeader.tsx`, `EmptyState.tsx`,
`ScrollBlur.tsx`, `Toaster.tsx`, `ErrorBoundary.tsx`, `PickerRow.tsx`,
`ui/Tabs.tsx`, `ui/TabPanels.tsx`.

**CHAT**: `Chat.tsx`, `Messages.tsx`, `Message.tsx`, `TurnGroup.tsx`,
`Composer.tsx`, `ComposerSelectors.tsx`, `CommandPicker.tsx`, `Markdown.tsx`,
`MarkdownViewer.tsx`, `Mermaid.tsx`, `ToolViewer.tsx`, `CompactionIndicator.tsx`,
`QuickCapture.tsx`, `GoalStrip.tsx`, `composer/BudgetDial.tsx`,
`composer/LoopStatus.tsx`.

**NAV**: `App.tsx`, `SidebarResizeHandle.tsx`, all `sidebar/*`, all
`commandPalette/*`.

**SETTINGS**: `SettingsModal.tsx`, all `settings/*` (incl. `settings/mcp/*`),
`automations/AutomationEditor.tsx`, `AutomationsModal.tsx`, `ArchiveModal.tsx`,
`ApprovalBanner.tsx`, `ApprovalReviewModal.tsx`.

**MEMTRACE**: `MemoryModal.tsx`, all `memory/*`, all `trace/*`,
`AgentRightSidebar.tsx`, `QueueCard.tsx`, `ReadinessCard.tsx`.

## 5. Reference docs (read what's relevant to your partition)

- `apps/desktop/docs/internal/ui-ux-intelligence.md` — the project's own motion
  & UX direction. Authoritative; align to it.
- `apps/desktop/docs/internal/apple-design-intel.md` — tap/active state spec,
  surface treatment.
- `apps/desktop/docs/internal/contrast-report.md` — known contrast findings.

## 6. Output (every implementer agent returns this)

A structured report: list of changes `{file, what, why, category}`, the count of
files touched, any **deferred shared changes** (things needing `styles.css` /
token edits you were not allowed to make), and any **risks/regressions** to
double-check. Be honest about what you did NOT change and why.
