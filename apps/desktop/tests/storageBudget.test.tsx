import { afterEach, expect, test } from "bun:test";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { ArchiveTab } from "@/features/settings/components/ArchiveTab";
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
      reclaimable_bytes: 300,
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

test("storage budget previews before executing and confirms destructive cleanup", async () => {
  const requests: string[] = [];
  window.ardenDesktop = {
    api: {
      request: async (_config, request) => {
        requests.push(`${request.method ?? "GET"} ${request.path}`);
        if (request.path === "/storage/status") return response(storageStatus);
        if (request.path === "/storage/backups") return response([]);
        if (request.path === "/sessions/archived") return response({ sessions: [] });
        if (request.path === "/config") return response(serverConfig);
        if (request.path === "/storage/plan") {
          return response({
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
          });
        }
        if (request.path === "/storage/execute") {
          return response({
            plan_id: "a".repeat(64),
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
    root?.render(<ArchiveTab />);
    await Bun.sleep(0);
  });

  expect(host.textContent).toContain("Chat history");
  expect(host.textContent).toContain("not currently reclaimable");
  expect(host.querySelector('button[aria-label="About Chat history"]')).not.toBeNull();
  const preview = Array.from(host.querySelectorAll("button")).find((button) => button.textContent === "Save & preview")!;
  await act(async () => preview.click());
  expect(requests).toContain("POST /storage/plan");
  expect(requests).not.toContain("POST /storage/execute");

  const cleanup = Array.from(host.querySelectorAll("button")).find((button) => button.textContent?.startsWith("Clean up"))!;
  await act(async () => cleanup.click());
  expect(host.textContent).toContain("Confirm permanent cleanup");
  expect(requests).not.toContain("POST /storage/execute");
  const confirm = Array.from(host.querySelectorAll("button")).find(
    (button) => button.textContent === "Confirm permanent cleanup",
  )!;
  await act(async () => confirm.click());
  expect(requests).toContain("POST /storage/execute");
});
