import { afterEach, expect, test } from "bun:test";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { connectAndResume, declineConnection } from "@/actions/connections";
import type { ServerConfig } from "@/api/types";
import { ConnectionBanner } from "@/features/chat/components/ConnectionBanner";
import { getState, setState, type PendingConnection } from "@/stores";

let root: Root | null = null;

const serverConfig: ServerConfig = {
  chat_model: "gpt-5.2",
  chat_model_max_context: 128_000,
  compaction_token_limit: 100_000,
  compaction_token_trigger: 90_000,
  research_model: "gpt-5.2",
  workflow_model: "gpt-5.2",
  memory_model: "gpt-5.2",
  embedding_model: "text-embedding-3-small",
  web_search: "auto",
  web_search_provider: "none",
  max_depth: 8,
  reasoning_effort: null,
  reasoning_efforts: ["low"],
  model_reasoning_efforts: {},
  compression_threshold: 0.8,
  max_messages: 20,
  compression_keep_ratio: 0.5,
  summary_max_tokens: 500,
  max_space_gb: null,
  storage_backup_retention_days: 14,
  storage_allow_archived_cleanup: false,
  storage_allow_delete_cold_chats: false,
  storage_allow_current_cleanup: false,
  storage_current_inactive_days: 90,
  storage_current_minimum: 100,
  memory_enabled: true,
  integrations: { gmail: { enabled: false } },
  tool_overrides: {},
};

function configWithGmail(enabled: boolean): ServerConfig {
  return { ...serverConfig, integrations: { gmail: { enabled } } };
}

const slackConnection: PendingConnection = {
  runId: "run-1",
  toolId: "call-1",
  integrationId: "slack",
  connectionId: "slack",
  label: "Slack",
  reason: "scope_required",
  detail: "Slack needs more access",
  capability: "Search Slack",
  action: "credentials",
  settingsTab: "integrations",
  requiredScopes: ["channels:history"],
  source: "recovery",
};

afterEach(async () => {
  delete (window as unknown as { ardenDesktop?: unknown }).ardenDesktop;
  await act(async () => {
    setState({ pendingConnections: [], settingsOpen: false, settingsTab: null, serverConfig: null });
    root?.unmount();
  });
  root = null;
  document.body.replaceChildren();
});

test("Slack recovery opens secure integration settings and shows scopes", async () => {
  const host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  setState({ pendingConnections: [slackConnection] });

  await act(async () => root?.render(<ConnectionBanner />));

  expect(host.textContent).toContain("Slack needs attention");
  expect(host.textContent).toContain("channels:history");
  const open = [...host.querySelectorAll("button")].find((button) => button.textContent === "Open settings");
  await act(async () => open?.click());
  expect(getState().settingsOpen).toBe(true);
  expect(getState().settingsTab).toBe("integrations");
});

test("decline sends a separate connection result and removes the card", async () => {
  const calls: { path: string; body: Record<string, unknown> }[] = [];
  (window as unknown as { ardenDesktop: unknown }).ardenDesktop = {
    api: {
      request: async (_config: unknown, request: { path: string; body: string }) => {
        calls.push({ path: request.path, body: JSON.parse(request.body) });
        return { ok: true, status: 200, statusText: "OK", contentType: "application/json", data: {}, text: "" };
      },
    },
  };
  setState({ pendingConnections: [slackConnection] });

  await declineConnection(slackConnection);

  expect(calls).toEqual([
    { path: "/connections/result", body: { run_id: "run-1", tool_id: "call-1", approved: false, result: "not now" } },
  ]);
  expect(getState().pendingConnections).toEqual([]);
});

