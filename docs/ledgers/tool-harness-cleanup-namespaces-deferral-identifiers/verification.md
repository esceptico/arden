# Verification

## Status

Static and database-level verification passed at `e5b33cc7`. Runtime behaviour
of the running application is **not** verified — the server has not been
restarted (G1).

## Evidence

| ID | Related work | Check | Expected | Observed | Result | Evidence and time |
| --- | --- | --- | --- | --- | --- | --- |
| V-01 | I-01, I-02, I-04, I-07, I-08 | Full server suite at HEAD | all pass | `2465 passed in 202.60s` | pass | `uv run pytest tests/ -q` @ `e5b33cc7`, 2026-08-03 |
| V-02 | I-02, I-03, I-04 | Live registry introspection | 96 tools; session tools all deferred; always-on small | `TOTAL 96 ALWAYS_ON 29 DEFERRED 67`; `session tools deferred: True` | pass | `ToolExecutor()` + `is_deferred_tool`, 2026-08-03 |
| V-03 | I-05, I-06 | Native prompt swap integrity | every anchor present; no classic loader text in native TOOLS block | assertions held; `tool_search(query` present; retired `background(task)` absent | pass | `_base_system_prompt(native_deferred_tools=True)` probe, 2026-08-03 |
| V-04 | I-10 | Heal migration against the **real** damaged schema | stray column dropped, triggers fixed, writes and search work | `columns after heal: [... search_text]`; `ai trigger still references file_search_text: False`; `fts integrity-check: OK`; `write after heal: OK`; `new row searchable: True` | pass | repro built from `~/.arden/sessions.db` schema + 300 real rows, then `SessionStore.init_schema()`, 2026-08-03 |
| V-05 | F7 | Intersect all declared SQL columns with old and new tool names | only the known `search_text` | `OLD: ['search_text']`, `NEW: none` | pass | grep of `CREATE TABLE` column declarations under `apps/server/arden`, 2026-08-03 |
| V-06 | F7 | Renamed tool strings outside registry/presentation | none | no matches | pass | `grep` excluding `integrations/*` registries and `tool_presentation.py`, 2026-08-03 |
| V-07 | F8 | Scope keys unchanged across the arc | key set identical | `read_only, all, area_observe, area_act, area_reply, area_action, fact_maintenance, fact_retention, daily_notes, wiki_maintenance, wiki_producer, fact_capture` | pass | `apps/server/arden/tools/scopes.py` + `git diff 08b37ac8^..HEAD`, 2026-08-03 |
| V-08 | F9 | Confirm mid-arc third-party merge and re-run gates after it | gates green at HEAD, not inherited | `03a23537` (PR #190) sits mid-arc; V-01 and V-09 re-run at `e5b33cc7` | pass | `git log --oneline --graph`, 2026-08-03 |
| V-09 | I-01, I-08, I-10 | Desktop gates at HEAD | tests, typecheck, lint clean | `1038 tests … 0 fail`; `tsc --noEmit` clean; `eslint` clean | pass | `bun test tests/`, `bun run typecheck`, `bun run lint` @ `e5b33cc7`, 2026-08-03 |
| V-10 | I-10 | Lint and format after the fix | clean | `All checks passed!`; `540 files already formatted` | pass | `ruff check` / `ruff format --check`, 2026-08-03 |

## Failures and gaps

- **G1 (open, requires user action)** — no runtime verification. Unproven:
  (a) an agent asked to do wiki/file work never sees `session_create`;
  (b) the model loads the `session` group on demand when the user genuinely
  asks for chat management; (c) the desktop renders labels and icons correctly
  for renamed tools in a live transcript. All three need a server restart.
- **G2 (open, low risk)** — the Daily Notes v2 prompt upgrade (I-09) is
  implemented but never exercised against a database seeded with the old
  prompt; it will run on the next boot of an affected install.
- **G3 (accepted)** — user-authored automation prompts that name old tools are
  intentionally not migrated. A stale name now fails loudly with recovery text
  rather than silently doing nothing. Decision recorded in
  `tasks/tool-harness-cleanup.md`.
- **Process failure recorded** — the outage (F6) shipped because the test suite
  is blind to schema-adjacent renames: fresh databases are self-consistent under
  either column spelling. A verification run in this session also silently
  failed to execute (wrong working directory) and was briefly reported as green.
  Both lessons are captured in `tasks/lessons.md`.

## Outcome

The arc is verified at the code, registry, prompt, and database layers, and the
outage is fixed with its heal path proven against the real schema. Runtime
behaviour remains unverified pending a server restart (G1).
