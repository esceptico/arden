# Arden desktop roadmap

Living punch list. Tick items as they ship; replace this file when it goes stale.

Status syntax: `[ ]` = todo · `[~]` = shipped, not yet manually tested · `[x]` = shipped + verified.

Out of scope for now (per user, 2026-05-05): notifications panel, tools/skills browser.

---

## Reliability bugs (fix now, in order)

These are the four issues landed 2026-05-06 — fix one at a time with full review.

1. [x] **Tool result truncation must always offer a continuation path.** Audited; three offending sites removed (`bash.py`, `read_url`, gmail `read_email`). `OFFLOAD_THRESHOLD = 50000` is the single knob; tools no longer trim their own output. `BASH_TIMEOUT` set to 120s as a runaway-command brake. Open follow-up: bash captures full stdout in memory before offload writes it; streaming subprocess → temp file would handle multi-GB output without an RSS spike, but no current evidence we need it.
2. [x] **Sub-agent failure must salvage prior work.** `spawner._stream_to` now catches non-cancel exceptions, runs a cheap synthesis pass over the (clamped) accumulated `child_messages`, returns `[partial — errored: …]` + summary. Foreground and background timeout paths also salvage. Per-tool errors were already tolerated by `agent/tools/runner.py`. Deferred: LLM-API-error retry-with-clamp inside the agent loop — offload now keeps tool results small enough that this hasn't reproduced post-fix; revisit if a real case shows up.
3. [x] **Tool calls invisible after research returns.** Root cause: `ActivityTail` static-mode (post-run, expanded panel) filtered to depth-0 items only — sub-agent's nested tools were dropped. Replaced with `buildStaticTree` that DFS-walks the parent→child tree and emits everything indented by depth, with an orphan-fallback pass for items whose parent landed in a different activity bucket. Memoized.
4. [x] **Cross-session streaming durability.** Server-side `_recent` event buffer on each `SessionBus` (deque, maxlen 10000), wiped at every `save_progress` checkpoint so disk and buffer never overlap. New `subscribe_with_replay()` atomically returns `(snapshot, queue)` and the SSE route yields the snapshot first. `submit_chat_message` persists the user message immediately (was only in memory until first step finished). `update_progress` upserts so a brand-new session's first save lands. `/session/history` returns `active_run_id`; client uses it to keep the in-flight turn from collapsing under "Worked for X" and re-arms the thinking indicator on switch-back. `setCurrentSession` resets `running: false` so the loader doesn't carry over to an idle session. Passthrough events (`ThinkingEvent`, `MessageIngestedEvent`) added so they don't fragment text-message boundaries.

## Server gaps with no UI

These are server endpoints that already work; just need a desktop surface.

- [~] **MCP server manager** — `/mcp/servers` list, add, enable/disable, tool whitelist, OAuth flow. _Implemented in `Settings → MCP servers` tab; not yet manually tested end-to-end._
- [ ] **Background tasks panel** — `/chat/background-tasks`, cancel, view results.
- [ ] **Pending messages queue** — `DELETE /chat/inject/{client_id}` exists; `MessageIngestedEvent` already streams. Show queued user messages while a run is in flight, with cancel-before-ingest. Currently the user can submit while a run is running but has no visibility into whether/when the message lands.
- [~] **Memory observability tab** — `/stats`, `/memory/audit`, `/memory/events`, `/memory/access/events`, `/memory/recall/inspect`. _Implemented as Search plus Advanced → Sent/Cleanup/Audit; learning, dreams, merge review, and injection-policy preview surfaces were stripped._
- [ ] **Run history view** — `/chat/runs/status`. Per-run metadata, timing, replay.
- [ ] **Provider discovery** — `/providers`, `/tool-providers`. Connect new models/integrations.
- [ ] **Directives editor** — `/directives` PUT. Edit the system-level directives file from the app.

## Ideas worth stealing from other desktop AI apps

