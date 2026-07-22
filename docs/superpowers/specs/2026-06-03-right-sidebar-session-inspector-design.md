# Right Sidebar → Session Inspector — Design

**Date:** 2026-06-03
**Status:** Design approved (pending spec review)
**Scope:** Move SSE connection diagnostics out of the chat header into the right
sidebar, and add a read-only Tools/MCP connection-status section. Read-only only;
model/context/cost and memory inspectors are explicitly deferred.

Backed by `right-sidebar-research` workflow (6-dimension web + codebase research).

---

## v2 PIVOT (2026-06-04) — Tools list killed, Context cockpit added

A second research pass (`agent-sidebar-research`) on what Cursor/Cline/Roo,
Claude Code/Codex, Devin/Windsurf/Aider actually surface concluded the MCP
**Tools list is config, not state** — every tool that shows MCP keeps it
collapsed/in-settings. The single element every agent tool converges on is a
**context-window usage meter**. Shipped changes:

- **KILLED** the MCP `Tools` section (`McpStatusSection`, `useMcpServers`,
  `mcpDotClass`/`mcpTrailing`, `MCP_POLL_MS`, the `listMCPServersApi`/`MCPServer`
  imports).
- **ADDED `SessionSection` (lead, "Context")** — a fill bar of
  `usage.lastPrompt / serverConfig.compaction_token_limit` (so % reads as
  "% until auto-compact"), 3-zone color (`<70%` ink-soft, `70–89%` warn,
  `≥90%` bad), `compact at {limit}` annotation, a `{in} in · {total} total · {cost}`
  line (cost hidden < $0.01), and the model chip. Data is the same store `usage`
  slice that drives `BudgetDial` (kept untouched in the composer).
- **ADDED `ApprovalsRow`** — the "needs you" signal: an amber `N awaiting approval`
  row that opens the review modal via `setReviewingApproval`.
- Panel title renamed `Active` → `Session`; sections now flow with uniform
  `space-y-3`; empty-state gated on `!visible && !hasUsage && no approvals`.
- **Left inert (flagged):** the `openSettings(origin, tab)` deep-link infra
  (store `settingsTab`, SettingsModal effect) — added for MCP sign-in, now
  unused but a reusable capability. Revert if undesired.

### v2.1 (2026-06-04) — connection footer + context both removed
- **KILLED the connection footer** (`TransportStatusLine` + helpers + `STALE_MS`/age
  constants + the collapsed bad-dot badge). The "healthy-hidden" stale gate fired on
  any *quiet* (idle-but-connected) stream, so it showed on essentially every session —
  noise, not signal. Connection state already lives in the composer (`s.connected`).
- **KILLED the context bar** (`SessionSection`) — redundant with the composer's
  always-visible `BudgetDial`.

**Net result:** the sidebar is now strictly the live execution trace —
**approvals (needs-you) + plan/todos + background agents + automations**. Collapsed
by default, badges when there's active work. No context (composer), no tools
(settings), no connection readout (composer). Direction beyond this is an open
product question (the user gave no preference).

The sections below describe the superseded v1 (MCP-centric) design.

---

## 1. Goal

Turn the existing right panel (`AgentRightSidebar.tsx`) from an "Active" panel into
a lightweight **session inspector**, without adding chrome that competes with the
chat or creates alert fatigue. Two additions:

1. **Tools/MCP status section** — read-only liveness of external MCP servers for the
   current session, silent when everything's healthy.
2. **Connection footer** — derived SSE stream health, shown only when degraded.

Remove the raw `connected · seq … · keepalive …` diagnostics from the chat header.

---

## 2. Decisions (locked)