test("Google Drive recovery reconnects its exact account", async () => {
  const calls: { path: string; body: Record<string, unknown> }[] = [];
  (window as unknown as { ardenDesktop: unknown }).ardenDesktop = {
    api: {
      request: async (_config: unknown, request: { path: string; body?: string }) => {
        calls.push({ path: request.path, body: request.body ? JSON.parse(request.body) : {} });
        const data = request.path === "/google/accounts"
          ? { accounts: [] }
          : request.path === "/config"
            ? { integrations: {} }
            : {};
        return { ok: true, status: 200, statusText: "OK", contentType: "application/json", data, text: "" };
      },
    },
  };
  const drive: PendingConnection = {
    ...slackConnection,
    integrationId: "google_drive",
    connectionId: "google_drive",
    label: "Google Drive",
    reason: "not_configured",
    detail: "Create a spreadsheet",
    capability: "Search, read, create, and edit Google Docs and Sheets",
    action: "oauth",
    requiredScopes: ["https://www.googleapis.com/auth/spreadsheets"],
    source: "suggestion",
    accountRef: "drive-owner@example.test",
  };
  setState({
    pendingConnections: [drive],
    serverConfig: { ...serverConfig, integrations: { google_drive: { enabled: false } } },
  });
  const host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);

  await act(async () => root?.render(<ConnectionBanner />));

  expect(host.textContent).not.toContain("Choose a Google account");
  const addAccountButton = [...host.querySelectorAll("button")].find((button) => button.textContent === "Add account");
  expect(addAccountButton).toBeTruthy();
  await act(async () => addAccountButton?.click());

  const oauthCall = calls.find((call) => call.path === "/google/google_drive/connect");
  expect(oauthCall?.body).toEqual({ account_ref: "drive-owner@example.test" });
  expect(calls.some((call) => call.path === "/google/accounts")).toBe(false);
});

test("Google Drive recovery verifies its exact account", async () => {
  const calls: { path: string; body: Record<string, unknown> }[] = [];
  (window as unknown as { ardenDesktop: unknown }).ardenDesktop = {
    api: {
      request: async (_config: unknown, request: { path: string; body?: string }) => {
        calls.push({ path: request.path, body: request.body ? JSON.parse(request.body) : {} });
        return { ok: true, status: 200, statusText: "OK", contentType: "application/json", data: {}, text: "" };
      },
    },
  };
  const drive: PendingConnection = {
    ...slackConnection,
    integrationId: "google_drive",
    connectionId: "google_drive",
    label: "Google Drive",
    action: "enable",
    accountRef: "drive-owner@example.test",
  };
  setState({
    pendingConnections: [drive],
    serverConfig: { ...serverConfig, integrations: { google_drive: { enabled: true } } },
  });

  await connectAndResume(drive);

  expect(calls).toEqual([{
    path: "/connections/result",
    body: {
      run_id: "run-1",
      tool_id: "call-1",
      approved: true,
      result: "connected",
      account_ref: "drive-owner@example.test",
    },
  }]);
});

test("Google connection runs OAuth, enables Google, then resolves", async () => {
  const calls: { path: string; body: Record<string, unknown> }[] = [];
  (window as unknown as { ardenDesktop: unknown }).ardenDesktop = {
    api: {
      request: async (_config: unknown, request: { path: string; body: string }) => {
        const body = request.body ? JSON.parse(request.body) : {};
        calls.push({ path: request.path, body });
        return {
          ok: true,
          status: 200,
          statusText: "OK",
          contentType: "application/json",
          data: request.path === "/config"
            ? configWithGmail((body.integrations as { gmail: boolean }).gmail)
            : request.path === "/google/gmail/connect"
              ? {
                  status: "connected",
                  account: {
                    id: "internal-account",
                    email: "me@example.test",
                    services: ["gmail"],
                    scopes: ["gmail.readonly"],
                  },
                }
              : {},
          text: "",
        };
      },
    },
  };
  const gmail: PendingConnection = {
    ...slackConnection,
    integrationId: "gmail",
    connectionId: "google",
    label: "Gmail",
    action: "oauth",
    requiredScopes: ["gmail.readonly"],
    accountRef: "me@example.test",
  };
  setState({ pendingConnections: [gmail], serverConfig });

  const host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  await act(async () => root?.render(<ConnectionBanner />));
  expect(host.textContent).toContain("Account: me@example.test");

  await act(async () => connectAndResume(gmail));

  expect(calls.map((call) => call.path)).toEqual(["/google/gmail/connect", "/config", "/connections/result"]);
  expect(calls[0].body).toEqual({ account_ref: "me@example.test" });
  expect(calls[1].body).toEqual({ integrations: { gmail: true } });
  expect(calls[2].body).toEqual({
    run_id: "run-1",
    tool_id: "call-1",
    approved: true,
    result: "connected",
    account_ref: "me@example.test",
  });
  expect(getState().pendingConnections).toEqual([]);
});

