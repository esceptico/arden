import { afterEach, beforeEach, expect, test } from "bun:test";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { ContextPanel } from "@/features/context/components/ContextPanel";
import { ProofSummary } from "@/features/context/components/ProofSummary";
import { DEFAULT_CONFIG } from "@/api/core";
import { DEFAULT_PREFS } from "@/stores/prefs";
import { getState, setState } from "@/stores";

const roots = new Set<Root>();
const originalDesktop = window.ntrpDesktop;

function setup() {
  const host = document.createElement("div");
  document.body.append(host);
  const root = createRoot(host);
  roots.add(root);
  return { host, root };
}

async function settle() {
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
}

function response(data: unknown, ok = true) {
  return {
    ok,
    status: ok ? 200 : 500,
    statusText: ok ? "OK" : "Error",
    contentType: "application/json",
    data,
    text: ok ? "" : "failed",
  };
}

const inspectorPayload = {
  run_id: "run-1",
  session_id: "session-1",
  updated_at: "2026-07-20T12:00:00Z",
  context_manifest: [{
    context_id: "ctx-1",
    content_type: "memory",
    source: "memory",
    ref: "record:1",
    freshness: "current",
    selection_reason: "Relevant user preference",
    size_bytes: 120,
  }],
  evidence: {
    sources: [{ provider: "gmail", kind: "message", ref: "42", title: "Sent email" }],
    approvals: [{ tool_call_id: "call-1", tool_name: "send_email", status: "approved" }],
    effects: [{ tool_call_id: "call-1", operation: "send", target: "message:42" }],
    receipts: [{ tool_call_id: "call-1", receipt: "provider:42" }],
    checks: [{ tool_call_id: "call-1", postcondition: "message exists", observed: "message:42" }],
    limitations: [],
  },
};

beforeEach(() => {
  setState({
    config: DEFAULT_CONFIG,
    currentSessionId: "session-1",
    messages: new Map([
      ["user-1", { id: "user-1", role: "user", content: "send it" }],
      ["activity-1", {
        id: "activity-1",
        role: "activity",
        content: "",
        activity: {
          label: "Called",
          done: true,
          items: [{
            id: "call-1",
            kind: "send_email",
            displayName: "Send email",
            target: "alice@example.com",
            outcome: {
              status: "succeeded",
              effect: { operation: "send", target: "message:42" },
              verification: { postcondition: "message exists", observed: "message:42" },
              receipt: "provider:42",
            },
            sourceRefs: [{ provider: "gmail", kind: "message", ref: "42", title: "Sent email" }],
          }],
        },
      }],
      ["assistant-1", { id: "assistant-1", role: "assistant", content: "Sent" }],
    ]),
    order: ["user-1", "activity-1", "assistant-1"],
    sourceRefsRevision: 1,
    rightInspectorTab: "activity",
    sourceTurnId: null,
    contextTurnId: null,
    prefs: { ...DEFAULT_PREFS, rightPanelCollapsed: false },
  });
});

afterEach(async () => {
  for (const root of roots) await act(async () => root.unmount());
  roots.clear();
  window.ntrpDesktop = originalDesktop;
  document.body.replaceChildren();
});

test("proof summary is collapsed by default and opens exact-turn Context", async () => {
  const { host, root } = setup();
  await act(async () => root.render(<ProofSummary turnId="user-1" />));

  const toggle = host.querySelector('button[aria-label="Expand outcome evidence"]') as HTMLButtonElement;
  expect(toggle?.getAttribute("aria-expanded")).toBe("false");
  expect(host.textContent).toContain("Evidence recorded");
  expect(host.textContent).toContain("1 action");
  expect(host.textContent).not.toContain("message exists");

  await act(async () => toggle.click());
  expect(host.textContent).toContain("message exists");
  expect(host.textContent).toContain("provider:42");

  const inspect = Array.from(host.querySelectorAll("button")).find((button) => button.textContent === "Inspect context");
  await act(async () => inspect?.click());
  expect(getState().rightInspectorTab).toBe("context");
  expect(getState().contextTurnId).toBe("user-1");
});

test("proof summary renders nothing for a turn without evidence", async () => {
  setState({
    messages: new Map([
      ["user-2", { id: "user-2", role: "user", content: "hello" }],
      ["assistant-2", { id: "assistant-2", role: "assistant", content: "hi" }],
    ]),
    order: ["user-2", "assistant-2"],
  });
  const { host, root } = setup();
  await act(async () => root.render(<ProofSummary turnId="user-2" />));
  expect(host.innerHTML).toBe("");
});

test("Context panel loads exact metadata and hands the turn to Sources", async () => {
  const paths: string[] = [];
  window.ntrpDesktop = { api: { request: async (_config, request) => {
    paths.push(request.path);
    return response(inspectorPayload);
  } } } as Window["ntrpDesktop"];
  setState({ rightInspectorTab: "context", contextTurnId: "user-1" });
  const { host, root } = setup();
  await act(async () => root.render(<ContextPanel />));
  await settle();

  expect(paths).toEqual(["/sessions/session-1/turns/user-1/inspector"]);
  expect(host.textContent).toContain("Context used");
  expect(host.textContent).toContain("Relevant user preference");
  expect(host.textContent).toContain("Outcome evidence");
  expect(host.textContent).toContain("provider:42");

  const sources = Array.from(host.querySelectorAll("button")).find((button) => button.textContent?.includes("View 1 source"));
  await act(async () => sources?.click());
  expect(getState().rightInspectorTab).toBe("sources");
  expect(getState().sourceTurnId).toBe("user-1");
});

test("Context panel keeps missing sidecars quiet and offers retry on request failure", async () => {
  let fail = false;
  window.ntrpDesktop = { api: { request: async () => fail ? response(null, false) : response(null) } } as Window["ntrpDesktop"];
  setState({ rightInspectorTab: "context", contextTurnId: "user-1" });
  const { host, root } = setup();
  await act(async () => root.render(<ContextPanel />));
  await settle();
  expect(host.textContent).toContain("No recorded context or evidence for this turn.");

  fail = true;
  await act(async () => root.render(<ContextPanel key="retry-case" />));
  await settle();
  expect(host.textContent).toContain("Could not load context evidence.");
  expect(Array.from(host.querySelectorAll("button")).some((button) => button.textContent === "Retry")).toBe(true);
});
