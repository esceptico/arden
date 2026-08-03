# Synthesis

> Current through code revision `b94f576a1143ab5cc53f1bda6e7a3f196856d8ef`; last consolidated: 2026-08-03T17:20:35+04:00.

## Answer in one paragraph

No whole source file was orphaned. The branch removes all 48 original zero-reference declarations, four obsolete desktop route wrappers, eight cascading declarations, ten CSS selector groups, and one unused dependency. It deliberately retains 41 test-only seams, external server routes, dynamic contracts, and the full database schema. All static checks, 3,502 tests, and the production build pass.

## Current model

The repository has two executable systems: Electron/Vite and a Python CLI/FastAPI server. File-level BFS is fully connected, but exported helpers, compatibility wrappers, test seams, motion tokens, and old service/store methods survive inside live modules. Normal lint stays green because exported declarations and public methods are intentionally outside unused-local checks.

## Scope boundaries

- **Included**: Python and desktop files/declarations; routes; CSS selectors/imports; database tables/schema references; declared dependencies; dynamic framework/protocol/manual entries; production bundle.
- **Excluded**: Unknown external consumers and destructive historical migration rewriting.

## Assumptions

- An HTTP route is an external entry point even without a live desktop caller.
- “Production-unreferenced” is stronger than a static suspicion but weaker than blanket deletion approval.

## Consequences for implementation

- Keep the remaining test-only seams and external routes until an explicit compatibility-retirement task proves them safe.
- Do not rewrite historical schema merely because a column has low static reference counts.
- Preserve generated CSS classes and canonical/manual design-system vocabulary.
