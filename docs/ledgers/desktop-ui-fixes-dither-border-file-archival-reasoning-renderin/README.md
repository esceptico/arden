<!-- development-ledger:v2 -->

# Desktop UI fixes: dither border, file archival, reasoning rendering, composer text

## Status

| Field | Value |
| --- | --- |
| State | verifying |
| Active phase | verification |
| Created | 2026-08-05T03:37:19+04:00 |
| Last updated | 2026-08-05T05:20:00+04:00 |
| Last consolidated | 2026-08-05T05:20:00+04:00 |
| Codebase branch | main |
| Codebase revision | 31dff5a421572d754bbe13ec64428837ee2634f8 |
| Sources checked through | code: 31dff5a421572d754bbe13ec64428837ee2634f8; web: not checked |

## Original task — verbatim

few issues:

* animation of dither (when agent working) overlapping over the border
* file archival is broken: from UI (was used not consolidated design choices) up to functionality (not clear functionality + some switches are not working)
* need to add an opt-in reasoning rendering
* text placement in composer feels weird


use /development-ledger and subagents for this if possible

## Amendments — verbatim

None.

## Current synthesis

Four issues, four different shapes of work. Full evidence in [research.md](research.md).

1. **Dither overlaps the border — pure CSS, 2 lines.** The composer's working "border" is
   an `inset` box-shadow (`chat.css:351-353`), which CSS paints *below* descendants. The
   dither canvas is a first child at `inset:0` with no radius (`chat.css:393-402`), so its
   top dot row lands on the 1 px accent ring. A second, smaller defect: neither the strip
   nor the canvas carries a radius, so a 23 px corner arc is clipped only by an ancestor
   `overflow:hidden`, leaving an anti-aliased fringe. No JS change needed.

2. **"File archival" is two unrelated features stacked in one tab.** `ArchiveTab.tsx` is
   3/4 a global disk-quota planner and 1/4 an archived-chat list; "archive" also means
   backup files on the same screen. Three retention switches are local React state whose
   only save button lives in the *previous* section, and their unsaved state is wiped by
   reopening Settings, any SSE reconnect, or a tab switch. `deleteCold` has no server
   field at all. Two persisted flags are effectively write-only. "Clean up" is expected to
   409 because the plan hash includes the live-growing log file and `sessions.db`. Eleven
   elements are hand-rolled where a canonical settings primitive already exists.

3. **Reasoning rendering is fully built and one line from working.** Backend request →
   stream → SSE → store → collapsible `ReasoningMessage.tsx` all exist; commit `d887d16c`
   hardcoded `hiddenInTranscript: true` for reasoning at `messageVisibility.ts:16-26`,
   making the renderer dead code. Adding the opt-in is 5 client-side edits mirroring the
   existing `fontSmoothing` pref. Zero backend work.

4. **Composer text sits ~10 px above optical center.** A 20 px line box is top-pinned by
   `items-start` inside a 44 px `min-height` that duplicates the row's own 56 px
   constraint, with asymmetric 8 px / 4 px padding compounding it. Separately, draft text
   starts 12 px left of every message body, so text visibly jumps right on send, and the
   panel carries four different internal left insets (8 / 12 / 12 / 16 / 16 px).

## Decisions

- **D1 — Archival: split + fix + conform.** Separate the disk-quota planner from the
  archived-chat list into two Settings tabs, repair every dead switch and the plan-hash
  409, and replace all 11 hand-rolled elements with the canonical settings primitives
  named in [research.md](research.md). User-adopted 2026-08-05.
- **D2 — Composer aligns to the transcript axis.** Input-row `padding-inline` goes
  16 px → `var(--content-gutter)` (28 px) so draft text sits exactly where it will render
  as a message and does not jump on send. The panel's other internal insets are left
  alone. User-adopted 2026-08-05.
- **D3 — Reasoning opt-in is a client-local pref**, not server config: `showReasoning`
  in `Prefs`, default `false`, mirroring `fontSmoothing`. Zero backend work.
- **D4 — Plan identity is the actionable candidate set**, not total tree bytes, so the
  409 cannot be triggered by the server's own log growth.

## Open questions

- None outstanding.

## Correction to the original research

Research proposed moving the busy rim to an `::after` overlay so it would paint above the
dither canvas. That was implemented, measured, and **reverted**. An element's own inset
box-shadow is not clipped by its own `overflow`, but an overlay pinned to the same edge
is — so the overlay lost two thirds of its opacity around the 23px corner (12 accent
pixels on the arc vs 33). The shipped fix insets the *canvas* instead. See V-02/V-03 in
[verification.md](verification.md).

## Next action

Both gates are green (desktop: 1062 tests + build; server: 2582 tests). Nothing is
committed. Two manual checks in the running app remain — they are the only unproven
claims, listed at the end of [verification.md](verification.md): that the reasoning
toggle reveals blocks, and that the Storage switches persist across a Settings reopen.

Once reviewed, drop the rescue backup: `git stash drop stash@{0}`.

## Details

- [Research](research.md)
- [Implementation](implementation.md)
- [Verification](verification.md)
