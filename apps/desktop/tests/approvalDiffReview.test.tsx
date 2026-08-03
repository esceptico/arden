import { afterEach, expect, test } from "bun:test";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { ApprovalReviewModal } from "@/features/chat/components/ApprovalReviewModal";
import { setState } from "@/stores/index";

let root: Root | null = null;

afterEach(async () => {
  await act(async () => {
    setState({ pendingApprovals: [], reviewingApprovalToolId: null });
    root?.unmount();
  });
  root = null;
  document.body.replaceChildren();
});

test("approval file diffs use the compact mockup patch surface", async () => {
  const portal = document.createElement("div");
  portal.id = "app";
  document.body.append(portal);
  const host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  setState({
    currentRunId: "run-1",
    reviewingApprovalToolId: "tool-1",
    pendingApprovals: [{
      toolId: "tool-1",
      toolName: "file_write",
      path: "notes/a.md",
      status: "pending",
      diff: "--- a/notes/a.md\n+++ b/notes/a.md\n@@ -1 +1 @@\n-old\n+new",
    }],
  });

  await act(async () => root?.render(<ApprovalReviewModal />));
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });

  const review = document.querySelector("[data-approval-diff]");
  expect(review).toBeTruthy();
  expect(review?.textContent).toContain("--- a/notes/a.md");
  expect(review?.textContent).toContain("+new");
  const sheet = document.querySelector<HTMLElement>('[role="dialog"]');
  expect(sheet?.className).toContain("surface-bottom-sheet");
  expect(sheet?.textContent).toContain("Review file changes");
  expect(sheet?.textContent).toContain("Awaiting approval");
  expect(sheet?.textContent).toContain("Deny");
  expect(sheet?.textContent).toContain("Approve changes");
  expect(review?.className).toContain("arden-code-well");
  expect(review?.className).not.toContain("--r-row");
  expect(document.querySelector('[role="tablist"]')).toBeNull();
});
