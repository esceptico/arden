# Plan 001: Fix nonexistent `warning`/`danger` color classes so status states actually render

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md`.
>
> **Drift check (run first)**: `git diff --stat 57ec2d10..HEAD -- apps/desktop/src/features/memory/components/MemoryInspector.tsx apps/desktop/src/features/memory/components/WikiLinkPreview.tsx`
> This plan was written against a dirty working tree on branch
> `codex/memory-ledger-v2` at commit `57ec2d10`. Compare the "Current state"
> excerpts against the live code before proceeding; on a mismatch, STOP.

## Status

- **Priority**: P1
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none
- **Category**: bug
- **Planned at**: commit `57ec2d10`, 2026-07-13

## Why this matters

The memory inspector and wikilink preview use Tailwind classes `text-warning`,
`text-danger`, and `bg-warning/10`. The theme (`apps/desktop/src/styles.css`)
defines only `--color-warn` / `--color-bad` (plus `-soft` washes) — there is no
`--color-warning` or `--color-danger` anywhere in the repo. Tailwind v4 never
generates those utilities, so "Evidence missing/changed" warnings, the
stale-link-index banner, inline error alerts, and the proposed-target /
pending-review washes render with inherited ink color and transparent
backgrounds — status is invisible to the user.

## Current state

Files (all under `apps/desktop/`):

- `src/features/memory/components/MemoryInspector.tsx` — five broken usages:
  - line 76: `<div className="mt-2 flex items-center gap-1 text-warning"><AlertCircle className="size-3" />Evidence {state}</div>`
  - line 121: `return <p role="alert" className="text-xs text-danger">{message}</p>;`
  - line 208: `{links.stale && <div className="flex items-center justify-between gap-3 text-xs text-warning">`
  - line 245: `className="rounded-[8px] bg-warning/10 p-2"` (proposed lifecycle target)
  - line 301: `className="rounded-[8px] bg-warning/10 p-2.5 text-xs text-ink"` (pending review question)
- `src/features/memory/components/WikiLinkPreview.tsx`
  - line 167: `<span role="alert" className="text-xs text-danger">{preview.error}</span>`
- `src/styles.css` — the real tokens (lines ~72-75):
  ```css
  --color-warn: #f5a623;
  --color-warn-soft: rgba(245,166,35,0.1);
  --color-bad: #e5484d;
  --color-bad-soft: rgba(229,72,77,0.08);
  ```

Convention exemplars already correct in the same feature:
`MemoryEditReview.tsx:145` and `MemoryEditor.tsx:180` use `text-bad`;
`MemoryInspector.tsx:261` uses `bg-bad-soft` for the forget confirm.

Note: `variant="danger"` on `<Button>` (MemoryInspector.tsx:256, 268) is a
Button prop variant, NOT a Tailwind color class — leave those alone.

## Commands you will need

| Purpose   | Command (run from `apps/desktop/`) | Expected on success |
|-----------|------------------------------------|---------------------|
| Typecheck | `bun run typecheck`                | exit 0              |
| Lint      | `bun run lint`                     | exit 0              |
| Tests     | `bun test tests/`                  | all pass            |

## Scope

**In scope** (the only files you should modify):
- `apps/desktop/src/features/memory/components/MemoryInspector.tsx`
- `apps/desktop/src/features/memory/components/WikiLinkPreview.tsx`

**Out of scope**:
- `src/styles.css` — do NOT add `--color-warning`/`--color-danger` aliases;
  the repo rule is one token per concept (`warn`/`bad`).
- `Button` component and its `variant="danger"` prop.
- Any other `text-warning`/`text-danger` usages outside the memory feature
  (grep first; if any exist elsewhere, report them but don't touch them).

## Steps

### Step 1: Replace the broken classes

In `MemoryInspector.tsx`: `text-warning` → `text-warn` (lines 76, 208);
`text-danger` → `text-bad` (line 121); `bg-warning/10` → `bg-warn-soft`
(lines 245, 301 — use the pre-mixed soft token rather than an opacity
modifier, matching `bg-bad-soft` at line 261).

In `WikiLinkPreview.tsx`: `text-danger` → `text-bad` (line 167).

**Verify**: `grep -rn "text-warning\|text-danger\|bg-warning" apps/desktop/src/features/memory/` → no matches.

### Step 2: Run the gate

**Verify**: from `apps/desktop/`: `bun run typecheck && bun run lint && bun test tests/` → all exit 0.

## Test plan

Existing suites (`tests/memoryInspector.test.tsx`) assert content, not
classes; no new tests needed. Optionally add one assertion in
`memoryInspector.test.tsx` that the stale-links banner element carries
`text-warn` (model after existing queries in that file) to pin the token.

## Done criteria

- [ ] `grep -rn "text-warning\|text-danger\|bg-warning" apps/desktop/src/` returns no matches in `features/memory`
- [ ] `bun run typecheck` exits 0
- [ ] `bun test tests/` exits 0
- [ ] No files outside the in-scope list are modified (`git status`)
- [ ] `plans/README.md` status row updated

## STOP conditions

- The cited lines don't match the excerpts (drift).
- You find `--color-warning`/`--color-danger` actually defined somewhere —
  the premise would be wrong; report instead of editing.

## Maintenance notes

- Reviewer: eyeball the pending-review wash in both light and dark themes —
  `--color-warn-soft` differs per theme (`styles.css:73` vs `:1780`).
- Follow-up (not this plan): a lint rule or grep-based CI check for color
  classes that don't correspond to `--color-*` tokens would prevent this
  class of bug.
