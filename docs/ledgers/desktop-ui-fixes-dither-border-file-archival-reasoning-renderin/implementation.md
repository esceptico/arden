# Implementation

> A checked item means implemented, not verified. Observed proof lives in
> [verification.md](verification.md).

## Intended outcome

Fix all four reported issues at their root, per decisions D1–D4 in
[README.md](README.md).

## Checklist

### Issue 1 — dither overlaps the busy rim

- [x] **I-01 — Hold the dither field off the rim.** `apps/desktop/src/design/chat.css`
  — `.board-composer__strip-field` changes from `inset: 0; width: 100%; height: 100%`
  to `inset: var(--border-width) var(--border-width) 0` with
  `border-radius: calc(var(--r-panel) - var(--border-width)) … 0 0`, so the canvas is
  concentric with the panel and stops one rim-width short of it. `WorkingStrip.tsx`
  re-measures from `getBoundingClientRect` through its existing `ResizeObserver`, so no
  JS change was needed.
- [x] **I-02 — Leave the rim on the panel.** `.board-composer.is-working` keeps its own
  inset shadow (now expressed as `var(--border-width)` rather than a literal `1px`).
  An earlier attempt moved the rim to an `::after` overlay; that was **reverted** — see
  V-02, it measurably eroded the rim along the corner arc.

### Issue 2 — "file archival"

- [x] **I-03 — Persist `deleteCold`.** New `storage_allow_delete_cold_chats` on the
  config dataclass + `PERSIST_KEYS` (`apps/server/arden/config.py`), on
  `UpdateConfigRequest` (`apps/server/arden/server/schemas.py`), surfaced by
  `_config_response` (`apps/server/arden/server/routers/settings.py`). The plan
  request's `allow_delete_cold_chats` became `bool | None` so "omitted" is expressible,
  and `_storage_cleanup_plan` falls back to the stored value
  (`apps/server/arden/server/runtime/core.py`) — the same shape as its two siblings.
- [x] **I-04 — Stop the spurious 409.** `plan_id` in
  `apps/server/arden/storage_budget.py` now hashes the sorted work set
  (`kind`, `resource_id`, `estimated_reclaimable_bytes`) instead of whole-tree
  `before_bytes`. Log growth and `sessions.db`/`-wal` churn no longer move the hash; a
  changed candidate set still does. Known narrowing recorded in
  [research.md](research.md) follow-ups and V-05.
- [x] **I-05 — Remove dead `list_kept_backups`** (`storage_budget.py`), confirmed
  caller-free by repo-wide grep.
- [x] **I-06 — Split the tab and conform the UI.** New
  `apps/desktop/src/features/settings/components/StorageTab.tsx` takes the budget,
  inventory, policy, limits, preview/execute and backup list; `ArchiveTab.tsx` shrinks
  577 → 138 lines and owns archived chats alone. Both register in the existing
  `SETTINGS_NAV_GROUPS` `data` group; `SettingsTabId` gains `"storage"`. New intro copy
  on the Storage tab distinguishes backup archives from archived chats explicitly.
- [x] **I-07 — Make the switches persist structurally, not by patching the resync.**
  Each `SwitchControl` now reads `checked` straight from `serverConfig` and PATCHes on
  toggle. There is no mirror state left to be clobbered, so the revert-on-reconnect and
  revert-on-tab-switch failures are gone by construction rather than by guard. The
  numeric fields use a `draft ?? limitsOf(serverConfig)` pattern where `null` means
  "clean, follow the server".
- [x] **I-08 — Separate the verbs.** "Save & preview" splits into a dirty-gated
  "Save changes" and a "Preview cleanup" that sends only
  `{current_session_id, pinned_session_ids}` and writes no config. A failed execute now
  marks the plan stale and swaps the action for "Preview again" instead of leaving a
  dead plan next to a raw error.
- [x] **I-09 — Conform to the design system.** All 11 hand-rolled elements replaced with
  the primitives named in [research.md](research.md). The Tooltip-wrapped handler-less
  button was deleted outright rather than replaced — the category description became the
  row's `hint`, so no hover-only affordance is needed. `formatBytes` lifted to
  `apps/desktop/src/lib/format.ts` since both tabs need it.
- [x] **I-10 — Remove unused `maintainStorageApi`** (`apps/desktop/src/api/settings.ts`).

### Issue 3 — opt-in reasoning rendering

- [x] **I-11 — Add the pref.** `showReasoning: boolean` on `Prefs`
  (`apps/desktop/src/stores/types.ts`), `false` in `DEFAULT_PREFS`
  (`apps/desktop/src/stores/prefs.ts`). `PREFS_VERSION` deliberately not bumped — a new
  key falls through `{ ...DEFAULT_PREFS, ...parsed }`.
- [x] **I-12 — Unblock the renderer.** `apps/desktop/src/lib/messageVisibility.ts` gates
  **only** `hiddenInTranscript` on the pref; `breaksTurn` and `breaksActivity` are
  byte-unchanged, so activity grouping is untouched.
- [x] **I-13 — Thread the pref.** `Messages.tsx`, plus `sourceInspector.ts` and
  `SourcesPanel.tsx` so the two transcript projections cannot diverge.
- [x] **I-14 — Settings row.** `SettingsSettingRow` + `SwitchControl` in
  `AppearanceTab.tsx`, mirroring the `fontSmoothing` row.
- [x] **I-15 — Rehome the row.** It first landed under **Typography**, which is wrong for
  a chat-content toggle. Moved into the section that already owned the working strip,
  and that section renamed `Working strip` → **Agent activity**, dropping its
  `detail={…intensity}` chrome, which described only one of its now-two rows.

