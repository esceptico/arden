# Tool Harness Cleanup — LEDGER

Origin: bad "Discuss job applications" session — agent repeatedly called `create_session`
while doing wiki-page work. Root cause: `_sessions` source is NOT in `DEFERRED_SOURCES`
(`apps/server/arden/tools/deferred.py`), so `create_session` sits in every chat's schema.

## Settled decisions (2026-08-03 discussion)

- Namespaces via **name prefixes** (dots illegal: API tool names `^[a-zA-Z0-9_-]{1,64}$`).
  Prefix doubles as the workspace handle — `tools.prefix()` exists in `core/scope.py`.
- Name shape: **namespace prefix first, remainder = natural prose** (no forced
  resource/verb reordering inside the remainder — settled 2026-08-03, supersedes the
  earlier strict `<resource>_<verb>` draft).
- After rename, **name-prefix ↔ registry source agree 1:1** — enforced by a registry test.
- `deferred.py` hand-maintained maps (`DEFERRED_SOURCES`, `DEFERRED_TOOL_GROUP_BY_NAME`,
  `GROUP_ALIASES`) become **derived from prefix/source**.
- No regression eval for the incident; deferral pass gets a deferred-catalog snapshot test.
- Order: rename table (user review) → mechanical rename → prefix-derived deferral →
  prompt refinement.

## Registry state (re-measured 2026-08-03)

96 tools, 38 always-on. Drift since first dump: `background` spawn tool RETIRED
(ff01fec2; `_background` = `cancel_agent` only), new `_fact_capture` source with
`fact_capture_review` (79718f76).

Three stacked visibility mechanisms — evaluate per run context (chat / area / research /
automation): capability gating, per-run `allowed_tool_names`, deferral.

## Rename table (FINAL rule: namespace prefix first, remainder = natural prose)

Rule settled 2026-08-03: put the namespace at the start as a prefix; keep the rest
reading naturally. No forced <resource>_<verb> reordering inside the remainder.

### session_ (absorbs `_sessions` + lifecycle tools from `_app_control`)
| old | new |
|---|---|
| create_session | session_create |
| list_recent_sessions | session_list |
| read_session | session_read |
| search_transcripts | session_search_transcripts |
| rename_session | session_rename |
| archive_session | session_archive |
| send_message | session_send_message |

### app_ (rump of `_app_control`)
| old | new |
|---|---|
| open_in_app | app_open |
| request_attention | app_request_attention |
| followup_task | app_followup_task |

### automation_
| old | new |
|---|---|
| create_automation | automation_create |
| list_automations | automation_list |
| update_automation | automation_update |
| delete_automation | automation_delete |
| run_automation | automation_run |
| list_automation_runs | automation_list_runs |
| get_automation_result | automation_result |

### loop_ (split from `_automation`)
| old | new |
|---|---|
| create_loop | loop_create |
| schedule_wakeup | loop_schedule_wakeup |
| loop_done | loop_done (unchanged) |

### agent_ / directives_ / notify
| old | new |
|---|---|
| cancel_agent | agent_cancel |
| get_directives | directives_get |
| set_directives | directives_set |
| notify | notify (unchanged) |

### wiki_
| old | new |
|---|---|
| create_wiki_page | wiki_create_page |
| edit_wiki_page | wiki_edit_page |
| archive_wiki_page | wiki_archive_page |
| move_wiki_page | wiki_move_page |
| read_wiki_page | wiki_read_page |
| list_wiki_pages | wiki_list_pages |
| list_wiki_changes | wiki_list_changes |
| wiki_links | wiki_links (unchanged) |
| publish_wiki_generated | wiki_publish_generated |

### fact_
| old | new |
|---|---|
| search_facts | fact_search |
| get_fact | fact_get |
| get_fact_history | fact_history |
| get_due_fact_reviews | fact_due_reviews |
| plan_fact_changes | fact_plan_changes |
| commit_fact_changes | fact_commit_changes |
| fact_capture_review / fact_maintenance_review | unchanged |

### skill_ / goal_ / todo_
| old | new |
|---|---|
| create_skill | skill_create |
| use_skill | skill_use |
| get_goal | goal_get |
| complete_goal | goal_complete |
| block_goal | goal_block |
| update_todos | todo_update |

