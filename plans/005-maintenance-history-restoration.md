# Plan 005: Complete the Maintenance history recovery surface

## Outcome

For a Wiki Maintenance edit, the activity UI shows the recorded reason and
offers a safe Restore action when that exact edit is still the page's latest
version.

This is a narrow completion of the existing UI, not a redesign.

## Current state

- History API responses already include actor, origin, reason, commit ID, and
  before/after resource versions.
- The Desktop adapter drops `reason` and collapses the exact origin.
- `MemoryDiffOverlay` shows timestamp, actor, diff, and Close only.
- The revision core can restore content from a reachable commit, but no wiki
  service/router/Desktop path exposes safe restoration of a Maintenance edit.

## Backend

Add a `WikiService` operation for restoring one exact Maintenance change:

Input:

- page ID;
- Maintenance commit ID;
- current expected page version;
- current expected wiki head.

Rules:

1. Require exact current head and page version.
2. Load the reachable commit and require
   `origin == WIKI_MAINTENANCE_ORIGIN`.
3. Require exactly one update for the selected page, with both before and after
   versions.
4. Require the current page version to equal that commit's `after.version_id`.
   If any later page edit exists, return a conflict; do not overwrite it.
5. Parse the historical `before` page and restore its title, aliases, and body
   through the same validation used by `apply_maintenance_updates()`.
6. Preserve page identity, lifecycle, generated region, provenance, and all
   current wiki name/link invariants.
7. Commit as `user:desktop`, origin `desktop.restore`, with a reason referencing
   the restored Maintenance commit.
8. Project wiki/index/health through the existing post-commit path.

Do not implement a general historical checkout or three-way merge. A stale
historical edit must require manual editing.

Expose:

`POST /admin/wiki/pages/{page_id}/history/{commit_id}/restore`

with the same structured conflict/error mapping as current page updates.

## Desktop

Update:

- `apps/desktop/src/api/wiki.ts`
- `apps/desktop/src/api/memoryArtifacts.ts`
- `apps/desktop/src/features/memory/components/MemoryDiffOverlay.tsx`
- the parent callback wiring in `ArtifactMemoryView.tsx`/`MemoryInspector.tsx`

Carry the raw canonical origin and reason in `PageEditEvent`. Show the reason in
the diff header/body.

Show Restore only when:

- raw origin is `wiki.maintenance`;
- before and after versions exist;
- the event's result version equals the currently loaded page version.

Use the existing Button/dialog visual language. On success, close the overlay
and atomically refresh the page, history, links, and caches. On `409`, preserve
the current page and show that a newer edit prevents automatic restoration.

Do not alter notebook preview/summary, Memory layout, rename/archive placement,
motion primitives, or current Claude-owned styling.

## Tests

Server:

- restores the latest Maintenance edit;
- records user actor/origin/reason;
- rejects non-Maintenance commits;
- rejects stale page/head and a later page edit;
- preserves generated regions and provenance;
- rejects identity/name/link violations;
- post-commit projection failure is reported through the existing pending
  contract.

Desktop:

- history adapter preserves reason and raw origin;
- Restore appears only for eligible Maintenance edits;
- success refreshes all dependent state;
- stale conflict leaves content untouched and visible;
- diff Close behavior and existing visual contracts remain unchanged.

## Acceptance

- The ledger's actor/reason/restore history promise is complete.
- Restoration cannot clobber a newer user or producer edit.
- No broad history-rewrite capability is introduced.

