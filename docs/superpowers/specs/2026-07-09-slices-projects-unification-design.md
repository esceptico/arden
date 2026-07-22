# Slices/Projects Unification — Design (Capabilities Model)

**Goal:** One container concept. A **slice** is the only grouping primitive;
what used to distinguish "slice" from "project" becomes optional
*capabilities* a slice grows. The parallel store, the name-slug bridge, and
every `slice_key` dies.

## The Model

There is one thing — a slice (storage: the existing `projects` table, which
is the durable SQL store; "slice" is the user-facing noun). Every slice has:

- **Identity:** `project_id` (existing primary key; survives renames — the
  root defect of the slug bridge).
- **Sessions:** chats filed into it (existing `sessions.project_id`).
- **Config:** `default_cwd`, `instructions`, `knowledge_scope` (existing).
- **A room:** every slice can open as a room (sessions, open loops,
  activity). A room without an agent shows what exists plus a "start
  watching" affordance.

And may grow capabilities:

- **Page** (`page_path`): a memory topic page the slice is grounded in.
- **Standing agent** (`autonomy`: observe/act + the channel automation):
  daily sweeps, asks, the autonomy dial. Requires a page.

"Promote" stops being a concept — you attach capabilities. The old
"promote suggestion" gesture becomes "create slice with page+agent" (new
container) or "attach page+agent" (existing container whose name matches).

**Home strip** shows only slices that are *live*: standing agent present or
open asks. Quiet containers (Design, mats, Life) don't clutter Home but have
rooms reachable from the sidebar.

**Triage** simplifies: candidate homes are just slices (one kind);
`TriageTarget.kind` and the dedup-by-slug logic die.

## Non-Goals

- No changes to what the standing agent does (sweep cadence, ask
  nomination, autonomy contract — all as-is, just re-keyed).
- No Projects-modal redesign; it remains the config surface, the room the
  living surface. Copy sweep only ("project" → "slice" in visible UI).
- No multi-page slices, no nested slices.

## Storage & Migration

**Schema:** `projects` table gains two nullable columns:
`page_path TEXT`, `autonomy TEXT` (`observe` | `act`; non-null iff the
slice has the agent capability — `page_path` non-null is implied then).

**Deleted:** `~/.arden/slices.json` (SliceRegistry), `sessions.slice_key`
(column stays physically but is no longer read/written; drop opportunistically
at next schema rev), the `_slug`/`_project_for_slice` bridge,
`ensure_project_for_slice` (obsolete — filing is just `project_id`).

**Boot migration (idempotent, one scan, mirrors the wiki-layer precedent):**
1. For each entry in `slices.json`: find project by slug-match (last use of
   the slug rule) else create; set `page_path`/`autonomy` from the entry.
2. Sessions: `slice_key` set → ensure `project_id` set via the same mapping.
3. Asks (`slices_state.json`): re-key `slice_key` → `project_id`.
4. Automations: `task_id = "slice:{key}"` → `"slice:{project_id}"`; clear
   `automations.slice_key` field usage (keyed by task_id convention only).
5. Suggestion store dismissals: keyed by page slug — unchanged (they refer
   to pages, not containers).
6. Rename `slices.json` → `slices.json.migrated` so the migration never
   re-runs and the old data remains recoverable.

## Server Changes (by module)

- **`slices/models.py`:** `Slice` dataclass becomes the projection row:
  `project_id`, `title` (project name), `page_path | None`,
  `autonomy | None`. `Ask.slice_key` → `Ask.project_id`.
- **`slices/registry.py`:** deleted. Readers get slices from the projects
  store via a thin loader (`slices_from_projects(projects) -> list[Slice]`,
  agented = `autonomy is not None`).
- **`slices/asks.py`:** re-key by `project_id` (mechanical rename).
- **`slices/agent.py`:** instructions/record keyed by `project_id`; task_id
  convention `slice:{project_id}`.
- **`slices/suggester.py`:** unchanged classification; acting on a
  suggestion writes `page_path`/`autonomy` onto a project row (create if no
  name match) instead of appending to slices.json.
- **`slices/service.py` + `server/app.py` snapshot closures:** filter
  sessions/automations by `project_id`; `_slice_sessions` keeps the
  primary-only filter.
- **`services/chat.py:629` context injection:** `session_state.slice_key` →
  look up the session's project; inject when it has `page_path`.
- **`core/spawner.py:391`:** child inherits `project_id` (already does);
  drop the `slice_key` copy.
- **`services/session.py` / `context/store.py`:** delete
  `move_session_to_slice`, `update_session_slice`, `slice_key` in
  `session_row`/create/provision. Filing = `move_session_to_project`.
- **Routers:**
  - `POST /sessions/{id}/slice` deleted (filing = existing `/project` move).
  - `/slices` router keyed by `project_id`: `GET /slices` (overview:
    live slices + focus asks + suggestions), `GET /slices/{project_id}`
    (room detail), `PUT /slices/{project_id}` (autonomy),
    `POST /slices/{project_id}/asks/{ask_id}/resolve`,
    `POST /slices` → becomes "attach/create with capabilities"
    (`{project_id | name, page_path}`).
  - `/sessions/{id}/triage`: candidates = all projects (title only, one
    kind); `TriageDecision.target` = `{project_id, title}`.
- **`server/runtime/automation.py`:** registry replaced by projects-store
  reads; seeding/agent handler keyed by `project_id`.

## Client Changes

- **Types/store:** `SessionListItem.slice_key` dies. `slices` domain keyed
  by `project_id` (`openSliceKey` → `openSliceId`). `SliceSummary` gains
  `project_id`; `live` derivation server-side as today.
- **Filing:** `acceptTriage` uses `moveSessionToProject` only;
  `moveSessionToSliceApi` deleted; `createSessionWithSlice` →
  `createSession(projectId)` (existing).
- **Sidebar:** the ↗ open-room action becomes universal on every group
  (drop the slug discrimination — every container has a room).
- **Chat breadcrumb:** slice chip renders from the session's project when
  that project is a live slice — else the plain project name (same look).
- **Home strip:** unchanged visually; data = live slices from overview.
- **Copy sweep:** visible "project" strings → "slice" (ProjectSettingsModal
  title, context-menu items, palette entries). Internal identifiers stay.

## Testing

- Migration: fixture slices.json + projects + slice-tagged sessions +
  `slice:{key}` automations → one boot → projects have capabilities, asks
  re-keyed, automations re-keyed, sessions linked, `.migrated` rename, and a
  second boot is a no-op.
- Triage: candidates single-kind; move fills `project_id`.
- Room detail by `project_id` incl. primary-session filter.
- Desktop: store/actions compile-level + existing suites re-keyed.

## Sequencing (one branch, server-first)

1. Schema + projection loader + boot migration (server green with both
   read paths for one commit while routers flip).
2. Re-key asks/agent/automations/service + routers; delete registry,
   `slice_key` writes, `/sessions/{id}/slice`, slug bridge.
3. Desktop re-key + deletions + copy sweep.
4. E2E against real data; then delete the `slice_key` read paths.