### file_ (also the device-executor protocol — desktop moves in same change)
| old | new |
|---|---|
| read_file | file_read |
| write_file | file_write |
| edit_file | file_edit |
| list_files | file_list |
| find_files | file_find |
| search_text | file_search_text |

### email_ (integration id stays `gmail` — id persists in connection state)
| old | new |
|---|---|
| emails | email_search |
| read_email | email_read |
| send_email | email_send |
| reply_email | email_reply |

### calendar_
| old | new |
|---|---|
| calendar | calendar_search |
| create_calendar_event | calendar_create_event |
| edit_calendar_event | calendar_edit_event |
| delete_calendar_event | calendar_delete_event |

### drive_ (integration id stays `google_drive`)
| old | new |
|---|---|
| search_google_drive | drive_search |
| read_google_doc | drive_read_doc |
| edit_google_doc | drive_edit_doc |
| create_google_doc | drive_create_doc |
| read_google_sheet | drive_read_sheet |
| create_google_sheet | drive_create_sheet |
| update_google_sheet | drive_update_sheet |
| append_google_sheet_rows | drive_append_sheet_rows |

### slack_ (all 11 unchanged — already prefix-first, prose remainder fine)

### area_ (prefix preserved for `tools.prefix("area_page_")` scopes)
| old | new |
|---|---|
| submit_area_report | area_submit_report |
| area_run_automation / area_page_read / area_page_write / area_page_patch | unchanged |

### connection_
| old | new |
|---|---|
| request_connection | connection_request |

### unchanged singles
web_search, web_fetch, bash, current_time, render_html, research, workflow,
load_tools, tool_search, wiki_maintenance_review, fact_maintenance_review,
fact_capture_review, notify, loop_done.

Prefix↔source mapping is an explicit table (sources keep `_` internal marker;
integration ids gmail/google_drive keep their persisted ids) — enforced by test.

## Deferral target (after rename; grouping prefix-derived)

Always-on in normal chat (~20): load_tools, tool_search, todo_update, current_time,
file_read, file_list, file_find, file_search_text, bash, web_search, web_fetch,
fact_search, fact_get, research, workflow, skill_use, render_html, connection_request.

Newly deferred: session_* (all), fact_history, fact_due_reviews, fact_plan_changes,
fact_commit_changes, skill_create.

Capability-gated special-run tools (goal_*, area_*, *_maintenance_review,
fact_capture_review) unchanged — already invisible in chat.

## Touch-point ledger

(mark [x] as each lands)

- [x] RESEARCH: server touch points (done, see below)
- [x] RESEARCH: desktop touch points (done, see below)
- [x] Rename table reviewed by user (rule settled: prefix + natural prose remainder)

### Server touch points (researched 2026-08-03)

**MIGRATION-CRITICAL (names persisted to disk/DB):**

- [x] `automation/predefined.py:11-37` — DAILY_NOTES prompt seeded ONCE into automations
      table, never re-synced → existing installs keep old tool names in the prompt.
      Needs a one-time prompt refresh or version bump on rename.
- [x] User-created automation prompts (free text in automations table) routinely name
      tools — no migration path; acceptable: old names in prose degrade to "unknown
      tool" errors with recovery text. DECIDE: leave, or one-shot regex migration.
- [x] `automation/builtins.py` — builtin prompts re-sync on startup (self-healing) ✓
      but verify after rename.
- [x] `server/runtime/automation.py:145` — compares STORED `call["tool_name"]` against
      code constant `READ_WIKI_PAGE_TOOL_NAME` → breaks against historical rows after
      rename. Needs old-name tolerance or backfill.
- [x] `context/store.py:2554` — literal `"tool_name": "request_connection"` written into
      persisted invocation rows.
- [x] `tools/app_control.py:244` — `send_message:{tool_id}` as persisted idempotency
      client_id (probably harmless across rename — verify).
