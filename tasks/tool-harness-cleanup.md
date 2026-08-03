# Tool Harness Cleanup — deferral + namespaces + prompts

Origin: bad "Discuss job applications" session — agent repeatedly called `create_session`
while doing wiki-page work. Root cause confirmed: `create_session` is in the `_sessions`
source, which is NOT in `DEFERRED_SOURCES` (`apps/server/arden/tools/deferred.py:12`),
so it sits in every chat's schema list.

Decisions settled in discussion (2026-08-03):

- Namespaces via **name prefixes** (dots are illegal: API tool names must match
  `^[a-zA-Z0-9_-]{1,64}$`). Prefix doubles as the workspace handle for scope filtering —
  `tools.prefix()` already exists in `core/scope.py` (used once, `area_page_`).
- Name shape: **resource first, action after** — `<resource>_<verb>[_<qualifier>]`.
  `session_create`, `wiki_page_edit`, `email_send`, `calendar_event_create`, `fact_search`.
- After rename, **name-prefix and registry source must agree 1:1** — add a registry test.
- Then `deferred.py`'s hand-maintained maps (`DEFERRED_SOURCES`,
  `DEFERRED_TOOL_GROUP_BY_NAME`, ~40 lines of `GROUP_ALIASES`) become **derived from
  prefix/source** — namespace is the single source of truth for scoping, deferral
  grouping, and display grouping.
- **No regression eval** for the incident (too context-conditional). Deferral pass gets a
  cheap unit test instead: deferred-catalog snapshot per capability set.

## Current state (measured 2026-08-03, via live ToolExecutor dump)

96 tools total, 38 always-on. Three stacked visibility mechanisms — review per run
context (chat / area / research / automation), not the flat list:

1. capability gating (tool hidden when its service is absent)
2. `allowed_tool_names` per run
3. deferral (`DEFERRED_SOURCES` + `load_tools` / native `tool_search`)

## Always-on target list (~20, normal chat)

- Loop plumbing: `load_tools`, `tool_search`, `update_todos`, `current_time`
- Local reads: `read_file`, `list_files`, `find_files`, `search_text`, `bash`
- Web: `web_search`, `web_fetch`
- Memory recall: `search_facts`, `get_fact`
- Spawn surfaces: `background`, `research`, `workflow`, `use_skill`
- Chat output: `render_html`
- Capability-gated special-run tools (`_goals`, `_area`, maintenance reviews) stay as-is —
  already invisible in chat.

## Newly deferred

- All of `_sessions`: `create_session`, `list_recent_sessions`, `read_session`,
  `search_transcripts`
- Fact mutations/maintenance: `plan_fact_changes`, `commit_fact_changes`,
  `get_due_fact_reviews`, `get_fact_history`
- `create_skill`
- `request_connection` — judgment call; it's the "connect Gmail" discovery path, so the
  deferred-group description must carry that.

## Migration surface for the rename

Smaller than feared: **scopes persist only keys** — filters live in code
(`apps/server/arden/tools/scopes.py`: "a stored scope contains no tool names"). No DB
migration for scopes. Renames touch:

- code literals: `tools.named(...)` in `scopes.py`, tool-name constants
  (`PUBLISH_WIKI_GENERATED_TOOL_NAME` etc.), `RESERVED_USER_TOOL_NAMES` in `discover.py`
- prompts: `core/prompts.py`, agent_surface, deferred-group descriptions
- `deferred.py` maps (replaced by derivation, see above)
- skills markdown under `apps/server/skills/` that mentions tool names
- desktop tool presentation (name → icon/label mapping)
- tests
- old transcripts keep old names — display-only, harmless

## Plan (order matters: rename before deferral so grouping is derived from final names)

1. **Rename table** — all 96 tools, old → new, one doc for review BEFORE any mechanical
   change. Judgment calls live here (`emails` → `email_list`? `slack_post_message` →
   `slack_message_post`?). Optionally run the `tool-harness-audit` skill first so schema
   warts get fixed in the same sweep.
2. **Namespace rename** — mechanical sweep across code / prompts / skills / desktop /
   tests, plus the prefix↔source registry test.
3. **Deferral pass** — prefix-derived grouping, move newly-deferred set out of always-on,
   deferred-catalog snapshot test.
4. **Prompt refinement** — `core/prompts.py` + deferred group descriptions (some stale,
   e.g. `_background`'s description apologizes for its own shape).

Status: PARKED — user resolving memory-subsystem issues first; resume from step 1.
