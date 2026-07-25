import { afterEach, expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { act, useState, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { ContextMenu, type ContextMenuEntry } from "@/components/ui/ContextMenu";
import { CONTEXT_MENU_EDGE, placeContextMenu } from "@/components/ui/AnchoredPopover";
import { SessionContextMenu } from "@/features/sessions/components/SessionContextMenu";

let root: Root | null = null;

afterEach(async () => {
  if (root) {
    await act(async () => root?.unmount());
    root = null;
  }
  document.body.replaceChildren();
});

function read(path: string): string {
  return readFileSync(new URL(path, import.meta.url), "utf8");
}

function mount() {
  const app = document.createElement("div");
  app.id = "app";
  const trigger = document.createElement("button");
  trigger.id = "context-trigger";
  trigger.type = "button";
  const scene = document.createElement("div");
  app.append(trigger, scene);
  document.body.append(app);
  root = createRoot(scene);
  return { app, trigger, render: async (element: ReactNode) => act(async () => root?.render(element)) };
}

function ControlledContextMenu({
  trigger,
  source,
  entries,
  onClose,
}: {
  trigger: HTMLElement;
  source: "pointer" | "keyboard";
  entries: ContextMenuEntry[];
  onClose?: () => void;
}) {
  const [open, setOpen] = useState(true);
  return (
    <ContextMenu
      state={open ? { x: 32, y: 48, trigger, source } : null}
      entries={entries}
      onClose={() => {
        onClose?.();
        setOpen(false);
      }}
    />
  );
}

test("context placement uses the mock's 8px edge and fixed top-left origin", () => {
  expect(CONTEXT_MENU_EDGE).toBe(8);
  expect(placeContextMenu({ x: 2, y: 3 }, 160, 120, { width: 200, height: 200 })).toEqual({
    left: 8,
    top: 8,
    transformOrigin: "top left",
  });
  expect(placeContextMenu({ x: 192, y: 188 }, 160, 120, { width: 200, height: 200 })).toEqual({
    left: 32,
    top: 72,
    transformOrigin: "top left",
  });
});

test("context menu preserves Board compact material and row geometry", () => {
  const foundation = read("../src/design/foundation.css");
  expect(foundation).toContain(".surface-panel.surface-popover.arden-context-menu");
  expect(foundation).toContain("min-width: 10rem");
  expect(foundation).toContain("max-width: min(18rem, calc(100vw - 1rem))");
  expect(foundation).toContain("max-height: calc(100vh - 1rem)");
  expect(foundation).toContain("padding: var(--space-1)");
  // Shell radius must NOT be pinned locally: it falls through to
  // surface-popover's --r-menu so the corner-profile preference applies.
  expect(foundation).not.toMatch(/\.surface-panel\.surface-popover\.arden-context-menu\s*\{[^}]*border-radius/);
  expect(foundation).toContain("background: var(--surface-5)");
  expect(foundation).toContain("box-shadow: var(--shadow-3)");
  expect(foundation).toContain("min-height: 1.75rem");
  expect(foundation).toContain("padding: .3125rem var(--space-2)");
  expect(foundation).toContain("border-radius: calc(var(--r-menu) - var(--space-1))");
  expect(foundation).toContain(".arden-context-menu__item:not(:disabled):active { transform: none; }");
});

test("keyboard context menus focus first action, wrap arrows, restore on Escape, and keep shortcuts as a trailing sibling", async () => {
  const entries: ContextMenuEntry[] = [
    { id: "open", label: "Open" , onSelect: () => {} },
    { id: "close", label: "Close", shortcut: "⌘W", onSelect: () => {} },
  ];
  const { app, trigger, render } = mount();
  trigger.focus();
  let closed = 0;
  await render(
    <ControlledContextMenu
      trigger={trigger}
      source="keyboard"
      entries={entries}
      onClose={() => { closed += 1; }}
    />,
  );

  const menu = app.querySelector<HTMLElement>('[role="menu"]')!;
  const items = menu.querySelectorAll<HTMLElement>('[role="menuitem"]');
  expect(items).toHaveLength(2);
  expect(document.activeElement).toBe(items[0]);
  const shortcut = menu.querySelector<HTMLElement>(".arden-context-menu__shortcut")!;
  expect(shortcut.textContent).toBe("⌘W");
  expect(shortcut.parentElement).toBe(items[1]);
  expect(shortcut.previousElementSibling?.textContent).toBe("Close");
  expect(Array.from(shortcut.querySelectorAll("kbd"), (key) => key.textContent)).toEqual(["⌘", "W"]);

  await act(async () => {
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowUp", bubbles: true, cancelable: true }));
  });
  expect(document.activeElement).toBe(items[1]);

  await act(async () => {
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true }));
  });
  expect(closed).toBe(1);
  expect(document.activeElement).toBe(trigger);
});

test("pointer context menus leave focus on their invoking target", async () => {
  const { app, trigger, render } = mount();
  trigger.focus();
  await render(
    <ControlledContextMenu
      trigger={trigger}
      source="pointer"
      entries={[{ id: "open", label: "Open", onSelect: () => {} }]}
    />,
  );

  expect(app.querySelector('[role="menu"]')).not.toBeNull();
  expect(document.activeElement).toBe(trigger);
});

test("outside pointerdown dismisses without restoring focus", async () => {
  const { app, trigger, render } = mount();
  const outside = document.createElement("button");
  app.append(outside);
  trigger.focus();
  let closed = 0;
  await render(
    <ControlledContextMenu
      trigger={trigger}
      source="pointer"
      entries={[
        { id: "open", label: "Open", onSelect: () => {} },
        { id: "close", label: "Close", onSelect: () => {} },
      ]}
      onClose={() => { closed += 1; }}
    />,
  );

  await act(async () => {
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowDown", bubbles: true, cancelable: true }));
  });
  expect(document.activeElement).not.toBe(trigger);

  await act(async () => {
    outside.dispatchEvent(new Event("pointerdown", { bubbles: true, cancelable: true }));
  });
  expect(closed).toBe(1);
  expect(document.activeElement).not.toBe(trigger);
});

test("session menu exposes only Board actions with a real desktop route", async () => {
  const { app, trigger, render } = mount();
  await render(
    <SessionContextMenu
      state={{ sessionId: "session-1", x: 12, y: 24, trigger, source: "pointer" }}
      onClose={() => {}}
      onOpen={() => {}}
      onRename={() => {}}
      onArchive={() => {}}
    />,
  );

  const menu = app.querySelector<HTMLElement>('[role="menu"]')!;
  expect(Array.from(menu.querySelectorAll('[role="menuitem"]'), (item) => item.textContent)).toEqual([
    "Open chat",
    "Rename",
    "Archive",
  ]);
  expect(menu.querySelectorAll('[role="separator"]')).toHaveLength(1);
  expect(menu.querySelectorAll("svg")).toHaveLength(0);
  expect(menu.textContent).not.toContain("Pin to top");
  expect(menu.textContent).not.toContain("Compact context");
  expect(menu.textContent).not.toContain("Move to Inbox");
  expect(menu.textContent).not.toContain("Copy link");
});
