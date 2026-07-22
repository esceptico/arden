# ntrp Home surface — grounded facts (as shipped, main @ 5905ade5)

## Routing (`apps/desktop/src/app/App.tsx`)
- Home = **no session selected**: `const showHome = useStore((s) => s.currentSessionId === null)` (App.tsx:92).
- Branch (App.tsx:243–269): `openAreaKey || showHome` → a full-screen `<main>` flush between the sidebars, containing `<AnimatePresence>` with `openAreaKey ? <AreaRoom key={openAreaKey}/> : <Home key="home"/>` (dissolve/rise crossfade). Otherwise `<Chat/>`.
- Always-mounted chrome around it: left `Sidebar` (272px floating panel) + `SidebarToggle`, `AgentRightSidebar` (agent hub), `MemorySurface` full-window takeover, `SettingsModal`/`AutomationsModal`/`ToolViewer`, `CommandPalette`, `MarkdownViewer`, `ApprovalReviewModal`, `Toaster`.
- `goToNewSessionHome()` (actions/sessions.ts:24) = `setCurrentSession(null)` + `openArea(null)`.

## Home top-to-bottom (`features/home/components/Home.tsx`)
One centered, vertically-centered **640px column**; the page never scrolls as a whole. Order:
1. **Date line** — `new Date().toLocaleDateString(undefined, {weekday:"long", month:"long", day:"numeric"})`, 2xs uppercase faint.
2. **`HeroInput`** — the composer promoted (h-14 rounded-xl card with `⌘K` kbd).
3. **Greeting h2** — `greeting(brief.needs_you.length)`: `"All clear."` / `"One thing needs you."` / `` `${n} things need you.` ``
4. **Agent watch line** (optional, xs faint) — from `automations` filtered to `task_id.startsWith("area:")`: `` `${n} agent(s) watching · ${running} running now` `` or `` `… · last swept ${rel} ago` `` or bare `"N agents watching"`.
5. **Focus scroller** (`max-h-[48vh]`, internal scroll, ScrollFade edges) — **`WorkBrief`**.
6. **`AreasStrip`** (pinned below).

Disconnected state replaces everything: Sparkles glyph, `"Connect to get started"`, `"Open settings to point ntrp at your server."`, secondary Button `"Open settings"` → `openSettings(origin, "connection")`.

### Data plumbing
- `useAreasData()` (features/home/hooks/useAreasData.ts) reads `s.areas.overview` / `s.areas.loading`; fetches `GET /areas/overview` whenever `connected` flips true. Live refetch on `areas_changed` / `automation_finished` SSE lives in `useAutomationEvents`. Action layer: `src/actions/areas.ts`; API layer: `src/api/areas.ts`; store slice: `src/stores/areas-domain.ts` (`openAreaKey`, `overview`, `detailByKey`, `recordsById`).
- `automations`, `sessions`, `skills`, `draft/setDraft`, `connected` from the zustand store (`@/stores`).

## WorkBrief (`features/home/components/WorkBrief.tsx`) — section titles are exact strings
Order ("chief-of-staff order" per comment):
1. **`"Needs you"`** → one `FocusRow` per `brief.needs_you` ask (AreaAsk).
2. **`"Agents on it"`** → `BriefRow` per `brief.in_progress` item.
3. **`"Done for you"`** → `BriefRow` with `done` (accent Check icon; others get `CircleDashed`).
4. Footer whisper: `"That's it for today."` (always rendered).

- `BriefRow`: eyebrow = `item.area_title` + optional `item.outcome_title`, body = `item.text` (2-line clamp); click → `openArea(item.area_id)`.
- `FocusRow` (features/home/components/FocusRow.tsx): eyebrow = area title · kind tag · right-aligned relative age (`formatRelativePast(ask.created_at)`); body = `ask.text` 2-line clamp; **whole row** opens the area — no per-row action buttons (deliberate; actions live on AskCard in the room).
- Kind vocabulary (`src/lib/areaKind.ts`, shared with AskCard): `notify → "fyi"`, `question → "question"`, `review → "review"`. Uppercase text tags — **no dots anywhere** (dots banned app-wide; "live" is expressed via opacity, status via words).

## AreasStrip (`features/home/components/AreasStrip.tsx`)
- Header `"Areas"` (2xs uppercase). Every area = tonal rounded-full chip of its `title`; **non-live chips at `opacity-50`** (`!area.live`), live ones full-contrast. Click → `openArea(area.key)`.
- **Suggested areas** (daily suggester over unpromoted topic pages) = dashed ghost chips at the end: Plus + title (click → `promoteSuggestedArea(title, page_path)` = `POST /areas`), X (→ `POST /areas/suggestions/{key}/dismiss`), `title` attr carries `s.rationale`.
- Hidden entirely when both lists are empty.

