# UI/UX Intelligence

Last updated: 2026-05-07

## North Star

Arden should feel like a native personal AI operating layer, not a chat page with tools bolted on.

The target vibe is:

- Raycast-fast command entry
- Linear-clean density and information hierarchy
- Apple/Fluent-native material restraint
- Cursor/Claude/Codex-aware agent workbench
- ChatGPT Canvas-capable artifacts and provenance

Chat is one surface. The app should organize command, work, agents, memory, and library surfaces around the user.

## Product Shape

The shell should move toward a command-center layout:

```txt
native titlebar / command center / model / status
sidebar     | main workspace                 | inspector
sessions    | chat / canvas / artifact       | context
agents      | agent run / report / browser   | sources
memory      | document / task view           | tool calls
tasks       |                                 | plan / trace
automations |                                 | approvals
settings    |                                 |
```

The main point is separation of concerns:

- The main workspace is for conversation, artifacts, reports, previews, documents, and active work.
- The sidebar is for navigation, active runs, sessions, projects, tasks, and status.
- The inspector is for context, provenance, memory used, sources, approvals, tool details, and traces.

Avoid a giant single-column transcript as the whole product.

## Primary Surfaces

### Command

`Cmd/Ctrl+K` should become the soul of the app.

It should support:

- search memories
- open agent runs
- start research
- create automation
- run tool
- approve pending action
- open settings
- switch workspace/session/project
- search sources

Use nested command pages like Raycast, Linear, and Superhuman. Prefer one strong command surface over scattered AI buttons.

### Work

The work surface should support chat plus artifacts/canvas:

- chat
- generated reports
- diffs
- previews
- documents
- source views
- memory/context inspection

The app should support rich panes where they beat text. Chat coordinates work; it should not swallow every workflow.

### Agents

Agent runs should be first-class objects.

Every run should expose:

- plan
- tool calls
- sources
- memory used
- approvals
- artifacts
- operational timeline
- final answer

Do not expose private reasoning. Expose operational trace:

```txt
Understanding request
Searching web
  Read Apple Liquid Glass docs
  Read Claude Code redesign
Comparing patterns
Drafting recommendation
Complete
```

Agent states should be visible and filterable:

- running
- queued
- needs approval
- blocked
- error
- complete
- archived

### Memory

Memory is a trust surface, not only storage.

Prioritize:

- facts
- observations / patterns
- episodes
- entities
- stale / disputed state
- source provenance
- correction / archive actions

Use source-linked facts and patterns before adding graph views. Graphs/timelines can become noise unless the user has a concrete investigation task.

### Library

The library groups the app's capabilities:

- model providers
- data connectors
- MCP servers
- tools
- skills
- automations
- permissions

Connections should be scoped capabilities, not just OAuth badges. Show connected state, active use, read/write scope, approval requirements, last error, and last tested time where useful.

## Visual Direction

Use a dark-first, warm technical graphite base with native glass chrome.

Do:

- solid main work surfaces
- dimmer sidebar
- restrained borders instead of heavy shadows
- compact rows and predictable spacing
- one rare accent color for live agent state
- glass only for controls/navigation/transient chrome

Avoid:

- generic AI gradient blobs
- glass everywhere
- translucent dense tables/logs/code
- big marketing cards
- purple-blue fog as the default brand signal
- mascot/sparkle overload
- rounded pill soup

Suggested direction:

```css
--bg-base: #101112;
--bg-sidebar: #121314;
--bg-panel: #171819;
--bg-elevated: #1e2022;
--bg-hover: #25272a;
--border-subtle: rgba(255, 255, 255, 0.06);
--border-strong: rgba(255, 255, 255, 0.10);
--text-primary: rgba(255, 255, 255, 0.94);
--text-secondary: rgba(255, 255, 255, 0.64);
--text-muted: rgba(255, 255, 255, 0.42);
--accent-live: #55d6be;
```

The accent should probably be mint/cyan for live agent activity, not default purple. Purple can exist as an optional theme.

## Divider Rules

Full-width hairline dividers between every row are a 2008 admin-panel tell. Linear, Raycast, Cursor, and Codex all avoid them. Use one of these instead:

1. **No divider.** Default for grouped row lists inside a card. Rows separate via padding (`py-3` to `py-4`) and a soft hover background. Section headers and gaps carry the rhythm.
2. **Whisper divider.** When density is high enough that rows blur together, use `divide-y divide-line-soft/40` or `/50` on the parent. Never stack manual `<div className="border-t" />` between siblings.
3. **Inset divider.** When rows have a leading icon or label column, start the divider past it (`ml-12`, `pl-12`) so it reads intentional, not generic.
4. **Background tier.** For attached footer/error/info panels under a card, use `bg-surface-soft/35` or similar. The background shift already separates; do not also add `border-t`.

