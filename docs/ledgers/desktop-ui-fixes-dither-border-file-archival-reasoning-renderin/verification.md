# Verification

## Status

All four issues implemented and the full gate is green on both sides. Two behaviour
claims remain unproven in the running app — see Failures and gaps.

## Method note

The desktop API requires a bearer key I did not handle, so the running app could not be
driven end to end. Issues 1 and 4 are geometry claims, so they were verified against
**real rendered pixels**: a harness at `scratchpad/composer-harness.html` links the
project's actual `base.css` / `foundation.css` / `chat.css` and reproduces the composer
DOM plus a message-lane paragraph, painted by the same ordered-dither loop constants as
`WorkingStrip.tsx`. Headless Chrome via puppeteer, `deviceScaleFactor` 2–14. This proves
the CSS box math and paint order; it does not exercise React state or the live server.

## Evidence

| ID | Related work | Check | Expected | Observed | Result | Evidence and time |
| --- | --- | --- | --- | --- | --- | --- |
| V-01 | I-12..I-15 | Rendered geometry probe | draft text on the message axis; line optically centred | `axisDelta: 0` (message 288, draft 288); `stripLabelLeft: 288`; `toolGlyphLeft: 288`; `spaceAbove: 17.99` = `spaceBelow: 17.99`; `rowHeight: 56`; `sendBox: 30x30`; `sendGlyphRight: 992` vs `panelRight: 1020` = 28px | pass | `scratchpad/shoot.mjs`, 2026-08-05 |
| V-02 | I-02 | Accent pixels on the 23px corner arc, `::after` overlay vs panel inset shadow | overlay ≥ baseline | overlay **12** on-arc vs baseline **33** — the overlay sits on the panel's own `overflow` clip boundary and is eroded | **fail → reverted** | `scratchpad/px.mjs`, 2026-08-05 |
| V-03 | I-01 | Same probe, after insetting the field instead | rim intact and no longer overpainted | on-arc accent **49** after vs **29** before; rim renders as one unbroken arc, dots stop short of it | pass | `scratchpad/rim-before.png` / `rim-after.png` @14x, 2026-08-05 |
| V-04 | I-17 | `bun test` on the three guard files | all pass | `27 pass, 0 fail, 1050 expect()` | pass | 2026-08-05 |
| V-05 | I-03..I-05 | Server suite | green | `ruff check` → `All checks passed!`; `ruff format --check` → `582 files already formatted`; `pytest -k storage -q` → `29 passed, 2553 deselected`; `test_config_service` + `test_storage_runtime` → `13 passed` | pass | subagent run, 2026-08-05 |
| V-06 | I-04 | New regression test: grow a non-candidate file between plan and execute | no 409 | grew `logs/arden.log` by 5 MiB and added a 2 MiB `sessions.db-wal` → 2 actions completed | pass | `apps/server/tests/test_storage_runtime.py` |
| V-07 | I-04 | New regression test: change a planned candidate | 409 | planned backup regrown 1→3 MiB → 409 | pass | `apps/server/tests/test_storage_runtime.py` |
| V-08 | I-07..I-11 | Desktop gate after the reasoning work | clean | `tsc --noEmit` clean; `eslint` clean; `messageVisibility` + `sourceInspector` + `streamEvents` → `102 pass, 0 fail` | pass | subagent run, 2026-08-05 |
| V-09 | I-15 | Typecheck + lint after rehoming the settings row | clean | both clean, no output | pass | 2026-08-05 |
| V-10 | I-06..I-10 | Archival UI: switch persistence read from source | no mirror state to clobber | `StorageTab.tsx:260,273` — `checked={serverConfig.…}` with `onChange` → `persist({…})`; no resync effect remains | pass | 2026-08-05 |
| V-11 | all | **Full desktop gate, run by me** | green | `tsc --noEmit` clean; `eslint` clean; `bun test tests/` → `1062 pass, 0 fail, 5627 expect()` across 176 files; `bun run build` → `✓ built in 1.06s` | pass | 2026-08-05 |
| V-12 | all | **Full server gate, run by me** | green | `ruff check` → `All checks passed!`; `ruff format --check` → `582 files already formatted`; `pytest apps/server/tests -q` → `2582 passed in 142.28s` | pass | 2026-08-05 |
| V-13 | I-01 | **Live-app regression, user-reported**: working strip rendered as a solid white band with a broken-frame glyph | n/a | Removing `width/height: 100%` from the canvas made layout fall back to its width/height ATTRIBUTES (canvas = replaced element). `WorkingStrip.measure()` assigns `canvas.width = rect × dpr` per frame → measured size feeds backing store feeds measured size → doubles at 60fps until compositing fails. Replicated: buggy CSS `rect.width` per frame `[1516, 3032, …, 776192]`; fixed `[758 × 10]` | **fail → fixed** | `scratchpad/loop.mjs`, 2026-08-05 |
| V-14 | I-01 | Post-fix re-verification | geometry + rim intact, dark theme correct | `axisDelta: 0`, `spaceAbove = spaceBelow = 17.99`, `sendBox 30x30`; on-arc rim pixels 46 vs 33 baseline; dark-theme shot clean; full suite `1062 pass, 0 fail` | pass | 2026-08-05 |

### Post-review round (2026-08-05, after user live testing)

