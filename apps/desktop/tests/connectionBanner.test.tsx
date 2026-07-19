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
  delete (window as unknown as { ntrpDesktop?: unknown }).ntrpDesktop;
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
  (window as unknown as { ntrpDesktop: unknown }).ntrpDesktop = {
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

test("Google connection runs OAuth, enables Google, then resolves", async () => {
  const calls: { path: string; body: Record<string, unknown> }[] = [];
  const serverConfig = {
    chat_model: "gpt-5.2",
    research_model: "gpt-5.2",
    memory_model: "gpt-5.2",
    embedding_model: "text-embedding-3-small",
    web_search: "auto",
    web_search_provider: "none",
    google_enabled: true,
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
  (window as unknown as { ntrpDesktop: unknown }).ntrpDesktop = {
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

  expect(calls.map((call) => call.path)).toEqual(["/gmail/add", "/config", "/connections/result"]);
  expect(calls[0].body).toEqual({ service_choice: "email" });
  expect(calls[1].body).toEqual({ integrations: { google: true } });
  expect(getState().pendingConnections).toEqual([]);
});
