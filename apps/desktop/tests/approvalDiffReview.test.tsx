import { afterEach, expect, test } from "bun:test";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { ApprovalReviewModal } from "@/features/chat/components/ApprovalReviewModal";
import { setState } from "@/stores/index";
import { clearApprovalFeedbackDraft, setApprovalFeedbackDraft } from "@/lib/approvalFeedbackDraft";

let root: Root | null = null;

afterEach(async () => {
  setState({ pendingApprovals: [], reviewingApprovalToolId: null });
  clearApprovalFeedbackDraft("tool-1");
  if (root) await act(async () => root?.unmount());
  root = null;
  document.body.replaceChildren();
});

test("approval file diffs reuse raw DiffReview without memory effects", async () => {
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
      toolName: "write_file",
      path: "notes/a.md",
      status: "pending",
      diff: "--- a/notes/a.md\n+++ b/notes/a.md\n@@ -1 +1 @@\n-old\n+new",
    }],
  });
  setApprovalFeedbackDraft("tool-1", "use the API instead");

  await act(async () => root?.render(<ApprovalReviewModal />));
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });

  const review = document.querySelector("[data-diff-review]");
  expect(review).toBeTruthy();
  expect(review?.getAttribute("data-layout")).toBe("stacked");
  expect(review?.textContent).toContain("--- a/notes/a.md");
  expect(review?.textContent).toContain("+new");
  expect(document.querySelector("[data-memory-effects-scroll]")).toBeNull();
  expect(document.querySelector('[role="tablist"]')).toBeNull();
  expect(document.querySelector<HTMLInputElement>('input[aria-label="Rejection reason"]')?.value).toBe("use the API instead");
});
