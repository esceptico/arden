import { afterEach, expect, test } from "bun:test";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { ApprovalBanner } from "@/features/chat/components/ApprovalBanner";
import { respondToApproval } from "@/actions/approvals";
import { setState } from "@/stores/index";

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
