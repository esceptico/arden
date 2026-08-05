import { expect, test } from "bun:test";
import { readFileSync } from "node:fs";

const read = (path: string) => readFileSync(new URL(path, import.meta.url), "utf8");

test("Home keeps the mock's compact-height attention spacing", () => {
  const css = read("../src/features/home/components/Home.css");

  expect(css).toMatch(/\.mission-control__answer\s*\{[\s\S]*?padding:\s*\.375rem var\(--space-0-5\) 0;/);
  expect(css).toMatch(/\.mission-control__capture input\s*\{[\s\S]*?font:\s*var\(--weight-heading\) var\(--text-md\)\/normal var\(--sans\);/);
  // Both hint states share one grid cell — the slot holds the wider state's
  // width so the ⌘K ⇄ ⌘↩ swap never reflows the input row.
  expect(css).toMatch(/\.mission-control__capture-shortcut\s*\{[\s\S]*?display:\s*inline-grid;[\s\S]*?justify-items:\s*end;/);
  expect(css).toMatch(/__capture-shortcut > \.mission-control__capture-hint\s*\{\s*grid-area:\s*1 \/ 1;/);
  expect(css).toMatch(/\.mission-control__focus h2\s*\{[\s\S]*?display:\s*-webkit-box;[\s\S]*?-webkit-line-clamp:\s*2;/);
  expect(css).not.toMatch(/\.mission-control__focus h2\s*\{[^}]*max-width:/);
  expect(css).toMatch(/\.mission-control__focus-reason\s*\{[\s\S]*?font:\s*var\(--weight-regular\) var\(--text-sm\)\/1\.5 var\(--sans\);[\s\S]*?-webkit-line-clamp:\s*2;/);
  expect(css).toMatch(/\.mission-control__focus-next\s*\{[\s\S]*?font:\s*var\(--weight-regular\) var\(--text-sm\)\/1\.5 var\(--sans\);/);
  expect(css).toMatch(/@media \(max-height: 53rem\)\s*\{[\s\S]*?\.mission-control__room\s*\{\s*padding-top:\s*2\.875rem;\s*padding-bottom:\s*var\(--space-2-5\);/);
  // Optical center at every height: zero-basis 2:3 spacers split the FREE
  // space above/below the intact block, vanish when a full deck needs the
  // room, and the attention block must not reclaim the below-space (flex
  // none) — mid-page distribution with an internal void stays rejected.
  expect(css).toMatch(/__room::before\s*\{[\s\S]*?flex:\s*2 0 0px;/);
  expect(css).toMatch(/__room::after\s*\{[\s\S]*?flex:\s*3 0 0px;/);
  expect(css).toMatch(/__attention\s*\{[\s\S]*?flex:\s*none;/);
  // All-clear rhythm: field → activity table shares the 20px headline beat.
  expect(css).toMatch(/__attention\[data-deck-empty\]\s*\{\s*margin-top:\s*var\(--space-5\);/);
  const compact = css.slice(css.indexOf("@media (max-height: 47rem)"));
  expect(compact).toContain(".mission-control__focus-foot { margin-top: .875rem; }");
  expect(compact).not.toContain(".mission-control__focus-actions { margin-top:");
  const narrow = css.slice(css.indexOf("@media (max-width: 46.25rem)"), css.indexOf("@media (max-height: 53rem)"));
  expect(narrow).not.toContain(".mission-control__peer-meta");
  expect(css).not.toMatch(/@media \(max-height: 47rem\)\s*\{[\s\S]*?\.mission-control__attention\s*\{/);
});

test("Home's headline stands alone — running/handled counts live in the activity strips", () => {
  const home = read("../src/features/home/components/Home.tsx");
  expect(home).not.toContain("watchLine");
  expect(home).toContain("const answer = greetingPhrase(brief.needs_you.length, stale, hasAmbientActivity);");
});

test("Home peer metadata keeps the mock's semantic table columns", () => {
  const focus = read("../src/features/home/components/FocusRow.tsx");
  const foundation = read("../src/design/foundation.css");

  expect(focus).toContain('className="mission-control__peer-meta arden-attention-meta" data-columns="3"');
  expect(focus.match(/arden-attention-meta__separator/g)).toHaveLength(2);
  expect(foundation).toMatch(/\.arden-attention-meta\s*\{[^}]*--attention-meta-columns:\s*8ch 1ch minmax\(12ch, 16ch\) 1ch 8ch;[^}]*display:\s*grid;[^}]*grid-template-columns:\s*var\(--attention-meta-columns\);/s);
  expect(foundation).toMatch(/\.arden-attention-meta__separator\s*\{[^}]*text-align:\s*center;/s);
  expect(foundation).toMatch(/\.arden-attention-meta > time\s*\{[^}]*text-align:\s*right;/s);
});

test("Home swaps verbs, reply, and snooze inside the mock's fixed motion slot", () => {
  const homeFocus = read("../src/features/home/components/FocusRow.tsx");
  const homeCss = read("../src/features/home/components/Home.css");

  expect(homeFocus).toContain('className="mission-control__focus-foot"');
  expect(homeFocus).toContain('data-io-hidden={footMode === "actions" ? undefined : ""}');
  expect(homeFocus).toContain('data-io-hidden={footMode === "reply" ? undefined : ""}');
  expect(homeFocus).toContain('data-io-hidden={footMode === "snooze" ? undefined : ""}');
  expect(homeCss).toMatch(/\.mission-control__focus-foot\s*\{[^}]*display:\s*grid;/s);
  expect(homeCss).toMatch(/\.mission-control__focus-actions,[\s\S]*?grid-area:\s*1\s*\/\s*1;[\s\S]*?opacity var\(--motion-feedback\)[\s\S]*?transform var\(--motion-dissolve\)[\s\S]*?filter var\(--motion-feedback\)/);
  expect(homeCss).toMatch(/\.mission-control__focus-foot > \[data-io-hidden\]\s*\{[^}]*translateY\(3px\);[^}]*blur\(var\(--swap-blur\)\);/s);
});

test("Area room keeps one focused decision and a compact management view", () => {
  const room = read("../src/features/areas/components/AreaRoom.tsx");
  const management = read("../src/features/areas/components/AreaManagement.tsx");
  const css = read("../src/design/area.css");

  expect(room).toContain('className="board-area-needs"');
  expect(room).toContain('className="board-area-room-scroll scroll-fade"');
  expect(room).toContain("AreaManagement");
  expect(room).toContain('id="area-outcomes-title">Outcomes');
  expect(room).not.toContain("complete</span>");
  expect(management).toContain("Latest check");
  expect(management).toContain("Next check");
  expect(management).toContain("Current work");
  expect(management).not.toContain("open_loops");
  expect(room).toContain("AreaRequestDeck");
  expect(room).not.toContain("AreaSettingsButton");
  expect(room).not.toContain("AgentPresence");
  expect(room).not.toContain("OpenLoops");
  expect(css).toMatch(/\.board-area-page\s*\{[\s\S]*?display:\s*flex;[\s\S]*?flex-direction:\s*column;/);
  expect(css).toMatch(/\.board-area-management__checks\s*\{[\s\S]*?grid-template-columns:\s*repeat\(2, minmax\(0, 1fr\)\);/);
  // Vertical only: `.scroll-fade` masks one axis, so the room must never open
  // a horizontal scroll — sub-pixel rounding alone was enough to let it drag.
  expect(css).toMatch(/\.board-area-room-scroll\s*\{[\s\S]*?min-height:\s*0;[\s\S]*?flex:\s*1;[\s\S]*?overflow:\s*clip auto;/);
});

test("Area room matches the mock's 720px typography and pressure hierarchy", () => {
  const css = read("../src/design/area.css");
  const compactStart = css.indexOf("@media (max-height: 47rem)");
  const shortStart = css.indexOf("@media (max-height: 42rem)");
  const compact = css.slice(compactStart, shortStart);
  const short = css.slice(shortStart);
  const answer = css.match(/\.board-area-header h1\s*\{([^}]*)\}/)?.[1] ?? "";
  const subtitle = css.match(/\.board-area-header p\s*\{([^}]*)\}/)?.[1] ?? "";
  const title = css.match(/\.board-area-deck__card h2\s*\{([^}]*)\}/)?.[1] ?? "";
  const reason = css.match(/\.board-area-deck__reason\s*\{([^}]*)\}/)?.[1] ?? "";

  expect(answer).toContain("font: var(--type-editorial-title);");
  expect(subtitle).toContain("font: var(--weight-heading) var(--text-sm)/1.5 var(--sans);");
  expect(title).toContain("font: var(--weight-semibold) var(--text-2xl)/1.3 var(--sans);");
  expect(title).not.toContain("max-width:");
  expect(reason).toContain("font: var(--weight-regular) var(--text-sm)/1.5 var(--sans);");
  expect(compact).toContain(".board-area-header h1 { font-size: var(--text-2xl); }");
  expect(compact).toContain(".board-area-header p { margin-top: .3125rem; }");
  expect(compact).toContain(".board-area-deck__card h2 { margin-top: .5rem; font-size: var(--text-xl); }");
  expect(compact).not.toContain("-webkit-line-clamp: 1;");
  expect(compact).not.toContain(".board-area-deck__actions {");
  expect(short).toContain("-webkit-line-clamp: 1;");
  expect(short).toContain(".board-area-deck__actions { margin-top: var(--space-3); }");
});

test("Chat shares source and response actions in one persistent footer row", () => {
  const actions = read("../src/features/chat/components/MessageActions.tsx");
  const css = read("../src/design/chat.css");

  expect(actions).not.toContain("opacity-0");
  expect(actions).toContain("board-message-actions--assistant");
  expect(css).toMatch(/\.board-assistant > \.board-message-actions\s*\{[\s\S]*?grid-column:\s*2;[\s\S]*?grid-row:\s*2;/);
  expect(css).toMatch(/\.board-assistant\s*\{[\s\S]*?inline-size:\s*100%;[\s\S]*?max-inline-size:\s*none;/);
  expect(css).toMatch(/\.board-assistant \.board-assistant__prose :where\(h2\)\s*\{\s*max-inline-size:\s*40\.625rem;/);
  expect(css).toMatch(/\.board-assistant \.board-assistant__prose :where\(p\)\s*\{\s*max-inline-size:\s*40\.625rem;/);
});

test("Chat ports the mock's Worked disclosure instead of wrapping it in a Marker pill", () => {
  const turn = read("../src/features/chat/components/TurnGroup.tsx");
  const header = read("../src/features/chat/components/ActivityHeader.tsx");
  const message = read("../src/features/chat/components/Message.tsx");
  const css = read("../src/design/chat.css");

  expect(header).toContain("<SquareTerminal aria-hidden />");
  expect(header).toContain('className="board-trace__count"');
  expect(header).toContain('className="board-trace__chevron"');
  expect(header).not.toContain("<Marker");
  expect(turn).not.toContain("<Marker");
  expect(message).toContain("hideActivityHeader");
  expect(css).toMatch(/\.board-trace\s*\{[^}]*display:\s*grid;[^}]*grid-template-columns:\s*max-content minmax\(0, 1fr\);/s);
  expect(css).toMatch(/\.board-trace::after\s*\{[^}]*grid-column:\s*1\s*\/\s*-1;[^}]*height:\s*1px;[^}]*margin-top:\s*14px;[^}]*background:\s*var\(--edge\);/s);
  expect(css).toMatch(/\.board-trace__toggle\s*\{[^}]*width:\s*max-content;[^}]*height:\s*28px;[^}]*padding:\s*0 7px 0 2px;/s);
  expect(css).not.toMatch(/\.board-trace__toggle:hover\s*\{[^}]*background:/s);
});

test("Home, Chat, and Area surfaces use named depth and canonical materials", () => {
  const home = read("../src/features/home/components/Home.css");
  const chat = read("../src/design/chat.css");
  const chatStage = read("../src/features/chat/components/Chat.tsx");
  const rail = read("../src/features/chat/components/ChatRail.tsx");
  const messages = read("../src/features/chat/components/Messages.tsx");
  const queue = read("../src/features/chat/components/QueueCard.tsx");
  const session = read("../src/features/sessions/components/SessionRow.tsx");
  const inspector = read("../src/features/background-agents/components/AgentRightSidebar.tsx");

  expect(home).toMatch(/\.mission-control__capture-field:focus-within\s*\{[\s\S]*?var\(--shadow-3\), 0 0 0 1px color-mix\(in oklab, var\(--accent\) 40%, transparent\)/);
  expect(chatStage).toContain("z-[var(--z-raised)]");
  expect(chatStage).toContain("z-[var(--z-sticky)]");
  expect(chatStage).toContain("z-[var(--z-popover)]");
  expect(rail).toContain("z-[var(--z-raised)]");
  expect(messages).toContain("z-[var(--z-sticky)]");
  expect(messages).toContain("data-page-skeleton");
  expect(messages).not.toContain("surface-floating");
  expect(queue).not.toContain("surface-floating");
  expect(queue).not.toMatch(/rounded-(?:t|b)-\[/);
  expect(session).not.toContain("surface-card");
  expect(session).not.toContain("rounded-lg");
  expect(queue).toContain("Direct port of Fluid Functionalism");
  expect(chat).toMatch(/\.board-queue-stack__item\s*\{[\s\S]*?right:\s*40px;[\s\S]*?left:\s*40px;[\s\S]*?height:\s*44px;[\s\S]*?border-radius:\s*var\(--r-panel\);/);
  expect(chat).not.toContain("color-mix(in oklab, var(--surface-3) 88%, transparent)");
  expect(chat).not.toContain("backdrop-filter: blur(18px) saturate(1.08)");
  expect(chat).toMatch(/\.board-inspector\[data-mode="hub"\]\s*\{[\s\S]*?border-radius:\s*var\(--r-shell\);/);
  expect(chat).toMatch(/body:has\(\.board-inspector\[data-mode="hub"\]\) :is\(\.sidebar-toggle, \.shell-nav, \.right-sidebar-toggle\)\s*\{[\s\S]*?z-index:\s*var\(--z-shell\);/);
  expect(chat).toMatch(/\.board-inspector\[data-mode="peek"\]\s*\{[\s\S]*?border-radius:\s*var\(--r-panel\);/);
  expect(chat).toMatch(/\.board-inspector__header\s*\{[\s\S]*?padding:\s*0 8px 0 14px;[\s\S]*?background:\s*transparent;/);
  expect(chat).toMatch(/\.board-inspector__content\s*\{[\s\S]*?padding:\s*14px 14px 28px;/);
  expect(inspector).not.toContain("surface-radius-md");
  expect(inspector).not.toContain("board-inspector surface-panel");
  expect(inspector).toContain("board-inspector surface-peek");
});

test("Settings navigation uses the mock sidebar geometry and moving highlight", () => {
  const settings = read("../src/features/settings/components/SettingsModal.tsx");
  const tabs = read("../src/components/ui/Tabs.tsx");
  const css = read("../src/design/settings.css");

  expect(tabs).toContain('"sidebar"');
  expect(settings).toContain('variant="sidebar"');
  expect(settings).toContain('indicatorClassName="settings-nav-highlight"');
  expect(css).toMatch(/\.settings-nav-group \+ \.settings-nav-group\s*\{\s*margin-top:\s*var\(--space-3\);/);
  expect(css).toMatch(/\.settings-nav-group-label\s*\{[\s\S]*?height:\s*1\.75rem;[\s\S]*?font:\s*var\(--type-rail-group\);/);
  expect(css).toMatch(/\.settings-nav-highlight\s*\{[\s\S]*?background:\s*var\(--state-selected-bg\);[\s\S]*?box-shadow:\s*inset 0 0 0 1px/);
  expect(css).toMatch(/\.board-settings \.settings-nav-row\s*\{[\s\S]*?height:\s*1\.875rem;[\s\S]*?font:\s*var\(--type-rail-item\);/);
  expect(css).toMatch(/\.board-settings \.settings-nav-row\[data-active="true"\]\s*\{[\s\S]*?background:\s*transparent;/);
});

test("Home, Chat, and Area use the mock's exact visible icon variants", () => {
  const sidebar = read("../src/features/sessions/components/Sidebar.tsx");
  const chat = read("../src/features/chat/components/Chat.tsx");
  const reasoning = read("../src/features/chat/components/ReasoningMessage.tsx");
  const assistant = read("../src/features/chat/components/AssistantMessage.tsx");
  const actions = read("../src/features/chat/components/MessageActions.tsx");
  const trace = read("../src/features/chat/components/ActivityRows.tsx");
  const inspector = read("../src/features/background-agents/components/AgentRightSidebar.tsx");

  expect(sidebar).toContain("BubbleChat");
  expect(sidebar).toContain("ZapIcon");
  expect(sidebar).toContain("Brain01");
  expect(chat).toContain("ArrowLeft02");
  expect(reasoning).toContain("Brain01");
  expect(assistant).toContain("BookOpen01");
  expect(actions).toContain("PencilEdit02");
  for (const variant of ["Globe02", "File01", "PencilEdit02", "LeftToRightListBullet", "Bot", "Stop"]) {
    expect(trace).toContain(variant);
  }
  // The inspector's glyph now lives in the shared SidebarToggle, which swaps
  // it with state instead of showing the closing icon while already closed.
  expect(inspector).toContain("<SidebarToggle");
  expect(inspector).toContain('side="right"');
  expect(sidebar).not.toContain("strokeWidth");
  expect(chat).not.toContain("strokeWidth");
  expect(reasoning).not.toContain("strokeWidth");
  expect(assistant).not.toContain("strokeWidth");
  expect(actions).not.toContain("strokeWidth");
});

test("humanizeSlug keeps ordinary area labels readable", async () => {
  const { humanizeSlug } = await import("@/lib/format");
  expect(humanizeSlug("job-applications")).toBe("Job applications");
  expect(humanizeSlug("avanta")).toBe("Avanta");
});
