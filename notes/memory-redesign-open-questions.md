# Memory Redesign Follow-ups

This note tracks the remaining non-blocking follow-ups for the `memory_items` redesign. It no longer lists stale migration claims.

## Current decisions

- Legacy `apps/server/arden/knowledge/*` package imports and old client knowledge panes were removed.
- `apps/server/arden/memory/facts.py` was removed with the legacy fact-store path; the redesign runtime uses `MemoryDatabase` with `memory.items` for the graph primitive.
- `apps/server/arden/memory/service.py` stays as the stable `MemoryService` shell and chat connector attachment point.
- `pattern_finder` and `skill_inducer` are the surviving memory automation builtins.
- `/admin/memory/items` defaults to active items and now supports kind, status, scope, query, and validity filters.
- Desktop memory uses the single `MemoryItemsPane` with existing shared primitives and exposes Today / Graph / Skills / Search.

## Deferred

- Decide whether detail view should expose or summarize raw `usage`, `feedback`, and `artifact_ref` fields.
- Decide whether source refs need a first-class resolver instead of raw structured JSON in detail panes.
- Decide whether parent navigation should backfill list selection for parents outside the current filtered list.
- Rebuild current memory-system docs around the `memory_items` model once the redesign settles.
- Decide whether smoke tests should assert additional memory-specific readiness beyond `/health`.
