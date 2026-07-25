import { expect, test } from "bun:test";
import { readFileSync } from "node:fs";

const read = (path: string) =>
  readFileSync(new URL(path, import.meta.url), "utf8");

function rule(source: string, selector: string): string {
  const start = source.indexOf(`${selector} {`);
  expect(start).toBeGreaterThanOrEqual(0);
  const end = source.indexOf("}", start);
  expect(end).toBeGreaterThan(start);
  return source.slice(start, end + 1);
}

test("sidebars and peeks use solid shared furniture materials", () => {
  const shared = read("../src/styles.css");
  const shell = read("../src/design/shell.css");
  const chat = read("../src/design/chat.css");
  const memory = read("../src/design/memory.css");
  const automation = read(
    "../src/features/automations/components/automations-workspace.css",
  );
  const settings = read("../src/design/settings.css");
  const inspector = read(
    "../src/features/background-agents/components/AgentRightSidebar.tsx",
  );
  const memoryView = read(
    "../src/features/memory/components/ArtifactMemoryView.tsx",
  );
  const triggerPeek = read(
    "../src/features/automations/components/ScheduleTriggerPeek.tsx",
  );
  const automations = read(
    "../src/features/automations/components/AutomationsModal.tsx",
  );
  const electron = read("../electron/main.cjs");

  const peekMaterial = rule(shared, ".surface-peek");
  expect(peekMaterial).toContain("background: var(--surface-3)");
  expect(peekMaterial).toContain("box-shadow: var(--shadow-3)");
  expect(peekMaterial).toContain("backdrop-filter: none");
  expect(peekMaterial).toContain(
    "-webkit-backdrop-filter: none",
  );

  expect(inspector).toContain(
    'className="board-inspector surface-peek absolute',
  );
  expect(memoryView).toContain('className="mw-context surface-peek"');
  expect(triggerPeek).toContain(
    'className="automation-trigger-peek surface-peek"',
  );
  expect(automations).toContain(
    'className="automation-result-peek surface-peek"',
  );

  expect(chat).not.toContain(
    "background: color-mix(in oklab, var(--surface-3) 88%, transparent)",
  );
  expect(chat).not.toContain("backdrop-filter: blur(18px) saturate(1.08)");
  expect(rule(chat, '.board-inspector[data-mode="hub"]')).not.toContain(
    "backdrop-filter",
  );
  expect(memory).not.toContain(
    "background: color-mix(in oklab, var(--surface-3) 88%, transparent)",
  );
  expect(automation).not.toContain(
    "background: color-mix(in oklab, var(--surface-3) 88%, transparent)",
  );
  expect(automation).not.toContain(
    "backdrop-filter: blur(var(--surface-blur)) saturate(1.08)",
  );

  expect(rule(shell, ".workspace-rail")).toContain("background: var(--panel)");
  expect(rule(memory, ".memory-surface .mw-rail")).toContain(
    "background: var(--panel)",
  );
  expect(rule(automation, ".automation-workspace__rail")).toContain(
    "background: var(--surface-2)",
  );
  expect(rule(settings, ".settings-sidebar-card")).toContain(
    "background: var(--surface-2)",
  );
  expect(electron).not.toContain('vibrancy: "sidebar"');
});

