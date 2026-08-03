# Implementation

> This file is an implementation plan and completion record. A checked item is not verification evidence.

## Intended outcome

Remove only verified dead code and deliver reviewable area commits without changing runtime behavior.

## Scope delta

- Product changes cover route wrappers, backend declarations, frontend exports, CSS selectors, and one dependency.
- No server route or database schema change was made.

## Checklist

- [x] **I-01 — Remove zero-reference desktop exports**
  - Outcome: Delete unused actions/API helpers/components/types/tokens without changing runtime behavior.
  - Files or subsystem: Candidate appendix, Desktop `Z` rows.
  - Constraints and dependencies: Preserve dynamic false positives; update README claims where necessary.
  - Required verification: typecheck, lint, build, desktop tests.
- [x] **I-02 — Remove zero-reference backend declarations**
  - Outcome: Delete unused constants/helpers/wrappers/methods.
  - Files or subsystem: Candidate appendix, backend `Z` rows.
  - Constraints and dependencies: Keep protocol callbacks, workflow surface, and enum contracts.
  - Required verification: Ruff and targeted/full backend tests from `apps/server`.
- [x] **I-03 — Remove obsolete desktop route wrappers**
  - Outcome: Remove four test-only setup wrappers, one zero-reference account wrapper, their orphaned types, and obsolete tests.
  - Files or subsystem: `src/api/settings.ts`, `tests/serverContracts.test.ts`.
  - Constraints and dependencies: Preserve server routes as external contracts.
  - Required verification: desktop static checks, tests, and build.
- [x] **I-04 — Remove verified CSS and dependency candidates**
  - Outcome: Remove ten selector groups and `electron-builder-squirrel-windows`.
  - Constraints and dependencies: Preserve generated classes and canonical/manual CSS contracts.
  - Required verification: lint, tests, build, lockfile consistency.
- [x] **I-05 — Audit database schema**
  - Outcome: No change; all durable tables have live SQL.
  - Required verification: full backend suite.

## Execution order and dependencies

1. Routes: `0b178e07`.
2. Backend: `09d5e39b`.
3. Frontend: `b1c44533`.
4. CSS: `560c7e9b`.
5. Dependencies: `b94f576a`.

## Implementation notes

- Removed 60 declarations/types/functions, ten CSS selector groups, and one dependency.
- Retained 41 test-only declarations, server routes, dynamic callbacks, serialized enums, and database schema.
