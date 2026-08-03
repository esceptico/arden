# Implementation

> A checked item means implemented, not verified.

## Intended outcome

Tool names carry their owning surface as a prefix; deferral is a property of
each tool rather than a central source set; the always-on schema surface is
small and deliberate (the incident tool is not in it); prompts and internal
Python identifiers match the new names; the live database survives the rename.

## Checklist

- [x] **I-01 — Rename all 96 tools to namespace-prefixed names**
  - Outcome: `session_create`, `wiki_create_page`, `email_search`, `file_read`, …
    Rule: namespace first, remainder natural prose (no forced verb reordering).
  - Scope: server registry keys, prompts, skills, docs, desktop presentation and
    the Electron device-executor protocol names.
  - Commit: `08b37ac8`.
  - Required verification: V-01, V-02, V-09.
- [x] **I-02 — Replace source-set deferral with `ToolPolicy.deferred`**
  - Outcome: 67 tools flagged; MCP always deferred; `DEFERRED_SOURCES`,
    `DEFERRED_TOOL_GROUP_BY_NAME`, `DEFERRED_GROUP_LABELS` deleted.
  - Commit: `08b37ac8`.
  - Required verification: V-01, V-02.
- [x] **I-03 — Derive deferred groups from the name prefix**
  - Outcome: `deferred_group()`; `GROUP_ALIASES` reduced to legacy-word mapping.
  - Commit: `08b37ac8`.
  - Required verification: V-02, V-03.
- [x] **I-04 — Defer the session tools (the incident fix)**
  - Outcome: all `session_*` deferred behind group `session`, whose description
    states that creating a chat is never part of a content task.
  - Commit: `08b37ac8`.
  - Required verification: V-01 (static), G1 (runtime).
- [x] **I-05 — Remove the prompt replacement-dict drift trap**
  - Outcome: `(classic, native)` pairs defined once, embedded in
    `BASE_SYSTEM_PROMPT`, swap asserts each anchor. Stale `background(task)`
    prose replaced (that tool was retired in `ff01fec2`).
  - Commit: `08b37ac8`.
  - Required verification: V-03.
- [x] **I-06 — Enumerate newly deferred groups in tool-loading guidance**
  - Outcome: session management, fact history/corrections and skill creation
    named in both the `load_tools` and native `tool_search` prompt headers.
  - Commit: `3268ee05`.
  - Required verification: V-03.
- [x] **I-07 — Add namespace invariant tests**
  - Outcome: `tests/test_tool_namespaces.py` pins the prefix table, the exact
    always-on set, and group-description coverage.
  - Commit: `08b37ac8`.
  - Required verification: V-01.
- [x] **I-08 — Rename internal Python identifiers to match**
  - Outcome: tool functions, `approve_*` handlers, `*_tool` variables, Input
    model classes, wiki tool-name constants. Non-tool same-named symbols kept:
    HTTP route handlers, store/service methods, gmail client, revisions
    `read_file`, `ToolExecution.request_connection`.
  - Commit: `bea89e0b`.
  - Required verification: V-01, V-09.
- [x] **I-09 — Add the Daily Notes v2 prompt upgrade**
  - Outcome: databases seeded with old tool names in the daily-notes prompt
    self-heal at boot.
  - Commit: `08b37ac8`.
  - Required verification: not independently exercised — see G2.
- [x] **I-10 — Revert the `search_text` column and heal damaged databases**
  - Outcome: column and FTS triggers restored; migration drops the stray
    `file_search_text` column after dropping triggers; regression test
    reproduces the damaged on-disk state.
  - Commit: `e5b33cc7`.
  - Required verification: V-04 (real-schema heal), V-09.

## Notes

- Deliberately not done: renaming the *scope keys*, integration ids
  (`gmail`, `google_drive`), or provider wire identifiers
  (`tool_search_tool_bm25`, `tool_search_call`) — all of these persist or are
  owned by an external protocol.
- Deliberately not done: a regression eval for the original mis-call incident;
  the user judged it too context-conditional to reproduce.
