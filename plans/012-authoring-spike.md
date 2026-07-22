# Plan 012: Design spike — note authoring verbs (create, rename, delete) for the file-canonical vault

> **Executor instructions**: This is a SPIKE — the deliverable is a written
> design document plus a thin proof-of-concept, NOT a shipped feature. Follow
> the steps; on any STOP condition, stop and report. Update this plan's row
> in `plans/README.md` when done.
>
> **Drift check (run first)**: compare the "Current state" excerpts against
> the live code. Written against a dirty working tree at commit `57ec2d10`
> (branch `codex/memory-ledger-v2`). On mismatch, STOP.

## Status

- **Priority**: P3
- **Effort**: L (spike itself: M)
- **Risk**: MED — semantics touch the memory ledger's core invariants
- **Depends on**: none (design work); implementation would follow 011
- **Category**: direction
- **Planned at**: commit `57ec2d10`, 2026-07-13

## Why this matters

The memory view aspires to "Obsidian, but for memory" — yet the vault has no
authoring verbs. Users can read and edit-in-place, but cannot start a new
note, rename one, move one between directories, or delete one. Whether and
how to add these is constrained by the system's core design: memory is
file-canonical (markdown pages ARE the source of truth), pages carry
revisions and an append-only event ledger, and the rail's structure comes
from managed `<!-- ntrp:index -->` blocks in `index.md`/README files. This
spike produces the decision document so the build is a known quantity.

## Current state (the constraints, with evidence)

- **File-canonical**: `apps/server/ntrp/server/routers/memory.py:210-222` —
  the rebuild endpoint is a no-op: "the markdown pages ARE the source of
  truth, there is no projection to re-derive."
- **Client API surface is read/edit-only**:
  `apps/desktop/src/api/memoryArtifacts.ts` exports list/read/rebuild/
  preview/apply/retry/history/links (grep `^export` there) — no
  create/rename/delete.
- **Edits flow through a review pipeline**: `previewPageEdit` → server diff +
  memory-operation analysis → `applyPageEdit` with `baseRevision` conflict
  detection (409 `page_revision_conflict` handled in
  `ArtifactMemoryView.tsx:113-120`). New verbs must state how they interact
  with this pipeline (does create need review? does delete append a ledger
  event like RETRACT does — see the Forget copy in `MemoryInspector.tsx:263`:
  "This appends a RETRACT event. It does not delete history.").
- **Rail structure comes from managed index blocks**:
  `apps/desktop/src/features/memory/lib/notebookIndex.ts` parses
  `<!-- ntrp:index -->` blocks out of `index.md`/READMEs
  (`parseManagedIndex`, `buildNotebookRailModel`); a page absent from any
  index lands in the flat "Files" bucket. Creating a note therefore has TWO
  halves: the file, and (optionally) its index entry.
- **Links must not dangle**: rename/move breaks inbound wikilinks; the server
  has a link index (`getPageLinks`, stale flag) — a rename design must say
  whether the server rewrites `[[links]]` in referring pages (Obsidian does)
  or marks them unresolved.
- Server-side page store: `apps/server/ntrp/memory/pages.py`,
  `artifacts.py`, `ledger.py`, `journal.py` (recently churned on this
  branch) — read their public surfaces before proposing endpoints.

## Commands you will need

| Purpose | Command | Expected |
|---------|---------|----------|
| Server tests | `cd apps/server && uv run pytest tests/ -k memory` | pass |
| Desktop tests | `cd apps/desktop && bun test tests/` | pass |

## Scope

**In scope (deliverables)**:
- `docs/internal/memory-authoring-design.md` (create) — the decision doc.
- A proof-of-concept `create` endpoint + minimal UI behind nothing fancy
  (see Step 3) on a THROWAWAY branch, to validate the design's riskiest
  assumption. POC code is not merged by this plan.

**Out of scope**:
- Shipping any verb to main.
- Rename/move/delete implementation (design only).
- Changing ledger event schemas.

## Steps

### Step 1: Map the server's actual invariants

Read `apps/server/ntrp/memory/pages.py`, `artifacts.py`, `ledger.py`,
`file_store.py` and list, in the design doc: how a page comes into existence
today (who writes files — consolidation? curator? synth?), what the ledger
records per page mutation, what path/slug rules exist, and what happens to
records/links when a file disappears from disk today (is there a
reconciliation sweep?).

**Verify**: the doc's "Invariants" section cites `file:line` for each claim.

### Step 2: Write the design doc

`docs/internal/memory-authoring-design.md` covering, per verb
(create / rename+move / delete):

- **Semantics**: e.g. create = new file + optional index entry + ledger
  event? delete = file removal or Obsidian-style trash + ledger event
  (mirroring RETRACT's "does not delete history" stance)? rename = server
  rewrites inbound wikilinks vs. leaves unresolved?
- **API shape**: endpoint, request/response, conflict story (revision
  parameter? path-exists 409?).
- **Index interaction**: does create add to the parent README's managed
  block, or land in Files? Who edits managed blocks — is that a page edit
  through the review pipeline?
- **UI affordances**: rail "+" button, context menu (rename/delete), title
  click-to-rename — sketch which land in which follow-up plan.
- **What we deliberately don't do** (e.g. move-with-merge, bulk ops).
- A **recommendation** with a build-order (likely: create first — it's
  additive and dodges the link-rewrite problem entirely).

### Step 3: POC the riskiest slice

On a throwaway branch: minimal `POST /memory/artifacts` (path + title +
empty body) in `routers/memory.py`, a `createMemoryArtifact` client
function, and a bare "New note" button in the rail footer
(`NotebookRail.tsx:233-255` toolbar) that creates then `navigateTo`s the
page. Purpose: prove the file-store + summaries + rail model pick up a new
file without a restart, and surface whatever invariant Step 1 missed.

**Verify**: `uv run pytest tests/ -k memory` still passes with the new
endpoint's test; manual: create → note opens in the workspace → appears in
Files bucket after refresh. Record findings (especially surprises) in the
design doc's "POC findings" section; then leave the branch unmerged.

### Step 4: Decision checkpoint

Present the doc to the maintainer. Do not proceed to implementation plans —
those get written per-verb after sign-off.

## Done criteria

- [ ] `docs/internal/memory-authoring-design.md` exists with Invariants (cited), per-verb designs, recommendation, POC findings
- [ ] POC branch demonstrates create end-to-end; not merged
- [ ] Both test suites still pass on the POC branch
- [ ] `plans/README.md` updated with the doc's location and the recommendation one-liner

## STOP conditions

- Excerpts don't match (drift).
- Step 1 reveals that page files are written exclusively by an automated
  process that would fight user-created files (e.g. a consolidation pass
  that prunes unknown paths) — that's the headline finding; write it up and
  stop before the POC.
- The POC requires touching ledger event schemas — out of scope; document
  the need instead.

## Maintenance notes

- The design doc should live next to the memory research docs
  (`docs/memory-research.html` exists; `docs/internal/` for working docs).
- When implementation plans get written, they must revisit plan 006/008
  surfaces (rail context menus, switcher entries for new notes).
