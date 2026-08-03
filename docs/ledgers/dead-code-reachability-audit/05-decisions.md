# Decisions

## Current decisions

| ID | Decision | Rationale | Evidence | Adopted at |
| --- | --- | --- | --- | --- |
| D-01 | Seed reachability from all production roots, not the UI alone. | The backend has CLI, HTTP, scheduler, MCP, and background surfaces; Electron has path-loaded/manual entries. | F-01, F-04 | 2026-08-03 |
| D-02 | Remove only candidates supported by exact no-reference evidence plus contract review. | The amended request authorizes cleanup but prioritizes certainty. | `01-intent.md`, F-06, F-09 | 2026-08-03 |
| D-03 | Retain server routes without a desktop caller. | HTTP routes are external entry points; absence from one client is not dead-code proof. | F-07, F-08 | 2026-08-03 |
| D-04 | Make no database change and no empty database commit. | Every table has live SQL and historical schema compatibility is material. | F-08 | 2026-08-03 |
| D-05 | Split implementation commits by routes, backend, frontend, CSS, dependencies, and ledger. | Matches the user's requested review boundaries. | `01-intent.md` | 2026-08-03 |

## Proposed decisions

| ID | Proposal | Trade-off | Needed input |
| --- | --- | --- | --- |
| P-01 | Retire the remaining 41 test-only declarations in a separate compatibility-focused task. | Could simplify APIs, but repository-only evidence does not prove external safety. | Explicit API compatibility decision. |

## Superseded decisions

| ID | Previous decision | Superseded by | Reason | Changed at |
| --- | --- | --- | --- | --- |
| S-01 | Keep product source read-only. | D-02 | User explicitly requested removal and a PR. | 2026-08-03 |
