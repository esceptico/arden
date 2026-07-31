import { afterEach, expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import type { Automation } from "@/api/types";
import type { MemoryArtifactSummary } from "@/features/memory/lib/notebookTypes";
import { duplicateAutomationPayload } from "@/actions/automations";
import { AutomationDetail } from "@/features/automations/components/AutomationDetail";
import { AutomationRail } from "@/features/automations/components/AutomationRail";
import { NotebookRail } from "@/features/memory/components/NotebookRail";
import { NavRow } from "@/features/sessions/components/NavRow";
import { Sidebar } from "@/features/sessions/components/Sidebar";
import { getState, setState } from "@/stores";

let root: Root | null = null;

afterEach(async () => {
  if (root) {
    await act(async () => root?.unmount());
    root = null;
  }
  document.body.replaceChildren();
  localStorage.clear();
});

function read(path: string): string {
  return readFileSync(new URL(path, import.meta.url), "utf8");
}

function mount() {
  const app = document.createElement("div");
  app.id = "app";
  const scene = document.createElement("div");
  app.append(scene);
  document.body.append(app);
  root = createRoot(scene);
  return {
    app,
    render: async (element: ReactNode) => act(async () => root?.render(element)),
  };
}

const automation: Automation = {
  task_id: "daily-brief",
  name: "Daily brief",
  description: "Summarize the day.",
  model: null,
  triggers: [{ type: "time", at: "09:00", days: "daily" }],
  enabled: true,
  created_at: "2026-07-24T09:00:00Z",
  last_run_at: null,
  next_run_at: "2026-07-25T09:00:00Z",
  last_result: null,
  auto_approve: false,
  running_since: null,
  handler: null,
  builtin: false,
  cooldown_minutes: null,
};

const memoryArtifact: MemoryArtifactSummary = {
  path: "pages/brief.md",
  title: "brief",
  kind: "fact",
  type: "file",
  directory: "pages",
  scope: { kind: "global", key: null },
  snippet: null,
  summary: null,
  revision: "r1",
  recordCount: 0,
  generated: false,
  editable: true,
  readonlyReason: null,
  updatedAt: "2026-07-24T09:00:00Z",
  createdAt: "2026-07-24T09:00:00Z",
  labels: [],
  source: null,
};

function openContextMenu(target: HTMLElement, key = "ContextMenu") {
  target.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true, cancelable: true }));
}

test("Automation rail opens the mock action set from keyboard and dispatches real callbacks", async () => {
  const calls: string[] = [];
  const { app, render } = mount();
  await render(
    <AutomationRail
      automations={[automation]}
      selectedId={null}
      onSelect={() => calls.push("open")}
      onRunNow={() => calls.push("run")}
      onDuplicate={() => calls.push("duplicate")}
      onToggle={() => calls.push("toggle")}
    />,
  );

  const row = app.querySelector<HTMLButtonElement>(".automation-rail__row")!;
  row.focus();
  await act(async () => openContextMenu(row));

  const menu = app.querySelector<HTMLElement>('[role="menu"]')!;
  expect(Array.from(menu.querySelectorAll('[role="menuitem"]'), (item) => item.textContent)).toEqual([
    "Open",
    "Run now",
    "Duplicate",
    "Pause",
  ]);
  expect(menu.querySelectorAll('[role="separator"]')).toHaveLength(1);
  expect(document.activeElement).toBe(menu.querySelector('[role="menuitem"]'));

  await act(async () => (menu.querySelectorAll<HTMLButtonElement>('[role="menuitem"]')[1]!).click());
  expect(calls).toEqual(["run"]);

  await act(async () => {
    row.dispatchEvent(new MouseEvent("contextmenu", {
      bubbles: true,
      cancelable: true,
      clientX: 80,
      clientY: 48,
    }));
  });
  const pointerMenu = app.querySelector<HTMLElement>('[role="menu"]')!;
  await act(async () => (pointerMenu.querySelectorAll<HTMLButtonElement>('[role="menuitem"]')[2]!).click());
  expect(calls).toEqual(["run", "duplicate"]);
});