## HeroInput (`features/home/components/HeroInput.tsx` + `lib/heroRouting.ts`)
- Placeholder: `"Ask, search, or start a chat…"`; autofocus; `⌘K` kbd hint.
- Suggestion kinds `"chat" | "area" | "session" | "automation" | "skill"`, labels `{chat:"Chat", area:"Ask in", session:"Session", automation:"Automation", skill:"Skill"}`; max 6, chat row always first (Enter with no match just sends the raw text). Area matching also fires when the query *mentions* a ≥4-char area word.
- Apply: chat→`sendMessage`; area→`openArea` if the query is exactly the area name, else `createSession(areaKey)`+`sendMessage`; session→`switchSession`; automation→`runAutomation`; skill→prefill `/name `.
- Popover footer key hints: `↑↓ navigate`, `↵ {kind}`, `esc dismiss`.

## AreaRoom top-to-bottom (`features/areas/components/AreaRoom.tsx`)
Fixed-viewport 640px column, only the middle scrolls. Fetches `GET /areas/{key}` on mount.
1. `"← Home"` back link.
2. **Title h1 + capability pill**: `page_path===null` → `"Create page"` (FilePlus2); `autonomy===null` → `"Delegate"` (Bot); `"observe"` → `"Observing"` (Eye, click grants act); `"act"` → `"Acting"` (Zap, accent, click revokes). Plus `AreaSettingsButton` gear when autonomy ≠ null.
3. **`AgentStatusLine`** (AreaControls.tsx) exact strings: `"Paused — the agent isn't watching this area."` / `"Custodian unavailable — no check is scheduled."` / `` `Last check failed — ${err}` `` / `"Checking now…"` / `` `Checked ${x} ago · next ${y}` `` + whisper `` ` — ${next_check_reason}` ``.
4. **`AgentPresence`** — the standing agent (automation keyed `area:{key}`): status `` `Working… ${m:ss}` `` (live tick) / `` `Agent · swept ${rel}` `` / `"Agent · hasn't run yet"`; `"Run now"` button; second line = last-run summary (kebab-slug run-ids filtered out). Clicking the line opens the agent's channel session.
5. **`AskCard` per active ask** (up to 3 nominated per run). Card: kind tag eyebrow + Dismiss X; text splits `"title — detail"` on first em-dash; optional `"Why now:"` / `"Next:"` lines; buttons by kind — review: `"Approve"`/`"Reject"`; question: `"Reply"`; notify: `"Got it"`; plus verb-derived ghost action (`src/lib/askActions.ts`: `open_session→"Open"`, `retry→"Retry"`, `open_page→"Review"`), `"Discuss"`, and for `source==="agent"`: `"Fewer like this"` (appends `- Fewer asks like: "…"` to area instructions, dismisses the ask).
6. **Scroller**: empty copy (`"Nothing needs attention yet — start a conversation below or delegate the Area."` / `"This Area has conversations but no shared page yet. …"`) → **`AreaWork`** → **`OpenLoops`** → **`AreaActivity`** → `"Related"` chips (`openArea` cross-links).
7. **Pinned composer** — placeholder `` `Message in ${title}…` `` or `"Reply to the Custodian…"`; first send does `createSession(areaKey)` + `sendMessage`.

- `AreaWork`: header `"Work"` + `"Add outcome"` (form placeholders `"Outcome"` / `"What does done look like?"`); primary outcome row with `` `Done when ${success_criteria}` ``; work rows labeled `"Now"` (current) and `"Needs you"` (blockers, `text-warn`); overflow in `<details>` `"N more"`; empties: `"No active outcome."` / `"Give the Custodian an outcome to own."`
- `OpenLoops`: header `"Open loops"`, plain strings, cap 7 + `"Show all N"`, expand-in-place + `"Discuss"` (prefills composer `About the open loop "…" — `).
- `AreaActivity`: header `"Activity"`, user sessions only (agent channel session excluded), `"Untitled session"` fallback, right-aligned `"{rel} ago"`.
- Settings popover (`AreaSettingsButton`): `"Attention"` = Active/Ambient/Dormant; `"Notify me about"` = `"Questions & reviews"`/`"Everything"`/`"Never"`; `"Paused"` switch; footer `` `${runs_today} of ${runs_cap} runs today` ``.

