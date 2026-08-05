import { afterEach, expect, test } from "bun:test";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { StorageTab } from "@/features/settings/components/StorageTab";
import { getState, setState } from "@/stores";

let root: Root | null = null;
const originalDesktop = window.ardenDesktop;
const originalState = getState();

function response(data: unknown) {
  return { ok: true, status: 200, statusText: "OK", contentType: "application/json", data, text: "" };
}

const serverConfig = {
  chat_model: "test",
  chat_model_max_context: 128_000,
  compaction_token_limit: 100_000,
  compaction_token_trigger: 90_000,
  research_model: "test",
  workflow_model: "test",
  memory_model: "test",
  embedding_model: null,
  web_search: "none" as const,
  web_search_provider: "none",
  max_depth: 1,
  reasoning_effort: null,
  reasoning_efforts: [],
  model_reasoning_efforts: {},
  compression_threshold: 0.8,
  max_messages: 100,
  compression_keep_ratio: 0.5,
  summary_max_tokens: 1_000,
  max_space_gb: 1,
  storage_backup_retention_days: 14,
  storage_allow_archived_cleanup: true,
  storage_allow_delete_cold_chats: false,
  storage_allow_current_cleanup: false,
  storage_current_inactive_days: 90,
  storage_current_minimum: 100,
  memory_enabled: true,
  integrations: {},
  tool_overrides: {},
};

const storageStatus = {
  status: "quota_blocked",
  total_bytes: 2_000,
  reclaimable_bytes: 300,
  protected_bytes: 1_700,
  reclaimed_bytes: 0,
  max_bytes: 1_000,
  target_bytes: 850,
  checked_at: "2026-08-04T00:00:00+00:00",
  categories: [
    {
      id: "chat_history",
      label: "Chat history",
      total_bytes: 2_000,
      reclaimable_bytes: 0,
      item_count: 12,
      policy_tier: 2,
      protection_reason: "Chats require an explicit cleanup plan.",
      description: "The live session database.",
      measurement_kind: "physical",
    },
  ],
  reclaimed_by_category: {},
  database_reclaim_mode: "incremental",
};

const plan = {
  plan_id: "a".repeat(64),
  before_bytes: 2_000,
  target_bytes: 850,
  estimated_after_bytes: 700,
  estimated_reclaimable_bytes: 1_300,
  attainable: true,
  actions: [
    {
      tier: 3,
      kind: "delete_current_session",
      category_id: "chat_history",
      resource_id: "old-chat",
      estimated_reclaimable_bytes: 1_300,
      destructive: true,
      description: "Permanently delete an inactive current chat",
    },
  ],
  blockers: [],
  created_at: "2026-08-04T00:00:00+00:00",
};

interface Harness {
  requests: string[];
  bodies: Array<Record<string, unknown>>;
  host: HTMLElement;
}

async function mount(executeFails = false): Promise<Harness> {
  const requests: string[] = [];
  const bodies: Array<Record<string, unknown>> = [];
  window.ardenDesktop = {
    api: {
      request: async (_config, request) => {
        requests.push(`${request.method ?? "GET"} ${request.path}`);
        if (request.body) bodies.push(JSON.parse(request.body as string));
        if (request.path === "/storage/status") return response(storageStatus);
        if (request.path === "/storage/backups") return response([]);
        if (request.path === "/sessions/archived") return response({ sessions: [] });
        if (request.path === "/config") return response(serverConfig);
        if (request.path === "/storage/plan") return response(plan);
        if (request.path === "/storage/execute") {
          if (executeFails) throw new Error("Storage changed; review the refreshed cleanup plan");
          return response({
            plan_id: plan.plan_id,
            reclaimed_bytes: 1_300,
            actions_completed: 1,
            status: { ...storageStatus, status: "ok", total_bytes: 700 },
          });
        }
        return response({});
      },
    },
  } as Window["ardenDesktop"];
  setState({ connected: true, serverConfig, archivedSessions: [], currentSessionId: "open-chat" });
  const host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  await act(async () => {
    root?.render(<StorageTab />);
    await Bun.sleep(0);
  });
  return { requests, bodies, host };
}

const button = (host: HTMLElement, match: (label: string) => boolean) =>
  Array.from(host.querySelectorAll("button")).find((el) => match(el.textContent ?? ""))!;