| Question | Decision |
|---|---|
| Connection footer default | **Healthy-hidden** — absent while live+fresh; appears only on stale/reconnecting/error |
| Panel auto-open | **Never** — badge-only; the panel only opens on the user's manual toggle |
| Tools section scope | **MCP / external only** — no built-in tools listed |
| MCP data source | **Local fetch + poll while panel open** (option A); not lifted to the store |
| Collapsed badge signal | Reflects **SSE connection health** (in store, cheap) + existing active-work pulse. **Not** MCP health (would need always-on polling) |
| New design tokens | **None** — reuse existing classes, colors, and motion tokens verbatim |

---

## 3. Components & changes

### 3.1 `Chat.tsx` — remove diagnostics from header
- Delete the diagnostics `<span>` (`Chat.tsx:78–85`) and the `diagnostics` /
  `formattedDiagnostics` derivation (`:34`, `:36–40`) and the now-unused
  `formatTransportDiagnostics` import.
- **Keep** the `channel` badge and `from {origin}` label — only the diagnostics move.

### 3.2 `AgentRightSidebar.tsx` — new footer: `TransportStatusLine`
A `shrink-0` sibling rendered **after** the `scroll-thin` scroll area, inside
`motion.aside`, separated by a single `border-t border-line-soft` hairline.

- **Data:** `s.transportDiagnostics[currentSessionId]`; reuse
  `formatTransportDiagnostics` (`lib/transportDiagnostics`) for the hover detail.
- **Derived signals** (never raw):
  - **phase** → dot color via `StatusDot` convention: `connected → ok`,
    `connecting/reconnecting → accent`, `disconnected/failed → bad`,
    `idle/unknown → faint`. Static dot (no pulse — pulse is reserved for `running`).
  - **last-event age** = `now − updatedAt`, relative ("3s ago"); amber `>30s`, red `>2m`.
  - **seq** = `lastSeq`, dimmed (`text-faint`) as a liveness/replay proof.
- **Text style:** `text-2xs font-mono text-faint truncate` (mirrors `Chat.tsx:83`).
- **Visibility (healthy-hidden):** render the footer only when
  `phase !== "connected"` **or** `age > STALE_MS` (15s). When connected+fresh → not rendered.
- **Hover/click:** small popover (or native `title` for v1) carrying the full
  `formatTransportDiagnostics().title` (phase, seq, keepalive, after_seq, last close,
  last error). Raw keepalive/transport lives only here.

### 3.3 `AgentRightSidebar.tsx` — new section: `McpStatusSection`
Rendered in the scroll area, after the Active sections, with `mt-3` when preceded.

- **Data hook `useMcpServers`** (local to the sidebar):
  - Fetch `listMCPServersApi(s.config)` on first open and on a ~30s interval **while
    the panel is open** (`!collapsed`); clear interval on close/unmount.
  - Filter to `enabled` servers only.
  - Hold `{ servers, error }` in local state; no store slice.
- **Section header:** local `2xs` SectionHeader style (`AgentRightSidebar.tsx:324–335`):
  `text-2xs font-medium uppercase tracking-[0.08em] text-muted`. Label is bare `Tools`
  when all-healthy; gains a count/color **only on degradation** (`Tools · 1 error`,
  red). Header is a button → `openSettings()` (deep-link to the MCP tab if trivial,
  else default tab).
- **Per-server row** (`py-1`, dot + name + trailing state):
  - dot: `connected → bg-ok` · `error → bg-bad` · `disabled/disconnected → bg-faint` ·
    `connecting → bg-accent` (matches `ServerRow.tsx:47`).
  - trailing: `{tool_count} tools` (connected) · `error` · `disconnected`.
  - `auth === "oauth" && !connected` → render **"Sign in →"** as an actionable link
    that calls `openSettings()` (auth flow lives in settings), not a bare dot.
  - tool names sub-line optional (`pl-[14px] text-xs text-faint`, max 3 then `+N`).
- **Empty/visibility:** section renders only when ≥1 enabled MCP server exists. If the
  fetch errors, show a single muted "couldn't reach server" row (no crash).