test("failed Google verification restores the disabled integration", async () => {
  const calls: { path: string; body: Record<string, unknown> }[] = [];
  (window as unknown as { ardenDesktop: unknown }).ardenDesktop = {
    api: {
      request: async (_config: unknown, request: { path: string; body?: string }) => {
        const body = request.body ? JSON.parse(request.body) : {};
        calls.push({ path: request.path, body });
        if (request.path === "/connections/result") {
          return {
            ok: false,
            status: 424,
            statusText: "Failed Dependency",
            contentType: "application/json",
            data: { detail: "Verification failed." },
            text: "",
          };
        }
        const data = request.path === "/config"
          ? configWithGmail((body.integrations as { gmail: boolean }).gmail)
          : request.path === "/google/gmail/connect"
            ? {
                status: "connected",
                account: {
                  id: "internal-account",
                  email: "me@example.test",
                  services: ["gmail"],
                  scopes: ["gmail.readonly"],
                },
              }
            : {};
        return { ok: true, status: 200, statusText: "OK", contentType: "application/json", data, text: "" };
      },
    },
  };
  const gmail: PendingConnection = {
    ...slackConnection,
    integrationId: "gmail",
    connectionId: "google",
    label: "Gmail",
    action: "oauth",
    requiredScopes: ["gmail.readonly"],
    accountRef: "me@example.test",
  };
  setState({ pendingConnections: [gmail], serverConfig });

  await expect(connectAndResume(gmail)).rejects.toThrow("Verification failed.");

  expect(calls.map((call) => call.path)).toEqual([
    "/google/gmail/connect",
    "/config",
    "/connections/result",
    "/config",
  ]);
  expect(calls[0].body).toEqual({ account_ref: "me@example.test" });
  expect(calls[1].body).toEqual({ integrations: { gmail: true } });
  expect(calls[3].body).toEqual({ integrations: { gmail: false } });
  expect(getState().pendingConnections).toEqual([gmail]);
  expect(getState().serverConfig?.integrations.gmail?.enabled).toBe(false);
});

test("ambiguous verification failure preserves the enabled integration", async () => {
  const calls: { path: string; body: Record<string, unknown> }[] = [];
  (window as unknown as { ardenDesktop: unknown }).ardenDesktop = {
    api: {
      request: async (_config: unknown, request: { path: string; body?: string }) => {
        const body = request.body ? JSON.parse(request.body) : {};
        calls.push({ path: request.path, body });
        if (request.path === "/connections/result") throw new Error("network lost");
        const data = request.path === "/config"
          ? configWithGmail((body.integrations as { gmail: boolean }).gmail)
          : request.path === "/google/gmail/connect"
            ? {
                status: "connected",
                account: {
                  id: "internal-account",
                  email: "me@example.test",
                  services: ["gmail"],
                  scopes: ["gmail.readonly"],
                },
              }
            : {};
        return { ok: true, status: 200, statusText: "OK", contentType: "application/json", data, text: "" };
      },
    },
  };
  const gmail: PendingConnection = {
    ...slackConnection,
    integrationId: "gmail",
    connectionId: "google",
    label: "Gmail",
    action: "oauth",
    accountRef: "me@example.test",
  };
  setState({ pendingConnections: [gmail], serverConfig });

  await expect(connectAndResume(gmail)).rejects.toThrow("network lost");

  expect(calls.map((call) => call.path)).toEqual([
    "/google/gmail/connect",
    "/config",
    "/connections/result",
  ]);
  expect(getState().pendingConnections).toEqual([gmail]);
  expect(getState().serverConfig?.integrations.gmail?.enabled).toBe(true);
});
