# Memory V2-Only Design

## Goal

Make the local memory engine schema-v2-only. Remove upgrade compatibility and keep one current data model, parser, startup path, and UI contract.

## Scope

This cleanup applies only to `apps/server/arden/memory`, its runtime wiring, memory APIs, desktop memory UI, and their tests. Unrelated areas, sessions, automations, and database migrations remain unchanged.

## Current Vault Contract

- Visible knowledge lives in ordinary Markdown pages.
- Canonical records live in `raw/<page>.md` with the schema-v2 header and structured metadata.
- Page edits live in `raw/events/YYYY-MM-DD.md`.
- Journal state, projections, indexes, and maintenance data live under `.arden/`.
- An empty vault initializes directly in this format.

No schema-v1 line, inline timeline sentinel, legacy directory, legacy scope alias, legacy label shape, or SQLite memory database is accepted.

## Startup

Startup performs only:

1. journal recovery;
2. empty-vault v2 initialization;
3. schema-v2 validation;
4. current store and projection startup.

A non-empty unsupported vault fails with one concise error. Startup does not migrate, back up, reinterpret, or repair old formats.

## Removals

- Delete the v1-to-v2 migrator and migration report/backup paths.
- Move current-vault validation out of the migration module.
- Delete legacy ledger-line parsing; retain schema-v2 `day` and `unknown` time precision.
- Delete inline visible-page timeline parsing.
- Delete SQLite-to-file memory import.
- Delete `entities/` and `projects/` folding into `topics/`.
- Delete `project` scope compatibility and flat-label fallbacks.
- Refactor memory callers to plural `sources`, then delete the singular `source_ref` compatibility view.
- Delete legacy changelog normalization and old citation dialect handling.
- Delete corresponding fixtures, tests, comments, and documentation promises.

## Preserved Behavior

- Append-only record lifecycle and evidence.
- Stable scope and identity.
- Journaled canonical writes and recovery.
- Page-edit events, diffs, conflict handling, and external-edit ingestion.
- Daily timelines, synthesis, link indexes, health, search, and notebook UI.
- Current v2 vault bytes remain unchanged by startup.

## Testing

- Add contract tests proving empty-vault initialization and v2-only startup.
- Add rejection tests for non-v2 record sidecars and inline timelines.
- Keep journal, records, page-event, projection, router, and desktop suites green.
- Remove tests whose only purpose is legacy compatibility.
- Validate a copied snapshot of the user's current vault without modifying it.

## Non-Goals

- No compatibility switches.
- No automatic data repair.
- No unrelated repo-wide legacy cleanup.
- No new storage abstraction or schema-v3 redesign.
