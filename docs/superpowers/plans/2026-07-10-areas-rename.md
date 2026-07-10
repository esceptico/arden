# Areas rename — slices + projects → areas (one concept, one name)

User decision 2026-07-10: rename ALL "slice"/"project" naming to "area" —
UI copy, code identifiers, API routes and JSON keys, SQL schema, state files.
Motivation: after unification there is one concept; multiple names for it are
debt ("otherwise we will have 100500 different names for one concept").

## Locked decisions

- Storage: `projects` table → `areas`; `project_id` columns → `area_id`
  (areas + sessions tables). Idempotent boot migration (runs before the
  existing slices.json migration in lifespan).
- Id VALUES stay opaque — existing `proj_*` ids keep working, new ids mint
  `area_`. No re-keying of stored references (sessions, asks, automations).
- Asks `slice_key` field → `area_key` (state file migrated on load or boot).
- Automation task_id format `slice:{id}` → `area:{id}` (re-key at boot if any
  exist; live DB currently has none).
- SSE event `slices_changed` → `areas_changed` (server + client together).
- State files: `slices-state.json` → `areas-state.json`,
  `slices-suggestions.json` → `areas-suggestions.json` (rename at boot).
- API: one surface under `/areas`:
  - GET /areas — flat list (was GET /projects)
  - GET /areas/overview — home overview {areas, focus, suggested} (was GET /slices)
  - GET /areas/{id} — room detail (was GET /slices/{id}); declare AFTER /areas/overview
  - POST /areas — create-or-reuse by name, optional page_path (absorbs
    POST /projects and POST /slices attach-by-name; find_area_by_name inside)
  - PATCH /areas/{id} — update, incl. attaching page_path (absorbs POST /slices project_id branch)
  - PUT /areas/{id}/autonomy, asks + suggestions routes move under /areas
- Desktop: features/slices → features/areas; api/actions/store/types renamed;
  sidebar pref value sidebarGroupBy "project" → "area" (stale stored value
  falls through to default grouping — acceptable, no migration).
- KNOWN COST: iOS app real-API mode breaks on `project_id`→`area_id` wire keys
  until updated (surfaced to user in-session).
- SCOPE CUT (explicit): the MEMORY subsystem keeps its internal "project"
  vocabulary (scope kind "project", page frontmatter `type: project`,
  knowledge_scope, vault AGENTS.md conventions) — renaming it means migrating
  the vault + memory.db, and file_store.py is owned by a parallel session.
  Only memory/project_names.py's direct SQL against the renamed table changes.

## Stages (commit per stage; gates each time)

- [x] 1. Server code: ntrp/slices → ntrp/areas module; Slice→Area classes/fns;
       slice_key→area_key; slice:→area: task ids; events; runtime/automation.
- [x] 2. Server storage: schema rename + boot migration (projects→areas,
       project_id→area_id, state-file renames, task-id re-key, asks field).
- [x] 3. Server routers: unified /areas API as above; delete /projects + /slices.
- [x] 4. Server tests: rename + update; full pytest gate
       (2 pre-existing failures allowed: test_render_html_tool, test_tools).
- [x] 5. Desktop: api/types/store/actions/features rename + UI copy ("Areas");
       SSE event name; gates = typecheck + lint + bun test tests/.
- [x] 6. Live verify: migration dry-run against a COPY of ~/.ntrp/sessions.db;
       preview harness pass on sidebar/home/popover/room.
- [x] 7. Docs sweep (CLAUDE.md has no slices mentions — no change) + memory update.

## Session traps (for any executor)

- NEVER `git add` wholesale — name files (parallel session owns unrelated
  dirty files: memory/file_store.py, migrate_to_files.py, research_artifacts.py etc.).
- Never push; commit to local main only. Never touch the user's running
  server or dev processes; preview via renderer-alt (port 5186) only.
- Desktop tests: `bun test tests/` from apps/desktop. Server: `uv run pytest
  tests/ -q` + `uv run ruff check ntrp/` from apps/server.
- The user's live server is RUNNING on the old schema — the boot migration
  must be idempotent and only run on their restart.