test("Memory tree context action opens the selected file from keyboard", async () => {
  const selected: string[] = [];
  const { app, render } = mount();
  await render(
    <NotebookRail
      mode="files"
      tree={{ name: "", path: "", dirs: [], files: [memoryArtifact] }}
      allNotes={[memoryArtifact]}
      records={[]}
      selectedPath={null}
      loading={false}
      error={null}
      refreshing={false}
      recordsLoading={false}
      recordsError={null}
      navigationDisabled={false}
      onModeChange={() => {}}
      onSelect={(path) => selected.push(path)}
      onRetry={() => {}}
      onRetryRecords={() => {}}
      onRefresh={() => {}}
    />,
  );

  const file = app.querySelector<HTMLButtonElement>('[data-memory-entry="pages/brief.md"]')!;
  file.focus();
  await act(async () => openContextMenu(file, "F10"));
  // Shift+F10 is the platform keyboard route; plain F10 is intentionally inert.
  expect(app.querySelector('[role="menu"]')).toBeNull();

  await act(async () => {
    file.dispatchEvent(new KeyboardEvent("keydown", {
      key: "F10",
      shiftKey: true,
      bubbles: true,
      cancelable: true,
    }));
  });
  const menu = app.querySelector<HTMLElement>('[role="menu"]')!;
  expect(Array.from(menu.querySelectorAll('[role="menuitem"]'), (item) => item.textContent)).toEqual([
    "Open",
    "Copy path",
  ]);
  await act(async () => (menu.querySelector<HTMLButtonElement>('[role="menuitem"]')!).click());
  expect(selected).toEqual(["pages/brief.md"]);
});

test("App rows expose the same pointer and keyboard context invocation geometry", async () => {
  const positions: { source: string; x: number; y: number }[] = [];
  const { app, render } = mount();
  await render(
    <NavRow
      icon={null}
      label="Memory"
      onClick={() => {}}
      onMenu={(position) => positions.push({
        source: position.source,
        x: position.x,
        y: position.y,
      })}
    />,
  );
  const row = app.querySelector<HTMLButtonElement>("button")!;
  await act(async () => {
    row.dispatchEvent(new MouseEvent("contextmenu", {
      bubbles: true,
      cancelable: true,
      clientX: 42,
      clientY: 64,
    }));
  });
  await act(async () => openContextMenu(row));
  expect(positions).toEqual([
    { source: "pointer", x: 42, y: 64 },
    { source: "keyboard", x: 12, y: -4 },
  ]);
});

test("Sidebar menus open real app and area destinations, and keep Set aside bound to archiveArea", async () => {
  const previousDesktop = window.ardenDesktop;
  const previous = {
    currentSessionId: getState().currentSessionId,
    memoryOpen: getState().memoryOpen,
    settingsOpen: getState().settingsOpen,
    automationsOpen: getState().automationsOpen,
    automations: getState().automations,
    areas: getState().areas,
  };
  const overview = {
    areas: [{
      key: "area-1",
      title: "Research",
      autonomy: null,
      page_path: null,
      live: false,
      updated: "2026-07-24T09:00:00Z",
      ask_count: 0,
    }],
    focus: [],
    brief: { done: [], in_progress: [], needs_you: [] },
  };
  setState({
    currentSessionId: null,
    memoryOpen: false,
    settingsOpen: false,
    automationsOpen: false,
    automations: [],
    areas: { ...previous.areas, overview, openAreaKey: null },
  });
  window.ardenDesktop = {
    api: {
      request: async () => ({
        ok: true,
        status: 200,
        statusText: "OK",
        contentType: "application/json",
        data: { automations: [] },
        text: "",
      }),
    },
  } as unknown as NonNullable<Window["ardenDesktop"]>;

  try {
    const { app, render } = mount();
    await render(<Sidebar />);
    await act(async () => { await Promise.resolve(); });
    const memory = Array.from(app.querySelectorAll<HTMLButtonElement>("button"))
      .find((button) => button.textContent?.trim() === "Memory")!;
    await act(async () => openContextMenu(memory));
    const appMenu = app.querySelector<HTMLElement>('[role="menu"]')!;
    expect(appMenu.textContent).toBe("Open");
    await act(async () => (appMenu.querySelector<HTMLButtonElement>('[role="menuitem"]')!).click());
    expect(getState().memoryOpen).toBe(true);

    const area = app.querySelector<HTMLButtonElement>(".home-area-list__row")!;
    await act(async () => openContextMenu(area));
    const areaMenu = app.querySelector<HTMLElement>('[role="menu"]')!;
    expect(Array.from(areaMenu.querySelectorAll('[role="menuitem"]'), (item) => item.textContent)).toEqual([
      "Open area",
      "Set aside",
    ]);
    await act(async () => (areaMenu.querySelector<HTMLButtonElement>('[role="menuitem"]')!).click());
    expect(getState().areas.openAreaKey).toBe("area-1");
    expect(read("../src/features/sessions/components/Sidebar.tsx")).toContain(
      "onSelect: () => void archiveArea(contextMenu.areaId)",
    );
  } finally {
    await act(async () => setState(previous));
    if (previousDesktop) window.ardenDesktop = previousDesktop;
    else delete window.ardenDesktop;
  }
});