### 3.4 `AgentRightSidebar.tsx` — collapsed persistence + connection badge
- Replace `useState(true)` for `collapsed` with a `localStorage`-backed init:
  key `arden:right-panel:collapsed`, default `true`; write on every toggle.
- Collapsed toggle badge (`AgentRightSidebar.tsx:392–408`): in addition to the
  existing active-work pulse + count, show a **static `bg-bad` dot** when the current
  session's SSE phase is `disconnected`/`failed`/`reconnecting`. This is the only
  ambient connection signal while collapsed.

---

## 4. Animation & transitions (reuse existing tokens)

- **Panel slide:** unchanged — `x`-transform, `EASE_EMPHASIZED`, `MOTION.route` (0.36s).
- **MCP section / rows mount:** `AnimatePresence initial={false}`; enter
  `opacity 0→1` (`MOTION.row`) + `y -6→0` via `SPRING_ROW_ENTRY`; sibling reflow via
  `layout` + `SPRING_LAYOUT`.
- **Footer appear:** `opacity` only (`MOTION.panel`). No `y` (it's anchored; a slide
  fights its position).
- **Connection dot color change:** CSS `transition: background-color 120ms`. At most a
  single `scale [1,1.4,1]` (~180ms) on first connect. Nothing more.
- **Do NOT animate:** ticking values (seq, age), the header count, the toggle icon
  swap, dot pulse on static connection states, per-row exit on collapse.
- **Reduced motion:** rely on the app-root `<MotionConfig reducedMotion="user">`; add a
  `@media (prefers-reduced-motion: reduce){ .status-dot-breathe{ animation:none } }`
  guard for the CSS keyframe.

---

## 5. Consistency checklist (from codebase research)

- Shell: `surface-panel surface-radius-md` (existing) — no new container.
- Section header: the **local** `2xs` variant, not `components/SectionHeader.tsx`.
- Card block (if grouping rows): `rounded-[8px] border border-line-soft
  bg-surface-soft/45 px-2.5 py-2` (matches `TodoSidebarSection`).
- Status dot: `StatusDot` component; size `w-1.5 h-1.5`.
- Text color hierarchy: `text-ink → ink-soft → muted → faint → whisper`.
- Footer mono text: `text-2xs font-mono text-faint`.

---

## 6. Out of scope (deferred / skipped)

- **Deferred:** model selector, per-thread token/cost, context-window meter, memory/lens
  inspector — distinct surfaces with their own data plumbing; revisit after this lands.
- **Skipped:** raw seq/keepalive/transport inline; always-on healthy footer; per-tool
  toggles (Settings → ToolsTab owns those); auto-open on any event; built-in tools in
  the Tools section; aggregate summaries (too few servers).

---

## 6b. As-shipped refinements (UI polish pass)

- **MCP fetch lifted to `AgentRightSidebar`** (still local, not a store slice): the
  parent owns `useMcpServers(!collapsed)` and passes `servers`/`error` down to a now
  presentational `McpStatusSection`. This lets the parent compute `hasMcp`.
- **Empty-state gated on `!visible && !hasMcp`** — the "No active agents" block no
  longer floats orphaned between the Tools list and the footer; it shows only when the
  panel is genuinely empty.
- **MCP rows are single-line** — dot + name + `{n} tools` / `error` / `disconnected`
  (red trailing on error). The truncated tool-name sub-line was cut as low-value noise
  (mid-word truncation read as clutter). Names use `text-xs text-ink-soft`, counts
  `text-2xs`.

## 7. Risks / open implementation questions

- **`updatedAt` semantics:** confirm `transportDiagnostics.updatedAt` updates on each
  event/keepalive so "age" is a true liveness signal (verify in
  `store/chat-stream.ts` during implementation).
- **Settings deep-link:** confirm whether `SettingsModal` accepts an initial tab; if
  not trivial, header click falls back to `openSettings()` at the default tab.
- **MCP fetch when server down:** if `s.config` server is unreachable, the fetch fails;
  render the muted error row rather than an empty section.