## Data shapes & enums (`src/api/areas.ts`)
- `AreaSummary { key, title, autonomy: "observe"|"act"|null, page_path, live: boolean, updated, ask_count }`
- `AreaAsk { id, area_key, text, kind: "notify"|"question"|"review", source, actions:[{verb,ref}], state, created_at, snoozed_until, provenance?, why_now?, what_next?, expires_at?, stable_key?, resolution?, resolved_at?, area_title? }`
- `AreaAttention "dormant"|"ambient"|"active"`; `AreaInterrupts "asks"|"all"|"none"`
- `AreaAgentStatus { last_checked, next_check, next_check_reason, last_report, woken_by[], runs_today, runs_cap, running_since, availability: "ready"|"unavailable"|"error", last_error }`
- `AreaOutcomeStatus "active"|"paused"|"completed"|"cancelled"`; `AreaWorkStatus` adds `"in_progress"`; `AreaWorkItem { kind: "loop"|"action"|"blocker", owner: "custodian"|"user"|"external", due_at, next_attempt_at, … }`
- `AreasOverview { areas, focus: AreaAsk[], suggested?: AreaSuggestion[], brief: { done, in_progress, needs_you } }`; `AreaBriefItem { area_id, area_title, stable_key, text, type?: "outcome"|"work", status?, owner?, outcome_title?, … }`
- `AreaSuggestion { id, key, title, page_path, rationale, created_at }`

## API endpoints (client-called)
`GET /areas/overview` · `GET /areas/{key}` · `POST /areas` (`{name, page_path}`) · `POST/PATCH /areas/{key}/outcomes[/{key}]` · `PATCH /areas/{key}/work/{key}` · `POST /areas/{key}/asks/{id}/resolve` (`{state, snoozed_until?, resolution?}`) · `POST /areas/{key}/asks/{id}/reply` · `PUT /areas/{key}/autonomy` · `PATCH /areas/{key}` (attention/interrupts/paused/instructions) · `POST|DELETE /areas/{key}/page` · `POST /areas/suggestions/{key}/dismiss`.

## Server composition (grounding for what the numbers mean)
- `apps/server/ntrp/areas/service.py overview()`: `focus = nominate_focus(asks, cap=4)` — **one best ask per area**, ranked kind-priority then recency; `brief.needs_you` = those focus asks + `area_title`. `live` = area has any active ask OR its automation is running.
- `apps/server/ntrp/areas/work_store.py brief()`: `done` = `completed` work events **with a run_ref** (i.e. agent receipts) in the last **72h**, limit 6, `text` = event summary; `in_progress` = top-ranked active work item **per area** (in_progress first, custodian-owned first), limit 6.
- `suggested` merged in `apps/server/ntrp/server/routers/areas.py:69` from the area-suggestions store (LLM suggester over unattached topic pages).

## Existing analogues of "suggested focus / needs you / running"
- **"Needs you"** exists verbatim, twice: WorkBrief section title (Home) and the blocker row label in AreaWork (room).
- **"Running"** vocabulary: Home watch line `"· N running now"`; AgentPresence `"Working… m:ss"`; AgentStatusLine `"Checking now…"`; live chips (opacity contrast, no dot); `Automation.last_status` enum `"completed" | "failed" | "running"`.
- **"Suggested focus"**: the focus set itself is already server-nominated (`nominate_focus`, cap 4, one ask per area) — Home doesn't rank beyond that; "suggested" as a word only applies to **suggested areas** (ghost chips with a `rationale` tooltip). There is no separate "suggested focus" section; the greeting + Needs-you list is that role today.# Asks & Approvals in ntrp — ground truth

## 1. The Ask data model (server: `apps/server/ntrp/areas/models.py`)

`Ask` dataclass — exact fields:

| field | type | notes |
|---|---|---|
| `id` | str | deterministic: `approval:{run_id}:{tool_call_id}`, `runfail:{task_id}:{run_id}`, `agent:{area_key}:{stable_key}` |
| `area_key` | str | the area's id |
| `text` | str | **single string, no separate title/question fields.** UI splits on first `" — "` into title/detail (`splitAsk` in AskCard.tsx) |
| `kind` | `"notify" \| "question" \| "review"` | notify = "FYI, no decision (Home only, expires quietly)"; question = "the agent is blocked on the user's judgment (push)"; review = "a proposed action awaiting approve/edit/reject (push)" |
| `source` | str | comment lists `"approval" \| "run_failed" \| "agent_output" \| "open_loop" \| "agent"` — **only `approval`, `run_failed`, `agent` have producers in code**; `agent_output`/`open_loop` do not exist as producers |
| `actions` | `list[dict]` | `[{"verb": ..., "ref": ...}]`; real verbs: `open_session`, `retry` (ref = automation NAME), `open_page` |
| `state` | `"active" \| "done" \| "dismissed" \| "snoozed"` | |
| `created_at` | ISO str | |
| `snoozed_until` | str \| None | |
| `provenance` | str \| None | e.g. `run:{run_id}`, `automation-run:{id}` |
| `why_now` / `what_next` | str \| None | "the concrete why-now and what-happens-next make an ask answerable instead of rubber-stampable" |
| `expires_at` | str \| None | notify asks only: created+72h (`NOTIFY_ASK_TTL_HOURS = 72`); expiry = quiet dismissal on next `list()` |
| `stable_key` | str \| None | agent asks; reused across runs "until that exact decision is resolved" |
| `resolution` | str \| None | `"approved"`, `"rejected"`, `"acknowledged"`, `"dismissed"`, `"replied"` (client-chosen strings) |
| `resolved_at` | str \| None | |

