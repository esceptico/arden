import type { State, UiMessage } from "@/stores";
import {
  workingLabel,
  type WorkingLabel,
} from "@/features/chat/lib/workingLabel";

export interface ForegroundRunLabelInput {
  activityMessage?: UiMessage | null;
  approvalPending?: boolean;
  connectionLabel?: string | null;
}

/** User-facing phase for a live foreground run. User-action waits win over
 *  transport/tool/model phases because they are the next thing that can move
 *  the run forward. Provider-authored thinking prose is deliberately not
 *  accepted here: this is a small typed vocabulary owned by the desktop. */
export function foregroundRunLabel(input: ForegroundRunLabelInput): WorkingLabel {
  if (input.approvalPending) return { verb: "Awaiting", target: "approval" };
  if (input.connectionLabel) return { verb: "Connect", target: input.connectionLabel };
  return workingLabel(input.activityMessage);
}

export function workingLabelText(label: WorkingLabel): string {
  return label.target ? `${label.verb} ${label.target}` : label.verb;
}

export function selectForegroundApprovalCount(state: State): number {
  if (!state.running || !state.currentRunId) return 0;
  return state.pendingApprovals.filter((approval) => {
    if (approval.sessionId && approval.sessionId !== state.currentSessionId) return false;
    return approval.runId === state.currentRunId;
  }).length;
}

export function selectForegroundConnectionLabel(state: State): string | null {
  if (!state.running || !state.currentRunId) return null;
  return state.pendingConnections.find((connection) => connection.runId === state.currentRunId)?.label ?? null;
}
