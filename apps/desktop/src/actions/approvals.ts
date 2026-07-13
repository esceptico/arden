import { submitToolResult } from "@/api/chat";
import { getState } from "@/stores";
import { clearApprovalFeedbackDraft } from "@/lib/approvalFeedbackDraft";

export async function respondToApproval(
  toolId: string,
  approved: boolean,
  feedback = "",
): Promise<void> {
  const s = getState();
  if (!s.currentRunId) return;
  clearApprovalFeedbackDraft(toolId);
  s.resolvePendingApproval(toolId);
  try {
    await submitToolResult(s.config, {
      run_id: s.currentRunId,
      tool_id: toolId,
      result: feedback,
      approved,
    });
  } catch (error) {
    s.appendMessage({
      id: crypto.randomUUID(),
      role: "error",
      content: error instanceof Error ? error.message : String(error),
    });
  }
}

/** Bulk approve/reject every pending approval. With parallel tool
 *  execution the LLM can spawn N writable tools in one step; this lets
 *  the user resolve them in a single click instead of N.
 *
 *  `feedback` is forwarded to each rejected tool as the rejection
 *  reason — useful when the user typed a message in the composer to
 *  explain *why* they're rejecting (Enter-with-draft path). */
export async function respondToAllApprovals(
  approved: boolean,
  feedback = "",
): Promise<void> {
  const s = getState();
  if (!s.currentRunId) return;
  const pending = [...s.pendingApprovals];
  await Promise.all(pending.map((a) => respondToApproval(a.toolId, approved, feedback)));
}