test("Automation duplicate payload retains the real trigger and capability boundary", () => {
  const payload = duplicateAutomationPayload({
    ...automation,
    tool_scope: "area_observe",
    triggers: [{
      type: "message",
      source: "slack",
      channels: [{ id: "C1", name: "research" }],
      from_user_id: "U1",
      from_user_name: "Ana",
      contains: ["brief"],
    }],
  });
  expect(payload).toMatchObject({
    name: "Daily brief copy",
    description: "Summarize the day.",
    model: null,
    auto_approve: false,
    idempotency_scope: "global",
    triggers: [{
      type: "message",
      source: "slack",
      channels: ["research"],
      from_user: "Ana",
      contains: ["brief"],
    }],
    cooldown_minutes: null,
    // A copy of a custodian is not a custodian: the fine-grained key never
    // travels, and the copy keeps the source's reach rather than silently losing it.
    tool_scope: "all",
  });
  expect(payload.idempotency_key).toEqual(expect.any(String));
});

test("Each deliberate duplicate receives a fresh idempotency key", () => {
  const first = duplicateAutomationPayload(automation);
  const second = duplicateAutomationPayload(automation);

  expect(first.idempotency_scope).toBe("global");
  expect(second.idempotency_scope).toBe("global");
  expect(first.idempotency_key).not.toBe(second.idempotency_key);
});

test("An ambiguous duplicate retry can reuse its operation key", () => {
  const first = duplicateAutomationPayload(automation, "duplicate-operation");
  const retry = duplicateAutomationPayload(automation, "duplicate-operation");

  expect(retry.idempotency_key).toBe(first.idempotency_key);
});

test("Draft creation retries keep one idempotency key", async () => {
  const previousDesktop = window.ardenDesktop;
  const payloads: Array<Record<string, unknown>> = [];
  window.ardenDesktop = {
    api: {
      request: async (_config, request) => {
        if (request.path === "/automations" && request.method === "POST" && request.body) {
          payloads.push(JSON.parse(request.body) as Record<string, unknown>);
          return {
            ok: false,
            status: 500,
            statusText: "Internal Server Error",
            contentType: "application/json",
            data: { detail: "temporary failure" },
            text: "",
          };
        }
        throw new Error(`Unexpected request: ${request.method} ${request.path}`);
      },
    },
  } as unknown as NonNullable<Window["ardenDesktop"]>;

  try {
    const { app, render } = mount();
    await render(
      <AutomationDetail
        seed={{
          kind: "draft",
          preset: { name: "Retry me", prompt: "Retry this automation.", trigger_type: "time", every: "1h" },
        }}
        onClose={() => {}}
        onCreated={() => {}}
      />,
    );
    const create = Array.from(app.querySelectorAll<HTMLButtonElement>("button"))
      .find((button) => button.textContent?.trim() === "Create");
    expect(create).toBeDefined();

    await act(async () => {
      create?.click();
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    await act(async () => {
      create?.click();
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    expect(payloads).toHaveLength(2);
    expect(payloads[0]?.idempotency_scope).toBe("global");
    expect(payloads[0]?.idempotency_key).toEqual(expect.any(String));
    expect(payloads[1]?.idempotency_key).toBe(payloads[0]?.idempotency_key);
  } finally {
    window.ardenDesktop = previousDesktop;
  }
});

test("Run menus expose the real result peek and do not claim a missing deep link", () => {
  const detail = read("../src/features/automations/components/AutomationDetail.tsx");
  expect(detail).toContain('label: "Open result"');
  expect(detail).toContain("openRunContextMenu");
  expect(detail).not.toContain("Copy run link");
});
