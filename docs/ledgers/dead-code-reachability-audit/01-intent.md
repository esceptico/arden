# Intent

## Original request — verbatim

let's check the project for dead code
probably the best approach to it is to check which parts are connected to the UI -> go BFS-like to the dependencies -> get orphan code / nodes -> verify each orphan

if sounds good, you can proceed [$development-ledger](/Users/escept1co/.agents/skills/development-ledger/SKILL.md)

## Amendments — verbatim

thanks!
i think you might also cover:
- routes
- css
- database
- dependencies

after that create a PR and remove candidates you're sure about. one commit per area (backend / frontend / database / css / etc)

## Interpreted objective

Audit the project for dead code across source, routes, CSS, database schema, and dependencies; remove high-confidence candidates; verify; and deliver a PR with area-scoped commits.

## Constraints

- Treat graph-unreachable code as a candidate, not proof of dead code.
- Verify candidates against dynamic registration, configuration, CLI, automation, tests, and packaging entry points.
- Remove only candidates supported by direct evidence; preserve uncertain external/dynamic contracts.
- Keep backend, frontend, database, CSS, dependencies, and documentation changes in separate commits.

## Success conditions

- Enumerate the production entry points used to seed the reachability analysis.
- Produce evidence-backed orphan candidates with individual verification and confidence.
- Distinguish confirmed dead code from uncertain or intentionally external surfaces.
- Audit routes, CSS, database schema, and declared dependencies.
- Remove high-confidence dead code and open a verified pull request.

## Out of scope

- Treating tests as production roots, except as evidence that a candidate is intentionally supported.
