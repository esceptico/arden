# UI primitives

Shared building blocks for the desktop app. **Reuse these — don't hand-roll.**
A new panel/row/field should be assembled from these, not re-derive their markup.
Props live in each file (kept here as a map, not a spec, so it can't go stale).

## Buttons & actions
- **Button** — text button; variants `primary | secondary | ghost | quiet | danger`, sizes `sm | md`, `leadingIcon`/`trailingIcon`, `active`.
- **IconButton** — icon-only button; sizes `xs(22) | sm | md | lg`, `shape square|circle`, `tone faint|muted|primary`, `danger`, `active`, `title`→Tooltip.
- **ConfirmDeleteButton** — two-step destructive control (neutral → armed).
- **CopyGlyph** / **ThemeToggle** — click-to-copy and light/dark toggle.

## Form inputs  (chrome = `.input-field` / `.input-field-sm`; never re-derive it)
- **Input** — labelled `<input>` (label/help/error/size). **Textarea** — labelled `<textarea>`.
- **SearchInput** — icon + input + clear + busy. **Tabs `variant="segmented"`** — 2–4 exclusive options (capsule track + sliding pill).
- **SwitchControl** — toggle (FF-tactile: hover/press knob morph + drag-to-toggle); pair with a `Tooltip` for help text (don't expand the row).
- **SliderComfortable** — FF labelled settings selector (`pips` for small discrete ranges, `scrubber` for large).
- settings forms: **Field** wraps Input; **NumberField / PercentField** (in `features/settings`) are **SliderComfortable** rows.

## Feedback & status
- **Callout** — alert/notice box (`tone bad|warn|ok|neutral`, icon/title/action). **Badge** / **Chip** — labels/tags. **Skeleton** — loading. **EmptyState** — icon + copy + action (the store-wired home screen lives at `features/home/components/Home.tsx`, not here — not generic).

## Overlays & menus
- **PageModal** — portal+scrim+panel modal shell (`origin`, `elevated`, `grid`, `header`). **AnchoredPopover** — cursor/trigger-anchored popover (`variant menu|popover`). **HoverPopover** / **Tooltip** — hover surfaces. **MenuItem** — one menu/popover row.

## Layout, lists, content
- **SurfaceCard** — interactive card shell (stretched click-target). **ListColumn** — list container. **Collapse** / **Tabs** + **TabPanels** / **PickerRow** — shared layout controls.
- **Markdown** / **MarkdownViewer** / **Mermaid** — rendered content.
- **DiffReview** — shared review shell. Memory edits pass complete before/after Markdown plus server operations; tool approvals use raw-only mode with a server-authored `rawPatch` and omit `operations`, so they never display or apply memory effects. Raw rendering is lazy-loaded, keyboard-accessible, wraps full-file comparisons, and preserves unified patches exactly. The server remains authoritative for every mutation.

## Motion
- **Reveal** — rise-in/dissolve row wrapper. **BlurSwap** — crossfade-on-key. **RollingToken** — odometer digit.

## Timeline
- **ThinkingStep** — one step in a vertical "thinking" timeline: a gutter `node` (icon/dot) topping the row with a connector drawn below it (hidden on `last`), and content stacked to the right (label + optional description). One unified treatment for both the live tail and the settled view. Span-based, so it's valid inside a `<button>`. The activity trace composes it with `operationLabel`, which turns a tool kind into a natural-language verb + icon.

## Hooks (`@/lib/hooks`)
`useFocusTrap` · `useEscapeKey` · `useReanchor` (overlay re-anchor) · `useListNav` (roving keyboard) · `useMutationState` (busy/saved/error) · `useTimeoutFlag` · `useTimeTicker` · `useVisibilityPoll`.

## CSS primitives (`styles.css`)
`.input-field` / `.input-field-sm` (input chrome) · `.app-row` (list-row: hover=colour, selected=bg tint) · `.surface-*` (elevation ladder).

## The rule
- **No ad-hoc.** If a primitive covers it, use it. A raw `<input>/<textarea>` carrying `.input-field` is an ESLint error (`no-restricted-syntax` → use Input/Textarea/Field).
- **Legit-raw exceptions** (don't force these onto a primitive): icon-only nav toggles with bespoke sizing, full-card/row stretched click-targets, the composer `<textarea>`/send button, deliberately-tiny dense controls (e.g. 16px sidebar buttons), inline-in-prose text links, `<select>`, and any element where matching the primitive would visibly change a deliberately-tuned surface. When in doubt, prefer the primitive; when it genuinely doesn't fit, a sibling component beats bloating the base.