- [x] ~~**`@`-mention typed context picker**~~ — built v1 (memory-only); reverted. Use case wasn't clear and the UX didn't land. Park it; revisit if a concrete need shows up. (Cody.)
- [ ] **Live token meter + auto "summarize-and-fork" banner** above the composer. Leverages existing compaction events; fixes the "how full is context?" blindspot. (Zed.)
- [ ] **Tool/permission profiles per chat** — Write / Ask / Minimal scopes the agent's tools+model in one click. (Zed.)
- [ ] **Slash commands as parameterized templates** — `SKILL.md`-style with `{{var}}` arg prompts. (Cursor Skills, Warp Workflows.)
- [ ] **Automations with `{{var}}` placeholders + quick-fire dialog** — turns scheduled prompts into reusable templates that can also be fired ad-hoc. (Warp Workflows.)
- [ ] **Background agents that produce reviewable diffs** — long-running entropy-reduction tasks (inbox triage, vault cleanup) run async and present a changeset to review. (Cursor Background Agents.)
- [ ] **Global OS hotkey + screenshot-to-chat / "work with apps"** — Electron `globalShortcut`, capture from anywhere on the OS, promote captures to memory. (ChatGPT, Witsy, Pieces.)
- [ ] **Voice capture → structured note** — voice in, fact-extraction-ready transcript out. (Granola, Cleft, Bee.)
- [ ] **Editable memory timeline view** — chronological fact/pattern changes with inline edit and source links. (Bee.)
- [ ] **Persistent project containers** — `Project = (instructions + files + memory scope)` shared across sessions. (Claude Projects, LibreChat Presets.)
- [ ] **Live/interactive artifacts** — chat-embedded UIs that re-render with fresh data. (Claude Live Artifacts, MCP Apps.)

## Ranked picks (next-to-do, biased to Arden's "personal entropy reduction" framing)

1. ~~**`@`-mention context picker**~~ — built and reverted; parked.
2. [ ] **Live token meter + auto-fork banner** — cheapest win; landed in ~an hour using existing compaction signal.
3. [~] **MCP server manager** — without it, MCP is power-user-only. _Shipped, untested._
4. [~] **Memory observability tab** (audit + events + recall inspect) — trust-builder for the memory work. _Shipped in desktop Memory modal; needs manual testing._
5. [ ] **Global hotkey + screenshot-to-chat** — biggest "real personal app" lift; needs Electron `globalShortcut` + native capture.
6. [ ] **Automation `{{var}}` placeholders + ad-hoc fire** — small change, big leverage on existing automations.
7. [ ] **Background tasks panel** — closes the async loop server-side wiring already enables.
8. [ ] **Editable memory timeline** — a future memory view, not a separate profile layer.

## UX patterns from top-tier agentic apps

Cross-app themes that show up in 3+ polished agentic desktops (Claude / Codex / Cursor / Zed / Cody / Roo / Granola / Pieces / Warp / Linear / Raycast).

### The bar (what serious agentic UI looks like)
- **Per-tool approval policy with patterns** — not a global yolo toggle. `bash:rm *` always-confirm, `read_file *` always-allow.
- **Edit-any-user-message → branch** as a click-on-card affordance, not a hidden menu.
- **Context chips above the composer** showing every file/fact/skill/repo the next turn will load, removable inline. Token meter is the backup, chips are primary.
- **Inline diffs with selective staging** — per-hunk accept/revert. Side-by-side beats inline for multi-line.
- **Streaming status inside the producing surface** — the composer shimmers, the spawning card shows the step counter. Skeletons are out.
- **Provenance links on every AI artifact** — click a fact, jump to the chat turn it was extracted from.
- **Modes live on the composer**, not in settings — Chat / Agent / Plan / Ask pill above the input gates tool exposure.
- **Sidebar = grouped + pinned + searchable** — recency-sorted flat lists are the floor, not the ceiling.
- **Universal Cmd+K palette** that subsumes settings — search + nav + actions in one ranked list.
- **Three-tier approval scope** — "Once / This session / Always (with pattern preview)" with the narrowest as the default keystroke.

### Top 12 UX moves worth stealing for arden