- Scope keys persist, not tool names — scopes safe ✓ (don't rename scope KEYS).

**TRAPS:**

- [x] `core/prompts.py:106-118` — `_base_system_prompt` replacement dict keys are
      VERBATIM copies of prompt lines; unmirrored edit silently no-ops the native
      tool-search variant. Fix structurally while here (derive, don't duplicate).
- [ ] `tools.prefix("area_page_")` in scopes.py:45 AND core/scope.py:6 (duplicated) —
      prefix dependencies; renames must preserve prefixes. Post-rename these get
      SIMPLER (prefix == namespace).
- [x] `tool_search` display-name special case duplicated: agent/agent.py:688,706 +
      server/routers/session.py:289.
- [x] Provider wire identifiers `tool_search_tool_bm25`/`tool_search_call` in
      llm/anthropic.py + llm/openai_responses.py are Anthropic/OpenAI protocol names —
      do NOT rename; only the local `name="tool_search"` mapping moves.

**CODE LITERALS (rename sweep units):**

- [x] `integrations/core.py:93-251` — the big registry key map (every builtin tool name)
- [x] `integrations/{gmail,calendar,google_drive,slack,web}/__init__.py` — key maps
- [x] `tools/scopes.py:45-86` — raw `tools.named()` literals (12 names)
- [x] `tools/deferred.py` — GROUP maps + Jinja templates (mostly DELETED by derivation)
- [x] `tools/discover.py:12-26` — RESERVED_USER_TOOL_NAMES (research_* — unchanged?)
- [x] `tools/research.py:568,273` — exclude sets
- [x] Error recovery_action strings across tools/*.py (sessions 9×, app_control 20×,
      wiki 12×, automation 14×, facts 5×, directives, goals, area, files 7×,
      render_html, background)
- [x] `core/tool_executor.py:32` — LIVE_READ_TOOLS = {"list_recent_sessions"}
- [x] `core/tool_executor.py:108-110` — load_tools/tool_search error text
- [x] `core/deferred_tools_middleware.py` — tool_search/load_tools literals
- [x] `core/model_context_budget.py`, `core/tool_result_files.py` — read_file/search_text hints
- [x] `services/chat.py:75` — INIT_AUTO_APPROVE = {plan_fact_changes, commit_fact_changes}
- [x] `tools/core/context.py:108` — Literal["load_tools","tool_search"] type
- [ ] Constants already centralized ✓: wiki/constants.py, fact capture/maintenance
      __init__.py, scopes.py AREA_REPORT_TOOL_NAME — rename at definition only.

**PROMPTS (prose, stage 4 overlaps):**

- [x] `core/prompts.py` — densest site (L22-319, ~40 names incl. dead `background`)
- [x] `core/agent_types.py:30-31`, `services/goal_continuation.py:27`,
      `services/chat.py:1165`, `tools/core/context.py:447,678,694`, `areas/agent.py:116`
- [x] `tools/deferred.py:82,85,372,603` — group descriptions + schema examples

**UI-HINT EMITTER:**

- [x] `agent/types/tool_presentation.py:18-67` — icon/noun table (~40 names); misses
      degrade silently to default icon.

**BUILTIN SKILLS (prose on disk):**

- [x] `skills/wiki-automation/SKILL.md` (9 names; L52 tells agent to put tool names into
      tool_scope — likely STALE vs scope-key contract, verify/fix regardless)
- [x] `skills/loop/SKILL.md` (create_loop ×6, loop_done, schedule_wakeup)
- [x] `skills/propose-automation`, `propose-skill`, `add-skill`, `add-tool`, `mermaid`,
      `implement` SKILL.md files
- [x] Docs: `integrations/README.md:44`, `apps/server/README.md:13`

**TESTS:** 64 files reference tool names; top churn: test_deferred_tools (142),
test_wiki_tools (131), test_fact_tools (92), test_app_control_tools (83),
test_session_tools (69), test_loop_tools (66). Read `test_tool_documentation.py` +
`test_tool_registry_ergonomics.py` FIRST — they may enforce invariants the rename
must satisfy.

### Desktop touch points (researched 2026-08-03)

Good news: desktop is mostly **server-hint driven** (`tool_presentation` sends
`display_name`/`icon`/`noun`/`kind`/`source` on TOOL_CALL_* events; hints win at render
time). A new tool needs zero desktop changes. A rename breaks exactly these:

- [x] `src/features/chat/lib/operationLabel.ts:48-106` — `TOOL_META` fallback map
      (~44 tool names; used for history reload). NOTE: contains dead names
      (`memory_search`, `recall`, `remember`, `forget`, `memory_read`, `memory_patch`,
      `memory_tree`, `memory_rebuild`, `background`?) — prune while here.
- [x] `operationLabel.ts:109-121` — `PREFIX_ICON` regexes key on name SHAPE
      (`^slack_`, `email`, `session|transcript`, `automation|loop|wakeup`) — silent
      dependency: renames shift icons without touching a literal. Re-derive after rename;
      prefix-based names actually make these regexes cleaner.
- [x] `src/stores/transcript-projection-types.ts:35` — `TODO_TOOL_NAME = "update_todos"`.
- [x] `electron/executor-tools.cjs:773-779` — device-tool handler names
      (`bash`, `read_file`, `list_files`, `find_files`, `search_text`, `write_file`,
      `edit_file`) — MUST match server's remote-executor protocol names; coordinate with
      server rename of file_* tools.
- [x] Desktop tests (rename fixtures alongside): operationLabel, htmlWidget*,
      streamEvents, historyMessages, activityTraceReplay, normalizeActivityGroups,
      workingLabel, executorDeviceTools, executorClient; other hits are arbitrary
      fixtures (write_file etc.) and `web_search` as a settings key (NOT a tool ref).

Not touch points (verified generic): ApprovalBanner, ToolsTab settings, automations
scope UI (uses scope keys + server labels), ActivityRows icon keys, SourcesPanel
provider keys.

## Status (2026-08-03, end of implementation session)

Stages 1-4 DONE — COMMITTED as 08b37ac8 + 3268ee05 on main (not pushed); server
restart pending for live activation:

- Rename SHIPPED: all 96 tools carry namespace-prefix names (codemod over quoted +
  backticked + string-token occurrences; f-strings and JS object keys hand-fixed).
  False-positive class to remember: quoted strings that name SERVICE METHODS
  (getattr(svc, "get_goal"), monkeypatch.setattr(source, "reply_email")) were reverted.
- Deferral SHIPPED via a new mechanism: `ToolPolicy.deferred: bool` flag per tool
  (67 flagged), MCP always deferred; groups DERIVED from name prefix
  (deferred_group = first `_` segment). DEFERRED_SOURCES /
  DEFERRED_TOOL_GROUP_BY_NAME / DEFERRED_GROUP_LABELS deleted; GROUP_ALIASES kept
  small for legacy words. Incident fix live: session_* all deferred behind group
  "session" whose description says creating a chat is never part of a content task.
- Always-on surface: 29 tools (20 in a normal chat; 9 capability-gated to special
  runs). Locked by tests/test_tool_namespaces.py (exact always-on set + every name
  prefix known + every derived group described).
- prompts.py replacement-dict trap KILLED: (classic, native) pairs defined once,
  embedded into BASE_SYSTEM_PROMPT, swap asserts anchors. Stale background(task)
  prose replaced with research()-based agent-control text.
- Persisted data: DAILY_NOTES_PROMPT_V2 upgrade path added (old-name prompts
  self-heal at boot); runtime/automation.py:145 verified NO-OP (validates current
  run only, never historical rows); user automation prompts left as-is (stale names
  fail loudly with recovery text).
- Desktop: operationLabel TOOL_META renamed + dead memory_* names pruned,
  PREFIX_ICON regexes now anchor on real prefixes, TODO_TOOL_NAME renamed,
  executor-tools.cjs device protocol renamed in lockstep with server file_* tools.
- Gates: server pytest 2464 passed, ruff clean+formatted; desktop 1039 tests pass,
  typecheck + eslint clean.

Remaining follow-ups:
- [ ] Live smoke after server restart (chat loads session group on demand;
      wiki task never sees session_create; desktop labels/icons on :5175)
- [x] Internal Python identifiers renamed (bea89e0b): tool fns, approve_* handlers,
      *_tool vars, Input classes, wiki constants — tokenize pass (NAME tokens only,
      dot-attribute-safe, per-name blacklists). Same-named NON-tool symbols kept:
      routers, store/service methods, gmail client, revisions read_file,
      ToolExecution.request_connection. Traps found: test fixture methods mimicking
      service interfaces (get_goal fakes), dataclass field + attr-access mismatch
      (OperatorDeps.create_session), module-attr test refs, quoted annotations.
- [x] Committed: 08b37ac8 (rename + deferral flag), 3268ee05 (prompt enumerations)
