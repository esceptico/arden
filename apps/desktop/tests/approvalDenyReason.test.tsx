import { afterEach, expect, test } from "bun:test";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { ApprovalBanner } from "@/features/chat/components/ApprovalBanner";
import { respondToApproval } from "@/actions/approvals";
import { getState, setState } from "@/stores/index";

afterEach(() => {
  delete (globalThis.window as unknown as { ardenDesktop?: unknown }).ardenDesktop;
});

function stubApi(calls: { path: string; body: unknown }[]) {
  (globalThis.window as unknown as { ardenDesktop: unknown }).ardenDesktop = {
    api: {
      request: async (_cfg: unknown, req: { path: string; body?: string }) => {
        calls.push({ path: req.path, body: req.body ? JSON.parse(req.body) : null });
        return { ok: true, status: 200, statusText: "OK", contentType: "application/json", data: {}, text: "" };
      },
    },
  };
}

// The reason a user types in the deny field must reach the backend as the
// tool's rejection feedback (which it already turns into agent guidance).
test("respondToApproval forwards the deny reason as the tool result + approved:false", async () => {
  const calls: { path: string; body: unknown }[] = [];
  stubApi(calls);
  setState({
    config: { serverUrl: "http://localhost:6877", apiKey: "" },
    currentRunId: "run-1",
    pendingApprovals: [{ toolId: "tool-1", toolName: "write_file", status: "pending" }],
  });

  await respondToApproval("tool-1", false, "use the API, not a raw file write");

  const toolResult = calls.find((c) => c.path === "/tools/result");
  expect(toolResult).toBeTruthy();
  expect(toolResult!.body).toMatchObject({
    tool_id: "tool-1",
    approved: false,
    result: "use the API, not a raw file write",
  });
});

// A 409 means the approval is already resolved — the user's intent is met.
// It must NOT land a red error bubble in the transcript; every other failure
// still does.
test("respondToApproval swallows an already-resolved 409 but still reports real failures", async () => {
  const statuses = [409, 500];
  (globalThis.window as unknown as { ardenDesktop: unknown }).ardenDesktop = {
    api: {
      request: async () => {
        const status = statuses.shift()!;
        return {
          ok: false,
          status,
          statusText: status === 409 ? "Conflict" : "Server Error",
          contentType: "application/json",
          data: { detail: status === 409 ? "Approval already resolved" : "boom" },
          text: "",
        };
      },
    },
  };
  setState({
    config: { serverUrl: "http://localhost:6877", apiKey: "" },
    currentRunId: "run-1",
    pendingApprovals: [
      { toolId: "tool-1", toolName: "write_file", status: "pending" },
      { toolId: "tool-2", toolName: "write_file", status: "pending" },
    ],
    messages: new Map(),
    order: [],
  });

  await respondToApproval("tool-1", true);
  const afterConflict = [...getState().messages.values()].filter((m) => m.role === "error");
  expect(afterConflict).toHaveLength(0);

  await respondToApproval("tool-2", true);
  const afterFailure = [...getState().messages.values()].filter((m) => m.role === "error");
  expect(afterFailure).toHaveLength(1);
  expect(afterFailure[0].content).toContain("boom");
});

// The deny-with-reason toggle is present on a pending approval and reveals
// the reason input when clicked.
test("ApprovalBanner exposes a deny-with-reason input", async () => {
  stubApi([]);
  const rootEl = document.createElement("div");
  document.body.append(rootEl);
  const root = createRoot(rootEl);

  setState({
    config: { serverUrl: "http://localhost:6877", apiKey: "" },
    currentRunId: "run-1",
    reviewingApprovalToolId: null,
    pendingApprovals: [{ toolId: "tool-1", toolName: "write_file", status: "pending" }],
  });

  await act(async () => {
    root.render(<ApprovalBanner />);
  });

  const toggle = document.querySelector('[aria-label="Deny with reason"]');
  expect(toggle).toBeTruthy();
  expect(document.querySelector('input[placeholder^="Why"]')).toBeNull();

  await act(async () => {
    toggle!.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
  expect(document.querySelector('input[placeholder^="Why"]')).toBeTruthy();

  // Clear the approval before unmount so the deck is empty (avoids the
  // keydown-effect cleanup racing the test teardown).
  setState({ pendingApprovals: [] });
  await act(async () => {
    root.unmount();
  });
  rootEl.remove();
});
