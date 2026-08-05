import { beforeEach, expect, test } from "bun:test";
import { handleServerEvent, resetStreamStateForTest } from "@/hooks/useEvents";
import { getState, setState } from "@/stores";
import { pendingConnectionsFromRuntime } from "@/stores/history-response";

beforeEach(() => {
  resetStreamStateForTest();
  setState({ currentSessionId: "session-1", pendingConnections: [] });
});

test("connection_needed creates a native integration card", () => {
  handleServerEvent({
    type: "connection_needed",
    session_id: "session-1",
    run_id: "run-1",
    seq: 1,
    tool_id: "call-1",
    integration_id: "gmail",
    connection_id: "google",
    label: "Gmail",
    reason: "auth_required",
    detail: "Reconnect Gmail",
    capability: "Search and send email",
    action: "oauth",
    settings_tab: "integrations",
    required_scopes: ["gmail.readonly"],
    source: "recovery",
    account_ref: "me@example.test",
  });

  expect(getState().pendingConnections).toEqual([
    expect.objectContaining({
      runId: "run-1",
      toolId: "call-1",
      integrationId: "gmail",
      source: "recovery",
      accountRef: "me@example.test",
    }),
  ]);
});

test("resolving a connection removes only its card", () => {
  setState({
    pendingConnections: [
      {
        runId: "run-1",
        toolId: "call-1",
        integrationId: "gmail",
        connectionId: "google",
        label: "Gmail",
        reason: "auth_required",
        detail: "Reconnect Gmail",
        capability: "Search email",
        action: "oauth",
        settingsTab: "integrations",
        requiredScopes: [],
        source: "recovery",
      },
    ],
  });

  getState().resolvePendingConnection("call-1");

  expect(getState().pendingConnections).toEqual([]);
});

test("terminal run clears an expired connection card", () => {
  handleServerEvent({ type: "RUN_STARTED", session_id: "session-1", run_id: "run-1", seq: 1 });
  handleServerEvent({
    type: "connection_needed",
    session_id: "session-1",
    run_id: "run-1",
    seq: 2,
    tool_id: "call-1",
    integration_id: "gmail",
    connection_id: "google",
    label: "Gmail",
    reason: "auth_required",
    detail: "Reconnect Gmail",
    capability: "Search email",
    action: "oauth",
    settings_tab: "integrations",
    required_scopes: [],
    source: "recovery",
  });
  handleServerEvent({ type: "RUN_FINISHED", session_id: "session-1", run_id: "run-1", seq: 3 });

  expect(getState().pendingConnections).toEqual([]);
});

test("runtime snapshot restores a pending connection after restart", () => {
  const restored = pendingConnectionsFromRuntime({
    session_id: "session-1",
    latest_event_seq: 4,
    checkpoint_seq: 4,
    active_run: null,
    pending_approvals: [],
    pending_connections: [{
      tool_id: "call-1",
      run_id: "run-1",
      integration_id: "slack",
      connection_id: "slack",
      label: "Slack",
      reason: "auth_required",
      detail: "Reconnect Slack",
      capability: "Search Slack",
      action: "credentials",
      settings_tab: "integrations",
      required_scopes: [],
      source: "recovery",
      account_ref: "me@example.test",
    }],
  }, true);

  expect(restored).toEqual([
    expect.objectContaining({ toolId: "call-1", runId: "run-1", accountRef: "me@example.test" }),
  ]);
});
