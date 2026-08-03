# Lessons

## 2026-08-03 — Never claim UI done without reading rendered pixels
Declared a "design pass" complete on the trigger peek with tests/typecheck as
the only evidence; the user's screenshot showed a sheared corner and an
unstyled overflow I would have caught in one screenshot. When the visual
verification path is blocked (headless auth), SAY the verification is blocked
and solve it (fixture server, dev-harness state, ask for a dev key) — never
silently substitute "gates are green" for "I looked at it." Rule: any turn
that claims visual work is finished must either include a screenshot I read,
or explicitly state that I could not render it and why.

## 2026-08-03 — Compose from the design system; never coin new UI
Not only buttons: every component, material, motion, and layout choice must
come from the project's existing vocabulary. Before any UI work, inventory
what the app already uses for that job (primitive components, tokens, the
sibling surface's pattern) and compose from those. Hand-rolling a raw element
+ bespoke CSS when a primitive exists is a defect even if it "works".
Canonical miss: the full-width "Add trigger" slab vs the app's circle Plus
IconButton idiom.

## 2026-08-03 — Rename codemods must treat SQL as a separate namespace
A tool-name string codemod (`search_text` → `file_search_text`) also rewrote a
SQLite COLUMN with the same name in context/store.py. Every test stayed green —
fresh test DBs are self-consistent under any spelling — but the live DB kept the
old column and boot half-migrated it (stray column added, good triggers dropped,
bad triggers created), killing every message write.
Rules: (1) before applying a rename map to string literals, grep the map's words
inside CREATE TABLE/INSERT/UPDATE/PRAGMA strings and exclude those files or
words; (2) schema-touching renames are only proven by booting against a COPY of
a real ~/.arden database, never by the test suite alone; (3) when a bad build
may have mutated user schemas, ship the migration that detects and heals that
exact damage (see test_migration_heals_stray_file_search_text_column).
