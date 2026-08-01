import { submitToolResult } from "@/api/chat";
import { ApiError } from "@/api/core";
import { getState } from "@/stores";
import { clearApprovalFeedbackDraft } from "@/lib/approvalFeedbackDraft";

/** Resolving an approval is idempotent from the user's side: the intent is
 *  "this tool call should stop waiting on me". The server answers 409 when it
 *  already has — a second click, another surface (banner / review sheet /
 *  approve-all / ⌘↩) winning the race, or a banner replayed from history for
 *  a run that resolved long ago. The transcript already shows the real
 *  outcome over SSE, so a red error bubble adds noise, not information.
 *  Everything else still surfaces. */
function isAlreadyResolved(error: unknown): boolean {
  return error instanceof ApiError && error.status === 409;
}

export async function respondToApproval(
  toolId: string,
  approved: boolean,
  feedback = "",
): Promise<void> {
  const s = getState();
  // The approval carries the run that resolves it — for a child agent's card
  // that is the PARENT run id, which may differ from the session the card was
  // seen in. The global currentRunId is only the fallback for events recorded
  // before attribution existed.
  const approval = s.pendingApprovals.find((a) => a.toolId === toolId);
  const runId = approval?.runId ?? s.currentRunId;
  if (!runId) return;
  clearApprovalFeedbackDraft(toolId);
  s.resolvePendingApproval(toolId);
  try {
    await submitToolResult(s.config, {
      run_id: runId,
      tool_id: toolId,
      result: feedback,
      approved,
    });
  } catch (error) {
    if (isAlreadyResolved(error)) return;
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
  const pending = [...s.pendingApprovals];
  await Promise.all(pending.map((a) => respondToApproval(a.toolId, approved, feedback)));
}
