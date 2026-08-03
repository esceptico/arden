# Research

## Surface research

- **Scope**: Verify the shipped tool-harness cleanup arc at `e5b33cc7` —
  namespace rename, deferral as a `ToolPolicy` flag, prompt refinement, internal
  identifier rename — and re-check the bug class behind the live outage.
  Separate implemented from verified.
- **Sources inspected**: `apps/server/arden/tools/{deferred,scopes}.py`,
  `apps/server/arden/tools/core/types.py`, `apps/server/arden/core/prompts.py`,
  `apps/server/arden/context/store.py`, `apps/server/tests/test_tool_namespaces.py`,
  `apps/server/tests/test_transcript_search.py`, `tasks/tool-harness-cleanup.md`,
  live registry via `ToolExecutor()`, live schema of `~/.arden/sessions.db`,
  `git log/diff` across `08b37ac8..e5b33cc7`.
- **Negative evidence**: no SQL column other than `search_text` collides with any
  old or new tool name (V-05); no renamed tool string appears outside the tool
  registry and presentation table (V-06); scope keys unchanged (F8).

## Consolidated findings

| ID | Type | Claim | Evidence | Implication | Confidence | Last checked |
| --- | --- | --- | --- | --- | --- | --- |
| F1 | fact | Arc origin: `_sessions` was absent from `DEFERRED_SOURCES`, so `create_session` sat in every chat's schema and was mis-called during wiki work | `tasks/tool-harness-cleanup.md`; pre-arc `deferred.py:12` | The fix must remove session tools from the always-on surface | high | 2026-08-03 |
| F2 | fact | Deferral is declared per tool via `ToolPolicy.deferred`, plus `source == "mcp"` | `apps/server/arden/tools/core/types.py:41-46`; `apps/server/arden/tools/deferred.py:120-124` | Deferral lives at the tool definition, not in a central membership map | high | 2026-08-03 |
| F3 | fact | Deferred group derives from the name prefix; `GROUP_ALIASES` only maps legacy words | `apps/server/arden/tools/deferred.py:127-129`, `:38-67` | The tool name is the single source of truth for grouping | high | 2026-08-03 |
| F4 | fact | Namespace/always-on/group-description invariants are test-enforced, not conventional | `apps/server/tests/test_tool_namespaces.py` | Future drift fails CI rather than silently widening the surface | high | 2026-08-03 |
| F5 | fact | The native tool-search prompt-drift trap is structurally removed; anchors are asserted | `apps/server/arden/core/prompts.py:28-83`, `:160-167` | A future prompt edit cannot silently no-op the native variant | high | 2026-08-03 |
| F6 | fact | `search_text` was also the `session_messages` column; the codemod rewrote it and the FTS triggers, breaking every message write on the live DB while tests stayed green | live schema dump (V-04); user report `OperationalError: table session_messages_fts has no column named file_search_text` | Fresh-DB test suites cannot prove schema-adjacent renames | high | 2026-08-03 |
| F7 | fact | No second instance of the F6 collision class exists | V-05, V-06 | The outage was a single-site failure, now fixed and healed | high | 2026-08-03 |
| F8 | fact | Scope keys persist, tool names do not; the key set is unchanged across the arc | `apps/server/arden/tools/scopes.py:23-37`; V-07 | Stored automation scopes were never at risk from the rename | high | 2026-08-03 |
| F9 | fact | Unrelated PR #190 (dead-code cleanup, `03a23537`) merged mid-arc, touching server and desktop | `git log --graph` (V-08) | Pre-merge gate results are stale; all gates re-run at `e5b33cc7` | high | 2026-08-03 |
| F10 | inference | The live app should recover on restart without manual DB surgery | V-04 heal run against the real schema + real rows | User action required is a restart only | medium-high | 2026-08-03 |

## Conflicts and gaps

- **G1 (open)**: no runtime observation. Nothing here proves the *model's*
  behaviour (loading the `session` group on demand, never reaching for
  `session_create` during content work) or the desktop's rendering of renamed
  tools. Requires a server restart; tracked in `verification.md`.
- Resolved conflict: an earlier "clean" full-suite run in this session silently
  failed to execute (wrong working directory) and was briefly reported as green
  while 14 tests were failing. All results in this ledger come from runs whose
  summary line is quoted verbatim.

## Supporting material

- Damaged-database repro script and heal transcript: recorded inline in
  `verification.md` V-04 (built from the real `~/.arden/sessions.db` schema and
  300 real rows; the 8 GB database itself was not copied).