1. [ ] **Context chips above the composer** — fact/observation/skill/file the next turn will load, removable inline. Turns memory from backend feature into tangible surface.
2. [ ] **Per-tool approval policy with patterns** (`bash:rm *` always-confirm, `read_file *` always-allow). Layered on the existing tool inspector.
3. [ ] **Mode pill on the composer** (Chat / Agent / Plan / Ask) — gates tool exposure, sets expectations, cuts token cost in Ask mode.
4. [ ] **Edit-any-message → branch** as click-on-card, not a hidden menu. We have the plumbing; this is purely surface.
5. [ ] **Streaming status inside the producing card** (not a global spinner). For sub-agents, show step counter + current tool on the spawning card.
6. [ ] **Per-hunk diff staging in tool inspector** for file-write tools. Side-by-side > inline.
7. [ ] **Provenance links on every memory item** — click fact → jump to chat turn it was extracted from. Trust layer for the memory system.
8. [~] **Universal Cmd+K** including session search, settings search, automation triggers, MCP toggle, "create memory from selection." One palette over five. _v1: sessions + actions (new/compact/archive/branch) + open targets (Memory/Automations/Archive/Settings). Skills/MCP/per-tab settings deep-links not yet wired._
9. [ ] **Pinned + grouped sidebar** — pinned sessions, grouped by project/tag, archive at bottom.
10. [ ] **Three-tier approval scope** on first tool prompt: Once / This session / Always (pattern preview).
11. [ ] **Workstream-style activity rollup** (Pieces) — "what happened today" surface, separate from chat.
12. [~] **Composer shimmer during stream** instead of a separate "thinking" row — quieter, signals attention on the input. _Shipped: composer pulses with a soft accent halo while waiting for the first token; standalone `ThinkingIndicator` deleted._

### Specific patterns worth nicking from a single app
- **Codex**: clicking a file-name strip collapses/expands the diff (header doubles as toggle); `/review` produces line-anchored inline comments; "approve once" vs "approve for this session" with narrowest default.
- **Cursor**: Plan-mode plans render as live editable Markdown files mid-stream; "scroll to bottom" pill appears only when the agent stream overflows below the fold.
- **Zed**: tool-call streams with `PermissionSelection` chip embedded in the same card (no modal); threads sidebar grouped by project with stop/archive/new as inline icon actions on hover.
- **Granola**: AI-generated text in gray *underneath* user-typed black text — visual class system instead of chat bubbles; every AI bullet hyperlinks to the transcript timestamp.
- **Cody**: first chat message pin-stays at top of panel after sending (anchor for context); auto-attached chips for current repo + selection.
- **Warp**: Active AI raises its hand on detected scenarios (failing test, merge conflict) — assistant initiates, not just responds.
- **Linear**: keyboard chords (`G then I` = Go to Issues), discoverable via the palette listing the chord next to each command.
- **Raycast**: every list row has a right-side action menu (`Cmd+K` opens a sub-palette of actions for the focused item).

## Already shipped (reference)

- Sessions: list, search, archive, restore, permanent delete, right-click menu, manual compact.
- Compaction indicator (server emits start/finish; client shows spinner + "compacted N → M" toast).
- Automations: list (Active/Templates tabs), prompt-first editor, run, view-last-run, scheduling chip.
- Memory: Search, Facts, Patterns, with Sent/Cleanup/Audit tucked under Advanced.
- Activity trace: per-parent rolling sub-tail; agent-kind tools render as agent cards instead of raw args.
- Tool inspector: agent variant with task / markdown result / recursive Activity tree.
- Edit + branch flows: stable `client_id` end-to-end (server stamps user messages on submit; provider preprocessor strips before API call).
- PageModal shell + lib/{hooks,format,agent} de-duped from across modals; MemoryModal split into focused `memory/*Pane.tsx` files.
- Cross-session streaming awareness (Letta-shaped):
  - Server checkpoints `run.messages` to SQLite via `SessionStore.update_progress` after every agent step (hook: `agent.hooks.on_step_finish` in `services/chat.py`). The DB is the source of truth for in-flight conversation state — no event-replay buffer.
  - Client gates SSE subscription on `historyLoadedFor === currentSessionId` so `setHistory()` can't race the first live deltas after a session switch.
  - `useActiveRuns` polls `/chat/runs/status` every 2s; sidebar rows render a breathing inset border while their session has an active run.
  - When a run finishes while the user is on another session, the row gets an "unread done" glow dot in place of the timestamp. Cleared the moment the user opens the session.
  - Right for single local / single cloud-VPS deploy. Multi-instance autoscaling would need Redis pub/sub for the bus; surviving server restarts mid-run would need a re-entrant agent checkpointer (LangGraph-style). Both deferred.