### Issue 4 — composer text placement

- [x] **I-16 — Centre the line box.** `.board-composer__input-row` in `chat.css` owns the
  box: `display:flex; align-items:center; min-height:3.5rem; padding: var(--space-3) …`.
  The editor's `min-h-[44px]` — which duplicated the row's 56px constraint and top-pinned
  the text — is gone, as is the asymmetric `pt-2`/`pb-1`.
- [x] **I-17 — Put the draft on the transcript axis.** Inline padding is
  `var(--content-gutter)`, the message lane's own value, applied to the input row, the
  working strip, the editing banner, and the image strip.
- [x] **I-18 — Align the toolbar optically.** `padding-inline: calc(var(--content-gutter)
  - (var(--control-size-large) - var(--icon-size)) / 2)` — the row is pulled back by the
  glyph's own inset so the first glyph lands on the text axis, not the button box.
- [x] **I-19 — Size the send button to its row.** `--icon-button-size` (28px) →
  `--control-size-large` (30px), the same fix the attach button already carried, which
  also lands the send glyph's right edge on the axis.
- [x] **I-20 — Remove duplication surfaced en route.** Component-side font-size /
  line-height (now in `chat.css` alone), the duplicated `min-h-14`, and the dead
  `.board-composer__input::placeholder` rule, which can never match a contenteditable.

### Post-review corrections (user feedback, 2026-08-05)

- [x] **I-22 — Revert the toolbar axis and send-button resize (I-18, I-19).** Both were
  my extrapolations beyond the approved text-axis decision, and pulling the corner
  controls 20px inboard left the send button visibly off its corner in the live app.
  Toolbar returns to its 8px inset, send to `--icon-button-size`. The guard test now
  asserts the toolbar does NOT reference `--content-gutter`, with the reason.
- [x] **I-23 — Reasoning rendered as FF thinking-steps.** `ReasoningMessage.tsx` reworked:
  new `lib/reasoningSteps.ts` parses line-leading `**Title**` markers (the shape OpenAI
  reasoning summaries arrive in) into labeled steps — killing the accidental bold — and
  the hardcoded `italic` class is gone. Steps render on a continuous hairline rail
  (no dots, per the app-wide rule), enter on the `TRACE_ROW` poses, the active step's
  label carries `.dp-running-text` shimmer + ellipsis, and the panel is
  `Collapse mode="height"` — the already-ported FF accordion body. The timeline
  auto-opens while streaming and folds down when the answer starts; a click overrides
  either way. Label/body sized per FF: both 13px, separated by weight and ink alone.
  Parser covered by 7 unit tests (`tests/reasoningSteps.test.ts`).

### Reasoning multi-part collapse (user report: "only one phrase per reasoning")

- [x] **I-24 — Separate streamed summary parts.** `apps/server/arden/llm/openai_responses.py`
  now handles `response.reasoning_summary_part.added` (and `reasoning_text.done`) as a
  boundary, appending `"\n\n"` as a real delta so the live stream and the collected
  fallback stay byte-identical. Previously that event was unhandled and a new part was
  indistinguishable from the next token of the current one.
- [x] **I-25 — Join parsed parts with a blank line.** `reasoning_content` was
  `"".join(reasoning_parts)`; each part is `**Title**\n\nbody`, so gluing buried every
  title after the first mid-line where it reads as inline bold rather than a heading.
- [x] **I-26 — Scope the summary fallback per item.** `if not reasoning_parts:` tested the
  response-wide accumulator, so any response carrying two or more reasoning items
  silently dropped every summary after the first — and, since the persisted
  `reasoning_content` comes from this path, reasoning visibly shrank on reload.
- [x] **I-27 — Keep every Anthropic thinking block.** `_parse_content_blocks` assigned
  `reasoning = block.thinking` per block, so a turn with several thinking blocks kept
  only the last. Now accumulated and joined with a blank line.
- [x] **I-28 — Drop the empty-body placeholder.** OpenAI emits `**Title**\n\n<!-- -->`
  for a heading with no body; Markdown renders the comment as nothing, leaving a step
  that measured as "has a body" while showing none — which also defeated the bodiless
  timeline's quiet styling.

### Guard tests

- [x] **I-21 — Rewrite the three composer guard tests** that pinned the old geometry as
  literal source strings, and add four covering the new contracts: the shared text axis,
  optical centring, the dither/rim clearance, and box metrics living in CSS rather than
  the className. All scoped to the rule body via the file's existing
  `match(/…\{([^}]*)\}/)` idiom — an unbounded `[\s\S]*?` silently matches across rules
  and makes negative assertions meaningless.
- [x] **I-29 — Cover the reasoning collapse.** New
  `apps/server/tests/test_reasoning_summary_parts.py` (6 tests) pins the streamed
  boundary, the parsed join, per-item scoping, `reasoning_text` precedence, and the
  Anthropic accumulation. `apps/desktop/tests/reasoningSteps.test.ts` grows to 10,
  including the placeholder body and a test that documents the old glued output still
  degrading to one step.

## Notes

- `POST /storage/maintain` is left in place. Its desktop caller
  (`maintainStorageApi`) is being removed as unused, but the method it wraps has three
  live server-side callers; whether an ops endpoint unreachable from the UI should stay
  is a product call, not a cleanup.
- The tree was swept into `stash@{0}` mid-session by a subagent running `git stash`.
  Rescued to `scratchpad/stash0-rescue.patch` and reapplied path-scoped. The stash entry
  is intentionally still present as a backup.