There are **no options arrays, no consequence field, no severity number stored** — salience (1–5) exists only in the nomination draft and is filtered out before storage.

Legacy fold on load (`areas/asks.py`): `{"review": "notify", "decide": "question", "act": "question", "drift": "notify"}` (pre-taxonomy kinds).

## 2. The three producers (`areas/service.py refresh_mechanical`, `areas/agent.py record_area_run`)

1. **`source="approval"`** — pending tool approvals of live runs whose session belongs to an area. `text = f"{tool_name} wants: {preview or tool_name}"`, `kind="review"`, action `open_session`. Reconciled (`AskStore.reconcile("approval", …)`): the ask auto-resolves to `done` when the underlying approval disappears; a resolved ask stays resolved while the condition persists. Approvals are gathered in `server/app.py`: scan live runs → `store.list_pending_tool_approvals(session_id)`.
2. **`source="run_failed"`** — an area automation whose `latest_run.status == "failed"`. `text = f"{auto['name']} failed — {error}"`, `kind="notify"`, action `retry` (ref = automation name).
3. **`source="agent"`** — Custodian post-run nominations via structured output (`AreaCustodianReport` extends `AreaAskNomination`: `asks` (max 3), `report`, `next_check_hours`, `next_check_reason`, plus outcome/work/evidence changes). Draft fields: `key` (slug, ≤80), `text`, `kind`, `salience` (1–5), `why_now`, `what_next`. **`SALIENCE_THRESHOLD = 3`** — below it the finding "stays on the page and never becomes an ask". `MAX_ASKS_PER_RUN = 3`. Action `open_page`. Dedup (`upsert_agent_nomination`): a user decision is durable (re-nominations stay silent); quiet TTL expiry re-surfaces as new. Silence retires previous nominations (every run re-decides).

## 3. Ranking / priority — exists

`areas/asks.py`: `_KIND_PRIORITY = {"question": 0, "review": 1, "notify": 2}`. `nominate_focus(asks, cap=4)`: best ask **per area** (kind priority wins, tie → newer), then priority asc / created_at desc, **capped at 4**. This is Home's `focus` == `brief.needs_you`. Room lists all active asks sorted `created_at` desc.

## 4. API endpoints (`server/routers/areas.py`)

- `GET /areas/overview` → `{areas: [{key,title,autonomy,page_path,live,updated,ask_count}], focus: Ask[]+area_title, brief: {done, in_progress, needs_you}}` (needs_you = focus rows)
- `GET /areas/{key}` → detail incl. `asks` (all active for that area)
- `POST /areas/{area_id}/asks/{ask_id}/resolve` body `{state, snoozed_until?, resolution?}` — then emits `areas_changed` and wakes the custodian: `request_area_wake(area_id, f"user resolved ask '{text[:80]}' as {state}")`
- `POST /areas/{area_id}/asks/{ask_id}/reply` body `{message}` — dispatches `f"REPLY TO ASK [{ask.id}]\n{message}"` into the custodian's channel session (`skip_approvals=False`, custodian tool_scope), then resolves the ask `done`/`"replied"`. 409 `"Custodian channel unavailable"` without a thread.

**Snooze**: fully modeled server-side (snoozed asks re-surface when `snoozed_until <= now`; resolve endpoint accepts it) but **no UI calls it — there is no snooze button anywhere**.

## 5. How the user acts (desktop)

