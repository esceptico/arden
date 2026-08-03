# Research

## Research inbox

Temporary observations awaiting classification and consolidation.

- Empty; all observations are consolidated below or in [candidate-audit.md](candidate-audit.md).

## Surface research passes

### Pass 1 — production roots and file graph

- **Question**: Which production code files are reachable from real executable roots?
- **Scope**: `apps/server/arden/**/*.py`; desktop renderer, Electron, and scripts.
- **Sources inspected**: `apps/server/pyproject.toml:48-49`; `apps/server/arden/cli.py:107-160`; `apps/server/arden/server/app.py:38-60`; `apps/desktop/package.json:6-20`; `apps/desktop/src/index.html:14`; `apps/desktop/src/app/main.tsx:1-33`; all static imports/re-exports/requires/dynamic literal imports.
- **Observations**: Python BFS reached 345/345 modules. Desktop BFS reached 367/369 files; both apparent orphans were verified non-import entry points and raise effective coverage to 369/369.
- **Negative evidence**: No file-level orphan survived verification.
- **Follow-ups**: Inspect declarations inside reachable files.

### Pass 2 — declarations and unreachable statements

- **Question**: Which top-level functions/classes/variables/constants, exports, and methods have no production reference?
- **Scope**: Python AST declarations; TypeScript/JavaScript exports and top-level script declarations; methods with zero attribute/name references; compiler/linter unreachable checks.
- **Sources inspected**: all production source and test files; repo-wide exact-name searches; definition context for every candidate; `ruff F401,F841`; TypeScript `noUnusedLocals`, `noUnusedParameters`, and `allowUnreachableCode=false`.
- **Observations**: 93 production-unreferenced declarations: 16 backend top-level, 35 backend methods, and 42 desktop exports. Of these, 48 have no test reference and 45 are test-only. See [candidate-audit.md](candidate-audit.md).
- **Negative evidence**: No unreachable statements, unused Python imports/locals, or TypeScript unused locals/parameters were reported.
- **Follow-ups**: Removal requires a separate implementation decision and targeted test updates.

### Pass 3 — dynamic and cross-process verification

- **Question**: Which static candidates are explained by runtime registration, protocols, packaging, or tree-shaking?
- **Sources inspected**: Electron `BrowserWindow` configuration; Click/FastAPI decorators; HTTP/logging/MCP protocols; workflow script surface; enum contracts; production Vite bundle.
- **Observations**: 17 apparent candidates were retained as false positives (2 files, 10 framework callback methods, 1 workflow API method, 4 serialized enum members). The bundle independently omits five unused setup/account API paths.
- **Negative evidence**: Server HTTP routes remain external entry points even where the desktop has no live caller; they are not promoted to dead code without an API-retirement decision.
- **Follow-ups**: Audit CSS/assets/dependencies separately if desired.

### Pass 4 — routes, CSS, database, and dependencies

- **Routes**: Five desktop wrappers were absent from the production bundle and unused by source. Their server endpoints remain because FastAPI routes are external contracts; `GET /admin/facts/{fact_id}` is also publicly documented.
- **CSS**: Every stylesheet is imported. Exact selector/source comparison found ten removable selector groups with neither authored-source nor generated-markup paths. Framework-generated Markdown/highlight classes and canonical/manual design-system classes were retained.
- **Database**: Every declared durable table has live read/write SQL. Migration tables and low-reference checkpoint/idempotency tables were retained; no database deletion was proven safe.
- **Dependencies**: Every Python runtime dependency maps to a production import. Desktop dependencies are referenced by source/config except `electron-builder-squirrel-windows`; Windows packaging targets NSIS.
- **Implementation**: Removed all original zero-reference declarations, four obsolete desktop route wrappers, their orphaned contract types/tests, the CSS selectors, and the unused package.

## Consolidated findings

| ID | Type | Claim | Evidence | Implication | Confidence | Last checked |
| --- | --- | --- | --- | --- | --- | --- |
| F-01 | fact | Every production Python module and desktop code file is reachable after non-import entry edges are modeled. | Custom import graph: Python 345/345; desktop 367/369 static plus `main.cjs:449,536` preload edges and manual `remote-sim.mjs:8` entry. | Cleanup is symbol-level, not whole-file deletion. | high | `2c6f282`; 2026-08-03 |
| F-02 | fact | 93 declarations have no production reference: 48 declaration-only and 45 test-only. | Repo-wide AST/name/reference audit; per-symbol evidence in [candidate-audit.md](candidate-audit.md). | There is a substantial removal/refactor batch despite a fully connected file graph. | high | `2c6f282`; 2026-08-03 |
| F-03 | fact | Existing checks do not report unused locals/imports or unreachable statements. | `ruff check arden --select F401,F841`; `bun run typecheck`; `bunx tsc --noEmit --allowUnreachableCode false`; AST terminator scan. | Candidates are mostly exported/public wrappers and methods, which normal local lint misses. | high | 2026-08-03 |
| F-04 | fact | Static analysis alone produced 17 verified false positives. | Dynamic contracts listed in [candidate-audit.md](candidate-audit.md). | Do not bulk-delete by reference count. | high | 2026-08-03 |
| F-05 | inference | The safest first cleanup batch is the 48 zero-reference declarations, followed by test-only wrappers with their tests. | F-02 plus absence of dynamic contracts after individual inspection. | Cleanup should be staged by risk, not one mass deletion. | medium | 2026-08-03 |
| F-06 | fact | All 48 original zero-reference declarations were removable without breaking static or behavioral verification. | Commits `09d5e39b`, `b1c44533`; full suites and build pass. | The confirmed declaration-level dead code is removed. | high | 2026-08-03 |
| F-07 | fact | Four obsolete setup route wrappers and their contract-only declarations/tests had no production bundle path. | Commit `0b178e07`; current UI uses `/services` and per-service Google routes. | Client route surface now matches production usage. | high | 2026-08-03 |
| F-08 | fact | No database table or server HTTP route met the deletion threshold. | Every table has read/write SQL; routes are external entry points and some are documented. | Retain them; absence from the desktop is insufficient evidence. | high | 2026-08-03 |
| F-09 | fact | One declared package and ten CSS selector groups were proven unreferenced. | Package/config scan; CSS selector scan plus generated-class verification; commits `560c7e9b`, `b94f576a`. | Safe non-code cleanup completed. | high | 2026-08-03 |

## Conflicts and uncertainties

- External Python consumers are not discoverable from this repository. Candidates are proven unused by Arden production code, not necessarily by third-party imports.
- Unknown external consumers remain outside repository-static proof.
