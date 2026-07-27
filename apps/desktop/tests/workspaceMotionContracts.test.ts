import { expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import {
  BLUR,
  CONTENT_ENTER_TRANSITION,
  CONTENT_EXIT_TRANSITION,
  DISTANCE,
  EASE_DISSOLVE,
  MOTION,
  PEEK_ENTRY_LINEAR_CSS,
  PEEK_ENTER_TRANSITION,
  PEEK_EXIT_TRANSITION,
  FURNITURE_TRANSITION,
  SPRING_PEEK,
  SPRING_SHEET,
} from "../src/lib/tokens/motion";

const read = (path: string) => readFileSync(new URL(path, import.meta.url), "utf8");

test("production exports the mockup motion contract exactly", () => {
  expect(MOTION.pageEnter).toBe(0.24);
  expect(MOTION.pageChrome).toBe(0.18);
  expect(MOTION.pageStagger).toBe(0.036);
  expect(MOTION.peekEnter).toBe(0.45);
  expect(MOTION.peekExit).toBe(0.16);
  expect(MOTION.content).toBe(0.26);
  expect(DISTANCE.pageEnter).toBe(6);
  expect(DISTANCE.contentSwap).toBe(8);
  expect(DISTANCE.peekEnter).toBe(16);
  expect(DISTANCE.peekExit).toBe(12);
  expect(BLUR.pageEnter).toBe(2);
  expect(SPRING_PEEK).toEqual({ type: "spring", stiffness: 210, damping: 26, mass: 1 });
  expect(PEEK_ENTER_TRANSITION.duration).toBe(0.45);
  expect(PEEK_ENTRY_LINEAR_CSS).toContain("linear(0.00000, 0.02075");
  expect(PEEK_ENTER_TRANSITION.ease(0.5)).toBeCloseTo(0.88501, 5);
  expect(SPRING_SHEET).toEqual({ type: "spring", stiffness: 360, damping: 32, mass: 0.75 });
  expect(FURNITURE_TRANSITION).toEqual({ duration: MOTION.furniture, ease: EASE_DISSOLVE });
  expect(PEEK_EXIT_TRANSITION.duration).toBe(0.16);
  expect(CONTENT_ENTER_TRANSITION.duration).toBe(0.26);
  expect(CONTENT_EXIT_TRANSITION.duration).toBe(0.16);
});

test("workspace routes and centered shell motion have one shared owner", () => {
  const app = read("../src/app/App.tsx");
  const route = read("../src/components/workspace/WorkspaceRouteHost.tsx");
  const entrance = read("../src/components/workspace/PageEntrance.tsx");
  const stage = read("../src/components/workspace/WorkspaceStage.tsx");
  const home = read("../src/features/home/components/Home.tsx");
  const area = read("../src/features/areas/components/AreaRoom.tsx");

  expect(app).toContain("<WorkspaceRouteHost");
  expect(route).toContain("<PageEntrance");
  expect(route).toContain('<AnimatePresence initial={false} mode="wait"');
  expect(route).toContain("CONTENT_EXIT_TRANSITION");
  expect(route).toContain("CONTENT_SWAP_VARIANTS.exit(direction)");
  expect(entrance).toContain("getBoardMotion()?.pageEntrance.bind(boundary)");
  expect(entrance).not.toContain("element.animate(");
  expect(stage).toContain("previousCenter");
  expect(stage).toContain('querySelector<HTMLElement>(".board-chat__lane")');
  expect(stage).toContain("FURNITURE_TRANSITION");
  expect(home).toContain("data-page-enter-item");
  expect(area).toContain("data-page-enter-item");
  expect(home).not.toContain("RISE_IN");
  expect(area).not.toContain("RISE_IN");
});

test("chat session swaps stay inside one workspace route", () => {
  const app = read("../src/app/App.tsx");
  const chat = read("../src/features/chat/components/Chat.tsx");

  expect(app).toContain("const workspaceRouteKey = openAreaKey");
  expect(app).toContain(": workspaceKind;");
  expect(app).not.toContain("`chat:${currentSessionId");
  expect(app).toContain("}, [compactShell, currentSessionId, openAreaKey]);");
  expect(chat).toContain('<Messages key={sessionId ?? "none"} />');
});

test("only the floating chat inspector uses the mock's outside-pointer dismissal", () => {
  const inspector = read("../src/features/background-agents/components/AgentRightSidebar.tsx");

  expect(inspector).toContain('closeOnOutsidePointerDown={mode === "peek"}');
});

test("content swaps and disclosures avoid layout-property animation", () => {
  const tabs = read("../src/components/ui/TabPanels.tsx");
  const collapse = read("../src/components/ui/Collapse.tsx");
  const memory = read("../src/design/memory.css");
  const messages = read("../src/features/chat/components/Messages.tsx");

  expect(tabs).toContain("CONTENT_SWAP_VARIANTS");
  expect(collapse).not.toContain("gridTemplateRows");
  expect(collapse).toContain('mode="popLayout"');
  expect(collapse).toContain("clipPath");
  expect(memory).not.toMatch(/transition:\s*max-width/);
  expect(messages).toContain('className="board-chat__scroll absolute inset-0');
});

test("chat transcript owns the viewport scroll while the composer stays overlaid", () => {
  const chat = read("../src/design/chat.css");

  expect(chat).toMatch(
    /\.board-chat__scroll\s*\{[\s\S]*?position:\s*absolute;[\s\S]*?inset:\s*0;[\s\S]*?overflow-y:\s*auto;/,
  );
  expect(chat).toMatch(
    /\.board-chat__bottom-stack\s*\{[\s\S]*?pointer-events:\s*none;/,
  );
});

test("conversation rail indexes explicit production anchors and restores the mockup field", () => {
  const rail = read("../src/features/chat/components/ChatRail.tsx");
  const user = read("../src/features/chat/components/UserMessage.tsx");
  const assistant = read("../src/features/chat/components/AssistantMessage.tsx");
  const trace = read("../src/features/chat/components/ActivityTrace.tsx");

  expect(rail).toContain('querySelectorAll<HTMLElement>("[data-chat-rail-anchor]")');
  expect(rail).toContain("CONVERSATION_RAIL.sigmaX");
  expect(rail).toContain("CONVERSATION_RAIL.sigmaY");
  expect(rail).toContain('behavior: reducedMotion ? "auto" : "smooth"');
  // The rail is an outline of YOUR turns. Anchoring assistant prose, activity
  // steps and artifacts too made it a tick per paragraph — 63 marks in one
  // conversation, navigable by nothing in particular.
  expect(user).toContain("data-chat-rail-anchor");
  expect(assistant).not.toContain("data-chat-rail-anchor");
  expect(assistant).not.toContain("conversationAnchors");
  expect(trace).not.toContain("data-chat-rail-anchor");
  // And the label is the prompt: the anchor is the whole article, so
  // textContent would drag the action row's timestamp in with it.
  expect(user).toContain("data-chat-rail-label={railLabel || undefined}");
});

test("full-page takeovers use the shared staged entrance", () => {
  const takeover = read("../src/components/workspace/Takeover.tsx");
  const automations = read("../src/features/automations/components/AutomationsModal.tsx");
  const memory = read("../src/features/memory/components/MemorySurface.tsx");

  expect(takeover).toContain("<PageEntrance");
  expect((takeover.match(/<PageEntrance\b/g) ?? [])).toHaveLength(1);
  expect(automations).toContain('data-page-enter-item="chrome"');
  expect(memory).toContain("<PageEntrance");
  expect(memory).toContain("useReducedMotion");
  expect(memory).toContain("reducedMotion ? { duration: MOTION.reduced } : SHEET_ENTER_TRANSITION");
});

test("reduced motion lets every CSS animation settle once", () => {
  const styles = read("../src/styles.css");

  expect(styles).toContain("animation-iteration-count: 1 !important");
  expect(styles).not.toMatch(/\.animate-spin\s*\{[^}]*animation-iteration-count:\s*infinite/);
});