| ID | Related work | Check | Observed | Result |
| --- | --- | --- | --- | --- |
| V-15 | I-22 | User report: send button off its corner | Toolbar axis + send resize reverted; guard test inverted to forbid `--content-gutter` on the toolbar | fixed |
| V-16 | I-23 | User report: reasoning bold+italic, one short line | Bold = OpenAI `**Title**` summary markers rendered raw; italic = hardcoded class. Parser splits markers into step labels; italic deleted. 7 parser tests pass | fixed |
| V-17 | I-23 | Rendered pixels of the step timeline, both themes | Label 13px/500 ink-soft, body 13px/400 muted (computed-style probe); rail, shimmer, hierarchy correct in light and dark | pass |
| V-18 | I-22, I-23 | Full desktop suite, twice | `1069 pass, 0 fail` × 2 (one unreproduced flake in an earlier run) | pass |
| V-19 | I-22 | User report: attach icon off the text axis after the V-15 revert | The revert over-corrected — symmetric 8px broke the leading glyph off the axis just as symmetric gutter had floated the send button. Toolbar padding is now asymmetric: `padding-left` puts the attach glyph ON the axis, `padding-right: 8px` keeps send at its corner. Measured: message = draft = attach glyph = 288; send 28×28 at 8px gap. Suite `1069 pass, 0 fail` | fixed |

Timing note: with `gpt-5.6-sol` (OpenAI Responses, `summary: "auto"`), reasoning arrives
as brief summaries near the end of thinking — the provider does not stream them earlier.
The timeline now opens itself during the stream so steps land as they arrive, which is
the part presentation can fix.

### Reasoning multi-part round (2026-08-05)

| ID | Related work | Check | Observed | Result |
| --- | --- | --- | --- | --- |
| V-20 | I-24..I-26 | Feed synthetic Responses events through the real collector and parser | BEFORE: both paths returned `…opens in spring.**Planning targeted…`, part 2's title glued mid-line. AFTER: `…spring.\n\n**Planning…` from both, byte-identical | **repro → fixed** |
| V-21 | I-24..I-28 | Real server output through the real client parser | 3 clean steps; `<!-- -->` body correctly reduced to empty; rendered timeline correct in both themes | pass |
| V-22 | all | Full gates | server `ruff` clean, `583 files already formatted`, `pytest -q` → `2588 passed`; desktop typecheck + lint clean, `bun test tests/` → `1072 pass, 0 fail` | pass |

Cross-checked against three references, as asked. **codex** (`chatwidget/streaming.rs:290-296`,
`history_cell/messages.rs:594`) segments purely on `reasoning_summary_part.added` and joins
parts with `\n\n` — the design adopted here — and additionally deletes `<!-- -->`
placeholder parts (`messages.rs:577-590`), which is where I-28 came from. **opencode**
goes further, keying deltas by `summary_index` into `${item_id}:${summary_index}` and
emitting a separate block per part (`protocols/openai-responses.ts:623-635`); that is the
more robust model but needs new event plumbing for the same visible result, so it is
recorded as a future option rather than adopted. **hermes-agent** does not segment at all
(`codex_runtime.py:1101`) and patches the symptom cosmetically in the TUI by inserting
`\n\n` before bold runs (`ui-tui/src/lib/text.ts:118`) — the failure mode this repo had.

## Failures and gaps

- **V-13 is the sharper lesson than V-02.** The one-shot harness sized the canvas once
  and could not exhibit the measure→assign feedback loop that the component runs per
  frame. Geometry harnesses must replicate the component's *sizing behaviour over time*,
  not just its DOM at rest — a canvas (any replaced element) whose CSS size is removed
  silently switches to attribute-driven layout.
- **V-02 is the other substantive finding.** The first fix for issue 1 was to move the rim onto
  an `::after` overlay so it paints above the canvas. That is correct in paint order and
  wrong in practice: an element's own inset shadow is *not* clipped by its own
  `overflow`, but an overlay pinned to the same edge *is*, so the rim lost two thirds of
  its opacity around the corner. Caught only by counting accent pixels on the arc —
  both the reasoning and the low-resolution screenshot looked fine. Reverted in favour
  of insetting the dither field.
- **`ConfirmDeleteButton` is a behaviour change** on archived-chat rows: permanent
  deletion is now a 3s cancellable countdown rather than click-twice. It is the app's
  canonical destructive control, so this is conformance rather than a new pattern — but
  it is a change to how deletion feels, not just how it looks.
- **`plan_id` guarantees narrowed deliberately** (D4). Two cases no longer 409 that
  previously did: a same-path same-size backup whose *content* changed, and a session
  edited at constant `logical_bytes`. Both remain memory-safe because execute re-inspects
  and re-derives protection under lock before mutating, but the guarantee is now "an
  expired backup at that path" rather than "that exact file". Closing the first case
  costs one `modified_at` field on `StoragePlanAction` and does not reintroduce
  whole-tree coupling.
- **`summary_index` is still ignored.** Boundaries come from `reasoning_summary_part.added`
  alone, matching codex. If a provider ever delivers parts out of order or interleaved,
  opencode's index-keyed model would be required; today's ordering makes it unnecessary.
- **No live-app verification.** The CSS harness proves box math and paint order; the test
  suite proves contracts and request shapes. Neither proves the reasoning toggle actually
  reveals blocks in a running session, nor that the archival switches persist against the
  real server. The desktop API needs a bearer key that was deliberately not handled, and
  the Browser pane reports a 0×0 viewport for this repo. Two manual checks remain:
  1. Settings → Appearance → Agent activity → **Show reasoning** on, then run a turn with
     a reasoning effort set — collapsible thinking blocks should appear, and activity
     grouping should look unchanged.
  2. Settings → **Storage** — toggle each retention switch, close and reopen Settings,
     confirm the value stuck; then **Preview cleanup** and **Clean up** without a 409.

## Outcome

All four issues implemented; both gates green (V-11, V-12). Not marked complete: the two
manual checks above are the only proof that the reasoning and archival behaviour work
against the live app, and neither has been run.