afterEach(async () => {
  if (root) await act(async () => root?.unmount());
  root = null;
  window.ardenDesktop = originalDesktop;
  setState({
    connected: originalState.connected,
    serverConfig: originalState.serverConfig,
    archivedSessions: originalState.archivedSessions,
    currentSessionId: originalState.currentSessionId,
    prefs: originalState.prefs,
  });
  document.body.replaceChildren();
});

test("the inventory explains each category in place, without a tooltip-only button", async () => {
  const { host } = await mount();
  expect(host.textContent).toContain("Chat history");
  expect(host.textContent).toContain("The live session database.");
  expect(host.textContent).toContain("Chats require an explicit cleanup plan.");
  expect(host.querySelector('button[aria-label="About Chat history"]')).toBeNull();
});

test("previewing is its own verb and never writes config", async () => {
  const { requests, bodies, host } = await mount();
  const preview = button(host, (label) => label === "Preview cleanup");
  await act(async () => preview.click());

  expect(requests).toContain("POST /storage/plan");
  expect(requests).not.toContain("PATCH /config");
  expect(requests).not.toContain("POST /storage/execute");
  // Limits and policy are read from the saved config, so the plan request
  // carries neither a target nor the policy flags.
  const planBody = bodies.at(-1)!;
  expect(Object.keys(planBody).sort()).toEqual(["current_session_id", "pinned_session_ids"]);
});

test("a destructive cleanup needs a second click", async () => {
  const { requests, host } = await mount();
  await act(async () => button(host, (label) => label === "Preview cleanup").click());

  const cleanup = button(host, (label) => label.startsWith("Clean up"));
  await act(async () => cleanup.click());
  expect(host.textContent).toContain("Confirm permanent cleanup");
  expect(requests).not.toContain("POST /storage/execute");

  await act(async () => button(host, (label) => label === "Confirm permanent cleanup").click());
  expect(requests).toContain("POST /storage/execute");
  expect(host.textContent).toContain("Preview builds a dry run");
});

test("a failed execute marks the plan stale instead of leaving it actionable", async () => {
  const { host } = await mount(true);
  await act(async () => button(host, (label) => label === "Preview cleanup").click());
  await act(async () => button(host, (label) => label.startsWith("Clean up")).click());
  await act(async () => button(host, (label) => label === "Confirm permanent cleanup").click());

  expect(host.textContent).toContain("stale — preview again");
  expect(host.textContent).toContain("Cleanup didn't run");
  expect(host.textContent).toContain("preview again before running it");
  expect(button(host, (label) => label.startsWith("Clean up"))).toBeUndefined();
  expect(button(host, (label) => label === "Preview again")).toBeDefined();
});

test("each retention switch persists on toggle", async () => {
  const { requests, bodies, host } = await mount();
  const switches = Array.from(host.querySelectorAll('button[role="switch"]'));
  expect(switches.map((el) => el.getAttribute("aria-label"))).toEqual([
    "Include archived chats in cleanup",
    "Delete cold archived chats",
    "Include inactive current chats in cleanup",
  ]);

  for (const control of switches) {
    await act(async () => (control as HTMLButtonElement).click());
  }

  expect(requests.filter((entry) => entry === "PATCH /config")).toHaveLength(3);
  expect(bodies).toEqual([
    { storage_allow_archived_cleanup: false },
    { storage_allow_delete_cold_chats: true },
    { storage_allow_current_cleanup: true },
  ]);
});

test("the space limit saves on its own button and reports a pending edit", async () => {
  const { requests, bodies, host } = await mount();
  const save = () => button(host, (label) => label === "Save changes");
  expect(save().disabled).toBe(true);
  expect(host.textContent).toContain("saved");

  const field = host.querySelector<HTMLInputElement>('input[type="number"]')!;
  await act(async () => {
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
    setter.call(field, "4");
    field.dispatchEvent(new Event("input", { bubbles: true }));
  });

  expect(host.textContent).toContain("unsaved changes");
  expect(save().disabled).toBe(false);
  expect(requests).not.toContain("PATCH /config");

  // happy-dom does not implement implicit form submission from a submit button.
  const form = host.querySelector("form")!;
  await act(async () => {
    form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    await Bun.sleep(0);
  });
  expect(requests).toContain("PATCH /config");
  expect(bodies.at(-1)).toMatchObject({ max_space_gb: 4 });
});