Hard rules:

- No empty `<div className="border-t border-line-soft" />` siblings as row separators.
- No stacking a border on top of a background tier shift.
- Edge-to-edge horizontal rules are reserved for: card header → body, modal header/footer, and pane separators in resizable layouts.
- Section gaps (`gap-6`, `mt-8`) replace dividers between unrelated groups.

## Material Rules

Liquid Glass / Acrylic / Mica ideas are useful only when they reinforce hierarchy.

Good places for glass:

- titlebar
- sidebar shell
- command palette
- floating companion
- popovers
- toasts
- transient controls
- floating inspector chrome

Bad places for glass:

- chat transcript
- code blocks
- diffs
- logs
- memory tables
- dense tool outputs
- long-form text

Use Mica-like long-lived shell material and Acrylic/Liquid-Glass-like transient surfaces. Content stays solid and readable.

## Motion Direction

Motion should explain state, not decorate.

Use opacity and transform animations by default. Avoid animating layout-heavy properties in long lists and transcripts.

Recommended timing:

```txt
hover: 80-120ms
row enter: 120-160ms
command palette: 160-200ms
panel open: 180-240ms
trace expand: 160-220ms
route transition: 180-320ms
success check: 100-140ms
```

Patterns:

- Streaming text: batch token updates; animate new blocks only.
- Tool calls: active expanded, success collapsed, errors and approvals expanded.
- Tool groups: group bursts under “N actions” with individual rows inside.
- Long tasks: show stages, not fake percentages.
- Errors: appear in place; no shake except form validation.
- Connection state: thin rail or badge with explicit text.
- Reduced motion: replace movement with opacity/state changes.
- Reduced transparency: use solid surfaces.

## Near-Term Implementation Bets

Do not import a whole ecosystem at once. Add libraries only when they collapse real complexity.

Reasonable near-term candidates:

- `cmdk` for global command palette
- `react-resizable-panels` for workspace and inspector panes
- `@tanstack/react-virtual` for long transcripts/timelines
- `motion` only where native CSS transitions are insufficient
- `floating-ui` only if popover positioning outgrows current CSS

Avoid introducing shadcn/Radix wholesale unless it reduces local code and matches the current visual language.

## Immediate Design Backlog

1. Define visual tokens for graphite surfaces, borders, accents, status colors, and glass chrome.
2. Convert settings/providers/models into capability-oriented surfaces with shared controls.
3. Redesign tool calls into grouped timeline states.
4. Add a right-side inspector for provenance, context, sources, approvals, and tool details.
5. Build a global command palette.
6. Add motion tokens and apply them to rows, panels, popovers, streaming, and connection states.
7. Rework memory UI around trust/provenance and source-linked facts/patterns/episodes.
8. Add a companion-window concept later: quick ask, quick remember, screenshot/current-context attach, promote to workspace.

## Reference Set

- OpenAI Codex app: https://openai.com/index/introducing-the-codex-app/
- Claude Code desktop redesign: https://claude.com/blog/claude-code-desktop-redesign
- Claude Code desktop docs: https://code.claude.com/docs/en/desktop
- Raycast settings: https://manual.raycast.com/settings
- Raycast AI chat: https://manual.raycast.com/ai/chat
- Raycast MCP: https://manual.raycast.com/ai/model-context-protocol
- Linear changelog: https://linear.app/changelog
- Linear UI refresh: https://linear.app/changelog/2026-03-12-ui-refresh
- Windsurf Cascade: https://docs.windsurf.com/windsurf/cascade/cascade
- ChatGPT desktop: https://chatgpt.com/features/desktop/
- Apple Liquid Glass overview: https://developer.apple.com/documentation/TechnologyOverviews/liquid-glass
- Apple HIG materials: https://developer.apple.com/design/human-interface-guidelines/ios/visual-design/materials/
- Microsoft materials: https://learn.microsoft.com/en-us/windows/apps/design/signature-experiences/materials
- Fluent material: https://fluent2.microsoft.design/material
- assistant-ui ToolGroup: https://www.assistant-ui.com/docs/ui/tool-group
- Vercel AI Elements: https://examples.vercel.com/blog/introducing-ai-elements
- Motion accessibility: https://motion.dev/docs/react-accessibility
- Web animation performance: https://web.dev/animations-and-performance/
- cmdk: https://cmdk.paco.me/
- react-resizable-panels: https://github.com/bvaughn/react-resizable-panels
- TanStack Virtual: https://tanstack.com/virtual/latest
