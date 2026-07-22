# Desk + Paper redesign reference ledger

This redesign is a system pass, not a bugfix queue. Local visual repairs are not accepted as the design method.

## Working contract

1. Audit the full surface: Chat, Memory, Settings, shared foundation, and motion.
2. Decide shared contracts before editing individual screens.
3. Prototype each primitive once, validate it, then propagate it everywhere.
4. Put static geometry, typography, spacing, color, elevation, radius, and component states in Paper.
5. Put interaction behavior and timing in the motion library and motion lab.
6. Validate the complete matrix before calling an iteration settled.

HTML mockups are interaction labs. They must express one coherent system, not accumulate page-specific overrides.

## Product baseline

The current ntrp desktop app is the primary reference for information architecture, placement, and established behavior. The redesign may refine its visual language, but must not invent product concepts.

- Preserve the current Chat structure: global sidebar, centered chat lane, composer, conversation rail, and Activity/Sources inspector.
- Automations is a separate window, not a sidebar tab.
- Do not introduce invented concepts such as “Spaces.”
- Preserve useful current proportions and shadows unless the system pass deliberately replaces them.
- Use Phosphor as the canonical icon family. Do not mix packs or use improvised SVG metaphors.

The user-provided current-app screenshots in the redesign conversation are the visual baseline. Capture fresh app screenshots before each holistic pass so comparison evidence is durable.

## Repository artifacts

- [Interactive plate](./board-memory.html) — settled Memory interaction draft.
- [Consolidated language](./board-language.html) — current static language notes.
- [Motion lab](./board-motion.html) — isolated interaction demonstrations.
- [Motion tokens](./board-motion.js) — canonical timing, easing, distance, and blur values for mockups.
- [Chat draft](./board-chat.html) — redesign exploration; not yet a settled system reference.
- [Settings draft](./board-settings.html) — Settings exploration.
- [Home draft](./board-home.html) — supervision home; layout direction UNSETTLED, awaiting user pick (see research).
- [Home research synthesis](./board-home-research.md) — 6-lens web research (Linear, triage email, agent dashboards, today-apps, ADHD evidence, ops glanceability): dark-cockpit baseline, triage-deck mechanics, no-perpetual-animation, spatial stability.
- [Home product ground truth](./board-home-product.md) — the SHIPPED app read end-to-end: Home.tsx/WorkBrief/FocusRow anatomy + exact strings, the Ask model (kinds fyi/question/review, why_now/what_next, ranking `question > review > notify` cap 4, snooze modeled but un-UIed), run/automation status enums, AgentRunRow/RunRuler idioms. The mockup's vocabulary now matches this.
- [ADHD apps field research](./board-home-adhd-apps.md) — what ADHD people's surviving apps actually do (Tiimo, Structured, Llama Life, Goblin Tools, Finch, Marvin, Focusmate…): time-as-shape, one-task players, no-scar-tissue returns, agents-as-body-doubles, spiciness dials, felt completions.
- [Stagger lab](./stagger-lab.html) — earlier sequencing study.

External artifacts:

