# Verification

## Verification status

Audit and implementation verification complete.

## Required evidence

| ID | Related implementation | Command or check | Expected | Observed | Result | Evidence and time |
| --- | --- | --- | --- | --- | --- | --- |
| V-01 | Audit | `python3 /tmp/arden_dead_code_graph.py` | Enumerate file reachability | Python 345/345; desktop 367/369 static, with two verified non-import roots | pass | 2026-08-03 |
| V-02 | Audit | Repo-wide AST/name scans plus exact `rg` verification | Identify declaration-level lower bound and verify each candidate | 93 production-unreferenced; per-symbol disposition recorded | pass | 2026-08-03 |
| V-03 | Audit | `uv run ruff check arden --select F401,F841` from `apps/server` | No unused imports/locals | `All checks passed!` | pass | 2026-08-03 |
| V-04 | Audit | `bun run typecheck` and `bun run lint` from `apps/desktop` | Existing static checks pass | Both exit 0 | pass | 2026-08-03 |
| V-05 | Audit | `bunx tsc --noEmit --allowUnreachableCode false`; Python terminator AST scan | No unreachable statements | Both exit 0 with no findings | pass | 2026-08-03 |
| V-06 | Audit | `bun run build`; search built JS for unused API paths | Build passes and tree-shaken paths are absent | Build passed; five candidate paths absent | pass with existing CSS warnings | 2026-08-03 |
| V-07 | Ledger | `validate_ledger.py` | Ledger valid | `VALID: 0 errors, 0 warning(s)` | pass | 2026-08-03 |
| V-08 | Backend cleanup | `uv run ruff check arden` | Static checks pass | `All checks passed!` | pass | 2026-08-03 |
| V-09 | Backend cleanup | `uv run pytest` from `apps/server` | Full suite passes | `2464 passed in 152.42s` | pass | 2026-08-03 |
| V-10 | Frontend/routes/CSS/deps | `bun run typecheck`; `bun run lint` | Static checks pass | Both exit 0 | pass | 2026-08-03 |
| V-11 | Frontend/routes/CSS/deps | `bun test` | Full suite passes | `1038 pass, 0 fail` | pass | 2026-08-03 |
| V-12 | Frontend/routes/CSS/deps | `bun run build` | Production bundle builds | Build passed in 1.58s | pass with existing CSS warnings | 2026-08-03 |
| V-13 | Database audit | Enumerate `CREATE TABLE` names and exact production SQL references | No definition-only durable table | Every table has read/write SQL | pass | 2026-08-03 |
| V-14 | Dependency audit | Python import/distribution mapping; desktop source/config reference scan | Identify unreferenced declarations | Only `electron-builder-squirrel-windows` removable | pass | 2026-08-03 |

## Failures and diagnosis

- Initial backend Ruff command was sandbox-blocked reading uv cache; rerun with approved uv access passed.
- Vite reports existing `::highlight(...)` CSS optimizer warnings; unrelated to dead-code reachability.

## Remaining gaps

- Unknown external consumers cannot be proven from this repository.
- CSS custom-property candidates were retained where JavaScript, Tailwind generation, theme mirrors, or manual design-system contracts could explain the edge.

## Final outcome

The cleanup is verified through `b94f576a1143ab5cc53f1bda6e7a3f196856d8ef`. No runtime behavior regression was observed.
