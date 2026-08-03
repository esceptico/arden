<!-- development-ledger:v1 -->

# Dead code reachability audit

## Status snapshot

| Field | Value |
| --- | --- |
| Ledger status | complete |
| Active phase | delivery |
| Created | 2026-08-03T16:38:03+04:00 |
| Last updated | 2026-08-03T17:20:35+04:00 |
| Last consolidated | 2026-08-03T17:20:35+04:00 |
| Codebase branch | codex/dead-code-cleanup |
| Codebase revision | b94f576a1143ab5cc53f1bda6e7a3f196856d8ef |
| Sources checked through | code: b94f576a1143ab5cc53f1bda6e7a3f196856d8ef; web: not checked |

## Executive synthesis

No whole code file was orphaned. The cleanup removes all 48 original zero-reference declarations, four obsolete test-only desktop route wrappers, eight declarations orphaned by that cleanup, ten CSS selector groups, and one unused package. Forty-one test-only seams, external HTTP routes, dynamic contracts, and all database tables remain because deletion safety was not proven. See [candidate-audit.md](candidate-audit.md).

## Current decisions

Use all production roots; remove only verified declaration/selector/dependency orphans; retain externally callable routes and historical schema. See [05-decisions.md](05-decisions.md).

## Open questions

See [03-questions.md](03-questions.md).

## Next action

Review the cleanup PR; retire the remaining test-only compatibility seams only in a separate explicit task.

## Lifecycle files

| File | Purpose |
| --- | --- |
| [01-intent.md](01-intent.md) | Verbatim user intent, amendments, constraints |
| [02-research.md](02-research.md) | Surface research and evidence-backed findings |
| [03-questions.md](03-questions.md) | Open and resolved questions |
| [04-synthesis.md](04-synthesis.md) | Current consolidated understanding |
| [05-decisions.md](05-decisions.md) | Adopted and superseded decisions |
| [06-implementation.md](06-implementation.md) | Implementation plan and completion record |
| [07-verification.md](07-verification.md) | Observed proof, failures, and gaps |