- [Consolidated language artifact](https://claude.ai/code/artifact/2b1bee3e-cde7-42e9-824a-f249d3ec4a5f)
- [Layered, not glassy inspiration board](https://claude.ai/code/artifact/2dabbaff-9b11-4862-9e9a-79ebb6772b7b)
- Local odometer/blur study: `/Users/escept1co/src/interaction-lab`

## Surfaces and elevation

Reference: [Fluid Functionalism — Surfaces](https://www.fluidfunctionalism.com/docs/surfaces)

Local source:

- `/Users/escept1co/src/fluid-functionalism/lib/elevated.tsx`
- `/Users/escept1co/src/fluid-functionalism/lib/surface-context.tsx`
- `/Users/escept1co/src/fluid-functionalism/lib/surface-classes.ts`

Principles to adapt:

- Elevation is relative to the current substrate, not an isolated card style.
- Nested surfaces use a consistent level model.
- Light mode lets shadow carry higher elevation after the first material steps.
- Dark mode needs both a visible material step and a layered edge/shadow; borders cannot disappear.
- Hover and selection are small relative lifts, not new unrelated colors.
- A surface has one coherent edge. Avoid a border plus a second competing outline.

Do not copy the demo styling literally. Translate its substrate model into Desk + Paper tokens.

## Composer

Reference: [Fluid Functionalism — Input Message](https://www.fluidfunctionalism.com/docs/input-message)

Local source:

- `/Users/escept1co/src/fluid-functionalism/registry/default/input-message.tsx`
- `/Users/escept1co/src/fluid-functionalism/app/docs/input-message/page.tsx`

Contract to adapt:

- One elevated input surface with stable left and right control slots.
- Auto-resize from one to eight rows without changing toolbar proportions.
- Clicking empty surface space focuses the input.
- Hover, focus, and drag recolor the same edge instead of stacking halos.
- Send, queue, and stop preserve the same button geometry and morph in place.
- Attachments and queued items use layout-aware motion.
- Enter sends; Shift+Enter inserts a newline.
- Reduced-motion and accessible labels are first-class states.
- Match ntrp's existing compact proportions; do not scale the reference blindly.

### Queue lifecycle

- While the assistant is working, an empty draft keeps Stop; a non-empty draft morphs that same control into Queue.
- Enqueue snapshots the trimmed text and attachments, clears the draft, and preserves focus.
- The head is visibly “Next.” Completion or Stop auto-dispatches it, then continues through the queue.
- Stable queue items support edit, remove, and reorder; keyboard equivalents are Enter/F2, Delete, and Alt+Arrow.
- The queue is a staged layer attached to the composer, not a toast or another chat message.
- Insert/remove uses the shared dissolve tokens; reorder retargets existing rows without recreating them.

## Live and running states

References:

- [shadcn — Shimmer](https://ui.shadcn.com/docs/utils/shimmer)
- [Motion Primitives — Text Shimmer](https://motion-primitives.com/docs/text-shimmer)

Rules:

- Use shimmer for genuinely running or streaming text, replacing unexplained progress circles.
- Keep the base label readable; shimmer is a moving highlight, not disappearing text.
- Do not combine shimmer, a spinner, and a “live” label for the same state.
- Default to a quiet linear cycle around two seconds, scaled to text length.
- Reduced motion falls back to static text.
- Completion dissolves the shimmer into the settled label without moving layout.

## Tabs

Two primitives are required; do not blend them.

### View tabs

Use for peer views such as Activity/Sources.

- Stable text positions.
- Sliding underline or indicator.
- Horizontal content transition with dissolve.
- No capsule selection surface.
- Inspector actions occupy a separate, consistently aligned action group.

### Rail tabs

Use for compact mode switches such as Files/Notebook/Facts and tool policies.

- Icons remain visible for every option.
- The active label expands beside its icon; inactive labels dissolve away.
- The active surface moves without spring jumps or width remeasurement artifacts.
- Geometry and timing are identical across Chat, Memory, and Settings.

Visual reference: user-provided Twitter/X expanding rail screenshot in the redesign conversation, 2026-07-18.

## Selectors and menus

- [shadcn/Radix grouped combobox](https://ui.shadcn.com/docs/components/radix/combobox#groups) — model grouping and search behavior.
- Model, effort, and speed belong to one model-settings menu hierarchy rather than unrelated composer controls.
- Related selectors share a grid and control height. Content length must not create arbitrary component widths.

## Motion language

- **Dissolve:** opacity plus gradual blur for replacement, removal, or focus transfer.
- Horizontal structures transition horizontally; vertical structures transition vertically.
- Preserve object identity: controls morph in place instead of disappearing and reappearing elsewhere.
- Sidebar, inspector, chat lane, bubble, trace, and composer reflow as one coordinated layout transition.
- Peek behavior is shared across Chat, Memory, and Settings; it cannot have page-specific entrances.
- Live motion communicates work. Settled surfaces remain quiet.
- Avoid decorative springing, click-scale feedback, and stagger unless they clarify hierarchy.
- All motion uses `board-motion.js`; no page-local durations or easings.

## Shared system decisions required before the next screen pass

- Typography ramp and font roles.
- Spacing rhythm and alignment grid.
- Surface/elevation map for light and dark themes.
- Radius profiles and which component classes use each profile.
- Icon family, weights, optical sizes, and semantic mapping.
- Canonical ViewTabs and RailTabs primitives.
- Composer geometry and model-settings hierarchy.
- Inspector shell: floating peek, docked sidebar, controls, and transitions.
- Sidebar responsive states and resizing bounds.
- Shimmer eligibility and completion behavior.
- Inline approval, input-needed, error, retry, and artifact-row patterns.
- Shared motion tokens and reduced-motion fallbacks.

## Validation gate

Every system iteration must be checked across:

- Chat, Memory, Settings, and foundation components.
- Light and dark themes.
- Wide, medium, narrow, and sidebar-resized windows.
- Sidebar open/closed; inspector closed/floating/docked.
- Settled, running, approval, input-needed, error, and completion states.
- Keyboard navigation, reduced motion, and 200% zoom.
- Typography, spacing, radius, icon, surface, shadow, and animation consistency.

An iteration is not ready for Paper propagation until these checks pass in the interaction draft. Static Paper frames and the motion specification are then updated together.
