import { afterEach, expect, test } from "bun:test";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { connectAndResume, declineConnection } from "@/actions/connections";
import { ConnectionBanner } from "@/features/chat/components/ConnectionBanner";
import { getState, setState, type PendingConnection } from "@/stores";

let root: Root | null = null;

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
    setState({ pendingConnections: [], settingsOpen: false, settingsTab: null });
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

test("Google connection card delegates account choice to Google", async () => {
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
  };
  setState({ pendingConnections: [drive] });
  const host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);

  await act(async () => root?.render(<ConnectionBanner />));

  expect(host.textContent).not.toContain("Choose a Google account");
  const addAccountButton = [...host.querySelectorAll("button")].find((button) => button.textContent === "Add account");
  expect(addAccountButton).toBeTruthy();
  await act(async () => addAccountButton?.click());

  const oauthCall = calls.find((call) => call.path === "/google/google_drive/connect");
  expect(oauthCall?.body).toEqual({});
  expect(calls.some((call) => call.path === "/google/accounts")).toBe(false);
});

test("Google connection runs OAuth, enables Google, then resolves", async () => {
  const calls: { path: string; body: Record<string, unknown> }[] = [];
  const serverConfig = {
    chat_model: "gpt-5.2",
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
    compaction_token_limit: 1000,
    compaction_token_trigger: 900,
    max_messages: 20,
    compression_keep_ratio: 0.5,
    summary_max_tokens: 500,
    consolidation_interval: 60,
    memory_enabled: true,
    integrations: {},
  };
  (window as unknown as { ardenDesktop: unknown }).ardenDesktop = {
    api: {
      request: async (_config: unknown, request: { path: string; body: string }) => {
        calls.push({ path: request.path, body: request.body ? JSON.parse(request.body) : {} });
        return {
          ok: true,
          status: 200,
          statusText: "OK",
          contentType: "application/json",
          data: request.path === "/config" ? serverConfig : {},
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
  };
  setState({ pendingConnections: [gmail] });

  await connectAndResume(gmail);

  expect(calls.map((call) => call.path)).toEqual(["/google/gmail/connect", "/config", "/connections/result"]);
  expect(calls[0].body).toEqual({});
  expect(calls[1].body).toEqual({ integrations: { gmail: true } });
  expect(getState().pendingConnections).toEqual([]);
});
