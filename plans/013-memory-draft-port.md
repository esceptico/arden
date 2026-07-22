# 013 — Port the approved memory-workspace draft 1:1

**Status:** PORTED 2026-07-14 — desktop + server shipped on codex/memory-ledger-v2 (uncommitted); gates green (desktop 700/700 tests + typecheck + lint + build; server 1834 passed). Pixel-verified via CDP rig against the fixture vault (light/dark/nb/editor/activity/diff/sort/create).
**Draft:** `docs/mockups/memory-workspace-draft.html` (published: https://claude.ai/code/artifact/031fb505-bda2-4ca1-85a2-33f5343bbfff)
**Generator:** scratchpad `build-draft.py` + `draft-template.html` — builds the draft from the real vault at `~/.ntrp/memory`. The TEMPLATE is the pixel spec (CSS + behaviors).
**Hard rule (user-mandated):** port AS IS. No reinterpretation, no "improvements" during the port. Where the draft and the current app differ, the draft wins.

## Material model (final)

The window is the background; sidebars are surfaces above it — the app's own
`surface-panel` model (App.tsx:216): content area sits on `--color-bg`
(#ffffff / #0f0f0f), rail + context are floating panels inset 8px, radius 12,
`--surface-1` fill (#fafafa / #171717), `--shadow-5`. No borders/delimiters —
the surfaces do the separating. The note has NO card around it.

## Element → target map

All paths relative to `apps/desktop/src`. Data layer (ArtifactCache,
NavigationHistory, edit/review flow, external review queue, wiki resolution)
stays — the port swaps the shell and presentational layer.

| Draft element | Behavior to port | Target |
| --- | --- | --- |
| Single top strip (40px, on window bg): rail-toggle · back/fwd · doc tabs · edit-hint · edit btn · ctx-toggle. NO second header row, NO view title, NO breadcrumb. | ArtifactMemoryView header | rewrite |
| Doc tabs — flexible `flex:1` tabs, min 72 max 220px, centered label, absolute × on hover, hairline separators between inactive neighbors, `+` add; active = `fill-selected` tonal pill (ink-7%); tabs hold paths; open-in-new-tab via rc-menu | new tab state in ArtifactMemoryView | new |
| 3-column grid `var(--rail-w) minmax(0,1fr) var(--ctx-w)`, widths as inline CSS vars, 240ms emphasized transition; hidden panel → 0px + margin/shadow cleared | ArtifactMemoryView layout | rewrite |
| Rail = floating panel (margin 8px 0 8px 8px): search + toolbar (view toggle / new note / new folder / sort), pinned cluster, Obsidian tree — folders as 14px rows w/ chevron + indent guide; files show FILENAME stems (Obsidian-native); root files after folders | NotebookRail | rewrite |
| Pin — hover pin button on rows, persisted (localStorage), pin ≠ navigate; pinned cluster at top | NotebookRail | new |
| Right-click menu on rows: Copy path / Pin/Unpin / Open in new tab | new rc-menu component | new |
| Create note/folder — inline input under toolbar, pre-filled with current dir; Enter creates + opens editor; Esc/click-away cancels; nested paths create chains | rail + server create endpoint | new |
| Sort menu — Name / Time modified / Time created, re-click flips direction (↑/↓); scope line "All folders" vs "This folder — X" | rail popover | new |
| Notebook view (toggle): rail widens to 516px, ONE navigator, two panes. Nav 224px (panel tone): Pinned section (collapsible), root row "Memory" + total, folders w/ counts. List pane (`--panel-inner`: #fff / #1e1e1e, hairline left divider): head = folder name + count + descendants toggle (layers, default ON), Pinned first, date buckets (Today/Yesterday/This week/This month/Earlier), rows = title 13.5 semibold + 2-line snippet + "Jul 13, 2026 · parentdir", selected = accent-soft rounded-10, inset hairlines between rows | NotebookRail nb mode | new |
| Note header — h1 = FILENAME stem; body h1 renders in prose (never stripped); frontmatter = Properties | MemoryNote | edit |
| Properties — Obsidian-style typed grid under h1: icon per type (text/list/date/checkbox/number), click-to-edit, list values as pills w/ × and + append, Add property row, system props (type/updated) hidden | new Properties component | new |
| Records — collapsible "Records N" disclosure (open when page body empty); rows = 108px meta col (date mono 11, kind · imp) + 14px text; LIST ONLY (timeline REJECTED) — data from detail.timeline | MemoryNote | edit |
| Context panel = floating panel (margin 8px 8px 8px 0) w/ sticky icon toolbar: 3 panes — Links / Outline / Activity | MemoryInspector rewrite | rewrite |
| Links pane — Links (outgoing) + Linked mentions (backlinks grouped by source, ≤2 excerpts, warn-tint marks) + Unlinked mentions (title-text matches) | Links pane; server /links gains `unlinked` | edit |
| Activity pane — page-edit events: dot spine, `MM-DD HH:MM actor +N −N` rows; >5 → "All N events ›" expands in place; click row → diff overlay | new pane (data = getPageHistory) | new |
| Diff overlay — 840px dialog; header path · ts · actor · stat + Unified/Split segmented switch; body mono 12.5 pre-wrap (NEVER pre); split = row-aligned grid pairs; Esc/scrim closes | new overlay (event.patch/analysis) | new |
| Editor — body-only textarea (frontmatter excluded), mono 13/1.75, autosize; hint "⌘S review · Esc close" in TAB STRIP; blur crossfade; Esc exits; review flow unchanged | MemoryEditor | edit |
| Quick switcher — searches title+path; operators `folder:`, `@YYYY-MM`, `#type` | MemoryQuickSwitcher | edit |
| Scrims — `--scrim`: ink-12% light / rgba(0,0,0,.45) dark | styles.css | token |
| Note crossfade — 190ms blur(3px) opacity swap | existing poses | keep |

## Server additions (apps/server)

1. `POST /memory/notebook/create` — create empty note (and parent folders) or
   folder; rejects machine paths, existing paths; returns new artifact summary.
2. `GET /memory/links` response gains `unlinked`: pages whose text contains
   this page's title without a wikilink (word-boundary, ≤4, with context).
3. Artifact summaries gain `created_at` (first ledger event, fallback mtime)
   for sort-by-created and NN row dates.

## Removed / rejected during iteration (do not port, do not re-pitch)
- Horizontal records timeline (twice), "Recent" clusters, synthesized props
  rows, word counts, kind/origin chips over the title, evidence ruler,
  WYSIWYG, ⌘O kbd hint in search, footer stats line, view title row,
  breadcrumb header, parentheses-as-source detection, cards around the note
  content, second sidebar for notebook view.

## Out of scope
- Server-side authoring beyond the create endpoint (plan 012 still deferred).
- Any visual invention beyond the draft.

## Verification
- CDP rig (memory `reference_headless_chrome_screenshot_rig`): fixture server
  + vite + shot.mjs; READ the PNGs; compare against draft shots (v19/v20).
- Gate: `bun run typecheck && bun run lint && bun test tests/` from
  `apps/desktop` (702 baseline — memory tests updated to the draft spec),
  then `bun run build`. Server: `uv run pytest tests/` for new endpoints.