**`features/areas/components/AskCard.tsx`** (room card) — kind-typed affordances, quoted labels:
- eyebrow = `ASK_KIND` label (`lib/areaKind.ts`): notify→`"fyi"`, question→`"question"`, review→`"review"` — uppercase text label, deliberately no dot; `X` dismiss at far end
- body: title / detail (em-dash split), then `"Why now: …"` and `"Next: …"` lines
- **review**: `"Approve"` (done/"approved") / `"Reject"` (dismissed/"rejected") / `"Discuss"`
- **question**: `"Reply"` → hands ask to the room composer (`setReplyingTo`, placeholder becomes `"Reply to the Custodian…"`)
- **notify**: `"Got it"` (done/"acknowledged") / `"Discuss"`
- secondary button from first action verb (`lib/askActions.ts primaryActionFor`): `open_session`→`"Open"`, `retry`→`"Retry"` (name→task_id via live automations; null if stale), `open_page`→`"Review"` (suppressed inside the ask's own room)
- `source === "agent"` only: `"Fewer like this"` — tooltip `"Dismisses this and adds a standing instruction so the agent raises fewer asks like it"`; appends `- Fewer asks like: "<text[:120]>"` to area instructions then dismisses (`actions/areas.ts fewerLikeThis`)

**Home** (`features/home/components/`): greeting = `"All clear."` / `"One thing needs you."` / `"N things need you."`; `WorkBrief` section order ("chief-of-staff order") = **`"Needs you"`** (FocusRow per ask) → `"Agents on it"` → `"Done for you"` → footer `"That's it for today."`. `FocusRow`: whole row opens the area; eyebrow = area title · kind label · relative age; ask text `line-clamp-2`. No per-row buttons by design.

**Consequence of note**: resolving an approval-sourced ask via its card only resolves the Ask record — the underlying tool-approval Future stays pending; the real approve happens in chat (the ask's `"Open"` action takes you there).

## 6. Approval gate for tool calls

Server (`tools/core/`): `ToolPolicy(requires_approval=True)` on bash, file writes, memory writes, automation create/edit, notify, directives, background. Middleware `request_approval` (`middleware.py`) → `tool.approval_info()` → `execution.request_approval(description, preview, diff)` (`context.py:713`):
- persists a row in SQLite `tool_approvals` (run_id, session_id, tool_call_id, tool_name, action, scope, preview, diff, status, requested_at, resolved_at, expires_at, result_feedback, kind='tool_approval', payload_json, resolution_json) — statuses seen in code: `pending / approved / rejected / cancelled / expired`
- emits SSE `ApprovalNeededEvent {tool_id, name, path (=description), diff, content_preview}` (`events/sse.py:392`)
- awaits a per-tool `asyncio.Future` in `pending_approvals` dict (`server/state.py:52`), timeout `approval_timeout_seconds = 300` → `"Approval timed out"`; no UI → `"No UI connected — cannot approve"`
- suspension pre-check lets a restarted run consume a resolution recorded while it was down
- bypasses: run-level `skip_approvals` (automation `auto_approve`), per-tool `auto_approve` set, registry override ASK forces blocking; `allow_approval_bypass` on policy

Client resolve: `actions/approvals.ts respondToApproval(toolId, approved, feedback)` → `submitToolResult({run_id, tool_id, result: feedback, approved})`; rejection feedback becomes "User rejected this action and said: …" guidance. `respondToAllApprovals` bulk-resolves.

**Where pending approvals surface (3 places)**:
1. **Chat**: `features/chat/components/ApprovalBanner.tsx` — card-deck stack above the composer (front card + one dimmed sliver, `"1 of N"`). Header `"Approve action"` (structured Key:value preview) or `"Approve {toolName}?"`. Buttons: `"Approve"` (⌘↩ from anywhere), `"Reject"`, deny-with-reason input (`"Why? — sent to the agent as guidance"`, `"Deny"`), bulk `"Approve all"` / `"Reject all"`, `"Review"` → `ApprovalReviewModal` for diffs/long bodies. Approve exits right, reject exits left. `ApprovalState` = `{toolId, toolName, path?, diff?, preview?, status: "pending"|"approved"|"rejected"}` (`stores/types.ts:256`).
2. **Agent sidebar**: `features/background-agents/components/ApprovalsRow.tsx` — amber row `"{count} awaiting approval"` + `"Review →"`; code comment calls it "The single load-bearing 'needs you' signal".
3. **Areas/Home**: as `source="approval"` review-kind asks (section 2.1).

## 7. Push + attention decay (`server/runtime/automation.py`)

`_notify_asks`: newly created agent asks push through notifiers gated by the area's `interrupts` policy — `"asks"`→{question,review}, `"all"`→all three, `"none"`→nothing. Subject `f"ntrp · {area.title}: {ask.kind}"`, body = text + Why now + Next. Comment: "the push IS the interrupt; Home holds the queue either way."
Agent asks unanswered for `AREA_ASK_IGNORED_DAYS = 7` step the area's attention down `active→ambient→dormant` ("asks unanswered").

Key files: `apps/server/ntrp/areas/{models,asks,service,agent}.py`, `apps/server/ntrp/server/routers/areas.py`, `apps/server/ntrp/server/runtime/automation.py`, `apps/server/ntrp/tools/core/{context,middleware}.py`, `apps/desktop/src/features/areas/components/{AskCard,AreaRoom}.tsx`, `apps/desktop/src/features/home/components/{Home,WorkBrief,FocusRow}.tsx`, `apps/desktop/src/features/chat/components/ApprovalBanner.tsx`, `apps/desktop/src/{api/areas.ts,actions/{areas,approvals}.ts,lib/{askActions,areaKind}.ts}`.# ntrp supervision substance — grounded research (repo: /Users/escept1co/src/ntrp)

## The three run primitives

1. **Background/child agents** — spawned via `ctx.spawn_fn` (core/spawner.py), tracked per-session, shown in the right-sidebar "Activity" hub.
2. **Workflows** — deterministic multi-agent runs via the `workflow` tool (`apps/server/ntrp/tools/workflow.py`) driving the `Orchestra` engine (`apps/server/ntrp/orchestra/engine.py`). Curated built-in presets only: `"audit"`, `"investigate"`, `"panel"`, `"implement"` (tool description); user-authored Python presets return `"User-authored Python workflow presets are disabled."`
3. **Automations** — scheduled/triggered agent runs (`apps/server/ntrp/automation/`), surfaced in the AutomationsModal (rail + detail) and, while running, in the sidebar hub.

## Status enums (exact values)

- `BackgroundAgentStatus` (`apps/desktop/src/stores/types.ts:136`): `"running" | "completed" | "failed" | "cancelled" | "interrupted" | "cancel_requested"`. `AgentRunStatus = BackgroundAgent["status"]` — same union. Active = `running | cancel_requested`.
- `WorkflowStatus` (`stores/workflow-domain.ts`): `"running" | "completed" | "failed" | "cancelled"`. `WorkflowPhaseStatus`: `"pending" | "running" | "completed" | "failed"`.
- Automation run rows (SQLite `automation_runs`, `automation/store.py`): `status TEXT NOT NULL DEFAULT 'running'` → settled to `'completed'` / `'failed'`. Detached runs stay `'running'` until the child's RunCompleted settles them (idempotent status guard).
- Automation object itself has **no status field** — state is bimodal: `running_since != null` → running; else `last_status` (`"completed" | "failed" | "running"`, derived server-side as `recent_statuses[0]`); `enabled=false` = paused. Client folds it via `resolveAutomationStatus()`: running → `"running"`, last failed → `"failed"`, last ok → `"completed"`, never-ran → `"interrupted"` (muted idle tone). Paused-ness deliberately NOT in status.
- Server session-level `WorkflowState` (`apps/server/ntrp/workflow/models.py`): `running, waiting_for_approval, waiting_for_input, waiting_for_auth, waiting_for_subagent, completed, failed, cancelled`.

## AgentRunView — the one view-model (`apps/desktop/src/lib/agentRun.ts`)

Fields: `key, name, type, status, elapsedLabel, childSessionId?, runId?, detached?, progress?, resultPreview?` + automation facets `enabled?, schedule?, nextRun?, recentStatuses?`.
- `type` via `humanizeAgentType`: `background_research/research → "Research"`, `sub_agent → "Agent"`, else snake→Title minus the word "agent".
- Meta line (`metaLine`): `[type, schedule, nextRun, detached ? "detached" : null].join(" · ")`.
- Third line: running → italic `progress`; settled → `resultPreview` (`resultSnippet`: first prose line, markdown chrome stripped, fences skipped, ≤140 chars + `…`).
- `formatElapsed`/`formatDuration`: `"45s" / "2m" / "3h"` (no suffix). Finished background agents show **no elapsed** (`createdAt` is client poll-time, would read "0s").
- Automation formatters: schedule `"every 2h 09:00–18:00 · weekdays"`, `"at 09:00 · weekdays"`, `"on:starts (15m)"`, `"idle 30m"`, `"every 5 turns"`; next-run: `"paused"` / `"due now"` / `"next in 2h"`; `formatRelative`: `"<1m" | "now" | "in 5m" | "5m ago" | "in 3h" | "3d ago"`.

## AgentRunRow (`apps/desktop/src/components/ui/AgentRunRow.tsx`)

Row anatomy in order: **leading glyph** (Bot icon, tone by status: completed `bg-ok-soft`, failed `bg-bad-soft`, cancelled/interrupted `bg-surface-soft text-faint`, else accent; hover swaps to a Stop square when running) → **name button** (opens child session) → **right cluster** = `StatusSparkline` (last 4 run outcomes as 1px pips, automations only) + `StatusDot` (pulses while running) + `elapsedLabel` (`text-2xs tabular-nums`); hover crossfades that cluster into an **affordance lane**: send-message composer (placeholder `"Message this agent…"`), per-instance actions, and for finished agents the handoffs `"Reply with result"` (CornerUpLeft), `"Pin to memory"` (Brain), `"Route to a new agent"` (Split). Below: badges row, meta line, progress/result line. Paused automation forces dot to `"interrupted"` tone and hides the sparkline.

## Right sidebar hub (`features/background-agents/components/AgentRightSidebar.tsx`)

Tabs: `"Activity"` / `"Sources"`. Activity panel order: **ParentBreadcrumb** (when inside a child session) → **ApprovalsRow** → **TodoSidebarSection** → section **"Agents"** (or `"Agents in this run"`; active first, then ≤6 recent terminal, `RECENT_AGENT_LIMIT = 6`) → section **"Workflows"** (`ExpandableWorkflowCard` list, dismissible ×) → section **"Automations"** (only `running_since != null`, non-internal, non-loop; `SidebarAutomationRow`) → empty state `"No agents yet.\nBackground agents you start appear here."`. Workflow leaf agents are excluded from the top-level roster (matched by `parentToolCallId`).

## Workflow progress (`WorkflowProgress.tsx` / `WorkflowDetail.tsx`)

`WorkflowProgressCard` line 1: workflow glyph (accent while running) + name + status **Badge** (`running`=accent, `completed`=ok, `failed`=bad, `cancelled`=neutral; cancelled renders the word `"stopped"`) + Stop button while running (kills the whole parent run via `stopRun()`) + chevron. Line 2: **segmented phase bar** — one 3px segment per phase (`completed`=bg-ok, `failed`=bg-bad, `running`=bg-accent + `phase-glare`, `pending`=bg-line), running phase's name floats above its segment; right-aligned meta string: `elapsed · Σ 45k · $0.42 · 3/5` (tokens via `formatTokens`: `812`, `1.4k`, `45k`, `1.1M`; cost via `formatCost`: `"<$0.01"`, `$0.42`, `$120`; `done/total` = settled/total agents). Ticks at 1s while running, 60s settled.

Expanded (`WorkflowDetail`): optional **summary line** (the "why" of a settled run — `"stopped by user"`, `"script did not compile"`, `"workflow execution failed"`, failure reason; red if failed) → phase groups (name + per-agent pip sparkline + count, default-open) → **agent rows**: 5px status pip (breathe halo while running) + name + tokens + elapsed; click opens the agent's live session. Empty states: `"Spinning up agents…"` / `"No agents ran."` / `"No agents yet."`. Plus a collapsible **"Source"** disclosure showing the workflow's Python (from the `workflow` tool-call args), syntax-highlighted, Copy/Copied button.

Per-agent live data (`WorkflowAgent`): `taskId, phase, name, agentType, childSessionId, status, detail, startedAt, completedAt, durationMs, tokens {prompt, completion, total, cache_read, cache_write}, cost, toolCount, lastTokenSeq` (seq dedupes replayed token events — spend accumulates).

## Server events feeding it (`apps/server/ntrp/events/sse.py`)

- `WORKFLOW_STARTED` (`workflow_started`): `session_id, run_id, workflow_id, parent_tool_call_id, name, description, phases: list[str]` — the **declared plan**, rendered as pending segments before any agent spawns.
- `TASK_STARTED` / `TASK_PROGRESS` / `TASK_FINISHED` (`task_started/progress/finished`): `task_id, parent_task_id, parent_tool_call_id, child_run_id, child_session_id, agent_type, wait, name, status, summary, depth, workflow_id, phase`; finished adds `tool_count`.
- `WORKFLOW_FINISHED`: `status, summary, agent_count`.
- `TOKEN_USAGE` (`token_usage`): `run_id, usage dict, cost, message_count, scope, task_id, child_run_id, workflow_id, phase` — this is where per-agent tokens/cost come from.
- `BACKGROUND_TASK` (`background_task`): `status` ∈ `"started" | "completed" | "failed" | "cancelled" | "activity"`, `detail`, `result_ref`, `terminal`. **Live progress per running agent** = the `"activity"` events (spawner.py:1035): `detail` = tool display name on ToolStarted, `"{name}: {preview}"` on ToolCompleted. No per-step counter, no elapsed, no tokens in this event.
- `AUTOMATION_PROGRESS` (`automation_progress`): `task_id, status` — strings are `"starting..."` (scheduler), then per tool call `"{label}..."` and `"{label}: {preview}"` (operator/runner.py). `AUTOMATION_FINISHED`: `task_id, result`.
- Orchestra hard limits: `_MAX_WORKFLOW_SPAWNS = 200`, global semaphore `AGENT_MAX_CONCURRENT`, shared `RunBudget` output-token ceiling (`WorkflowBudgetExceeded`), workers denied `workflow/research/background` tools.

## Automations — data model & endpoints

`Automation` (server dataclass ≈ client `api/types.ts`): `task_id, name, description, model (null = "session default"), triggers[], enabled, created_at, next_run_at, last_run_at, last_result (markdown), running_since, auto_approve, handler, builtin, cooldown_minutes, kind ("automation"|"loop"), read_history, tool_scope, output_schema` + serialized extras `last_status`, `recent_statuses` (newest-first). Trigger types: `"time" | "event" | "idle" | "count" | "message"` with fields `at/days/every/start/end`, `event_type/lead_minutes`, `idle_minutes`, `every_n/threshold/scope`, `channels/from_user/contains`.

`AutomationRun` (run ledger): `{ id: number, task_id, started_at, ended_at (null while running), status, result, error }`. Scheduler records start (`'running'`), then finish `status="completed" if success else "failed"` with `result`/`error`; `next_run_at` is re-advanced **before** the body runs.

Endpoints (`server/routers/automation.py`): `POST/GET /automations`, `GET /automations/{task_id}`, `GET /automations/{task_id}/runs?limit=` (default 20, max 200), `POST .../toggle`, `POST .../auto-approve`, `POST .../run`, `PATCH .../{task_id}`, `DELETE .../{task_id}`, `GET /automations/suggestions`, `POST /automations/suggestions/refresh`, `POST /automations/suggestions/{id}/dismiss`, SSE `GET /automations/events`. Agents: `GET /chat/background-tasks?session_id=`, `GET /chat/child-agents?session_id=`, `GET /chat/child-agents/{child_run_id}/result?wait=&timeout_seconds=`, `POST .../cancel`.

## AutomationDetail (`features/automations/components/AutomationDetail.tsx`) — sections in order

1. **Header**: name input (`"Untitled automation"` placeholder) + badges `"running"` (accent), `"channel"` (neutral + Radio icon), trust badge (`"read-only"` for handler `knowledge_health`, `"retention"` for `knowledge_retention`, `"learns context"` for `knowledge_reflection`; `"auto-approve"` suppressed here — the footer switch owns it; tone: knowledge_* neutral, auto_approve **bad**) + open-channel icon + countdown delete + close.
2. **The instrument**: label `"Runs — last 14 days"` / `"Fires — …"` (event-driven; `spanLabel`: `"last Nh"` / `"last N days"`), latest run readout `"{stamp} — completed|failed in {duration}"`, then **RunRuler**: horizontal time tape, runs as ink ticks (failed = red, always full opacity; ok fades with age), dotted baseline, `"now"` mark, next fire as faint tick labeled `nextLabel` (`"14:00"` / `"tmrw 09:00"` / `"Jul 22 09:00"`), future-zone text `"paused"` / `"waiting on next match"` / `"not scheduled"`; wheel pans with inertia; hover chip `"{stamp} · {dur} · ok|failed"`; click opens the run. Stats strip: `"success 27 / 30"` (or `"fires"` when event-driven), `"median 2m"`, `"next in 2h"` / `"waiting on next match"` / `"not scheduled"` / `"paused"`. Sub-second durations are treated as recorder artifacts → `"—"`.
3. **Prompt** textarea (`"What should the agent do when this automation fires?"`), templates for blank drafts, builtin note `"What this runs is code-owned; when it runs is yours — adjust the schedule, pause, or run now."`
4. **"Recent runs"** ledger (aux `"click to open"`, 5 rows): `runStamp` (`"today 14:03"` / `"Jul 18 09:00"`) + duration + `runSummary` (first prose line of `result`, skipping codename tokens; `"running…"`; `"Failed — {error ?? "no error recorded"}"`; `"Completed — no output."`). Opens result as markdown view, subtitle `"run · {stamp}"`.
5. Warning callout when message-trigger + auto-approve + no sender gate.
6. **Footer dials**: ScheduleChip (kinds `"at" | "every" | "event" | "message"`; events `"starts" | "ends" | "is approaching"`; days `"daily" | "weekdays" | "weekends"`), model picker (`"session default"` pseudo-entry = `model: null`), `"Auto-Approve"` switch, buttons `"Pause"/"Resume"`, `"Run now"` (disabled while running/paused), or draft `"Cancel"`/`"Create"`.

## AutomationRail (`AutomationRail.tsx`)

Groups in order: `"Yours"`, `"Area agents"` (task_id prefix `area:`), `"System"` (internal), `"Suggested"`. Row = name + right-aligned `whenLabel`: `"running"` (ink) | `"paused"` | `"on msg"` | `formatRelative(next_run_at)` e.g. `"in 2h"`. No leading glyphs/dots — state reads through text ink. `kind="loop"` is hidden from the desktop list.

## Notable absences (verified)

- No per-agent token/cost data outside workflows — plain background agents have no TOKEN_USAGE aggregation in the hub; tokens/cost exist only on `WorkflowAgent`.
- No "current step N of M" for background agents or automations — live progress is the last tool-activity string only.
- Automation runs have no openable session (`childSessionId` intentionally unset in `agentRunFromAutomation`); only a bound channel session is openable.
- Automations expose `next_run_at` + last-run summary + run ledger, but no median/success stats server-side — computed client-side in `statsFor`.
- No elapsed label on finished background agents (poll-time `createdAt` caveat, `agentRun.ts:243`).
