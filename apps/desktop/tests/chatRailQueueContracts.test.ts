import { expect, test } from "bun:test";
import { readFileSync } from "node:fs";

const read = (path: string) => readFileSync(new URL(path, import.meta.url), "utf8");

const runtimeRail = read("../src/features/chat/components/ChatRail.tsx");
const runtimeQueue = read("../src/features/chat/components/QueueCard.tsx");
const runtimeQueueContract = read("../src/features/chat/lib/queue.ts");
const runtimeStyles = read("../src/design/chat.css");
const runtimeRailStyles = runtimeStyles.slice(
  runtimeStyles.indexOf("/* Conversation rail"),
  runtimeStyles.indexOf("/* Activity / Sources inspector"),
);

test("conversation rails use exact transform-only marks and continuous label blur", () => {
  expect(runtimeRail).not.toContain("style.width");
  expect(runtimeRail).not.toContain("style.top");
  expect(runtimeRail).toContain("style.transform");
  expect(runtimeRail).toContain("--conversation-rail-label-y");

  expect(runtimeRailStyles).toContain("transform: scaleX(1.5);");
  expect(runtimeRailStyles).toMatch(/conversation-rail-label[\s\S]*?transition:\s*transform[\s\S]*?opacity[\s\S]*?filter/);
  expect(runtimeRailStyles).not.toMatch(/conversation-rail-(?:mark|label)[\s\S]*?var\(--ease-out\)/);
  expect(runtimeRailStyles).not.toMatch(/transition:[^;]*\bwidth\b/);
  expect(runtimeRailStyles).not.toMatch(/transition:[^;]*\btop\b/);
  expect(runtimeRailStyles).toMatch(/board-chat-rail__mark[\s\S]*?transform var\(--motion-furniture\) var\(--motion-dissolve-ease\)/);
  expect(runtimeRailStyles).toMatch(/board-chat-rail__tick\s*\{[\s\S]*?height:\s*9px;/);
  expect(runtimeRailStyles).toMatch(/board-chat-rail__mark\s*\{[\s\S]*?height:\s*var\(--tab-line-size\);[\s\S]*?border-radius:\s*var\(--r-control\);/);
  // The rail used to be hidden with the sidebar because it sat 12px INTO that
  // gutter and left the window without it. It keeps the same breathing room
  // from the window edge instead of disappearing.
  expect(runtimeRailStyles).not.toMatch(/data-sidebar-hidden="true"\] \.board-chat-rail\s*\{[^}]*opacity:\s*0;/);
  expect(runtimeRailStyles).toMatch(/data-sidebar-hidden="true"\] \.board-chat-rail\s*\{\s*left:\s*calc\(var\(--conversation-rail-inline-offset\) \* -1\);/);
  expect(runtimeRailStyles).not.toContain("var(--ease)");
});

test("queued messages use the ten-item Fluid Functionalism stack", () => {
  expect(runtimeQueueContract).toContain("export const QUEUE_MAX_ITEMS = 10");
  expect(runtimeQueueContract).toContain("export const QUEUE_ITEM_HEIGHT_PX = 44");
  expect(runtimeQueueContract).toContain("export const QUEUE_STACK_MAX_PEEKS = 2");
  expect(runtimeQueue).toContain("stackExpanded");
  expect(runtimeQueue).toContain("onMouseEnter={() => setStackHovered(true)}");
  expect(runtimeQueue).toContain("moveQueuedMessage(pointerDownId, slot)");
  expect(runtimeQueue).toContain('className="board-queue-stack"');
  expect(runtimeQueue).not.toContain('"Next"');
  expect(runtimeQueue).not.toContain("queueOrdinal");
  expect(runtimeQueue).toContain('aria-label="Remove queued message"');
  expect(runtimeQueue).toContain("board-queue-stack__thumbnails");
  expect(runtimeQueue).not.toContain("cancelQueuedMessageApi");
});
