# Wiki ledger gap-remediation plan

Planned against local `main` at `a98f5b49` on 2026-07-29.

This is a read-only implementation plan. It does not configure the Coast
automation and does not modify Arden source. Existing unstaged Desktop work and
`docs/architecture/local-tool-execution.md` are outside scope and must remain
untouched.

## Confirmed omissions

| Priority | Gap | Evidence | Plan |
| --- | --- | --- | --- |
| P1 | Revision-object collection exists but has no production caller or weekly schedule | Ledger 87, 470, 500; `ManagedFileRepository.collect()` is called only by tests | [002](002-weekly-revision-collection.md) |
| P1 | `stale_page` and `dangling_citation` are declared health codes but never emitted | Ledger 331-370; `wiki/health.py` and runtime health assembly | [003](003-wiki-provenance-health.md) |
| P1 | Area update rollback suppresses a failed custodian reconciliation | Ledger 2319-2332 and 3169-3174; `areas/lifecycle.py:52-61` | [004](004-area-lifecycle-reconciliation.md) |
| P2 | History shows a diff but omits the commit reason and cannot restore a Maintenance edit | Ledger 320-327; current diff overlay has only Close | [005](005-maintenance-history-restoration.md) |
| P2 | Four explicitly identified files still exceed 1,000 lines and retain separable ownership | Ledger 2293-2347; architecture document 130-165 | [006](006-core-ownership-splits.md) |

## Execution order

1. Implement 002-004 as isolated correctness/capability commits.
2. Implement 005 without redesigning the Memory UI.
3. Perform 006 one file at a time, with no behavior change.
4. Run the complete server and Desktop gates after all six.
5. Append decisions, failures, verification evidence, and commit IDs to the
   original ledger.

Plans 002-005 are independent. Plan 006 must preserve every API and regression
introduced by the earlier plans.

## Explicitly not gaps

- The current-memory migration was intentionally a one-time operator script,
  completed and deleted. Shipping a migration engine would violate the ledger.
- Generated folder pages and nested `README.md` files were explicitly removed
  in favor of `list_wiki_pages`.
- Dream is intentionally the fifth optional worker, disabled by default.
- Autonomous Areas and their `area:*` custodians are intentional current
  functionality.
- Backlink-based orphan warnings were explicitly retired.
- Rename remains durable `ask`; `always`/`never` routing was superseded.
- Page-level Synthesis retry and event-driven Wiki Maintenance wakeups remain
  documented future optimizations, not correctness requirements.
- Unlinked-mention and change-impact lookup are design ideas, not recorded
  completion gates. Do not add them in this remediation.
- Do not touch notebook preview/summary behavior or restyle the Memory UI.

## Final proof

From `apps/server`:

```bash
uv run ruff check arden tests
uv run ruff format --check arden tests
uv run pytest -q --tb=short
```

From `apps/desktop`:

```bash
bun run typecheck
bun run lint
bun test
bun run build
```

From the repository root:

```bash
git diff --check
```

Also run a controlled runtime matrix:

- create, edit, and archive a disposable automation page through the agent
  tools, including exact replay and stale-revision checks;
- execute the weekly collector against fixture repositories with reachable,
  recovery-protected, young-unreachable, and expired-unreachable objects;
- project health with stale and missing citation fixtures;
- force both primary and compensating Area-custodian failures;
- restore the latest Maintenance edit and reject restoration after a newer page
  edit;
- verify the registered tool catalog, four health workers, optional Dream, and
  autonomous Areas are unchanged except for the generic wiki write tools and
  storage-maintenance worker.
