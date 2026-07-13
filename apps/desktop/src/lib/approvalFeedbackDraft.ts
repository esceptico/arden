const drafts = new Map<string, string>();

export function getApprovalFeedbackDraft(toolId: string) {
  return drafts.get(toolId) ?? "";
}

export function setApprovalFeedbackDraft(toolId: string, value: string) {
  if (value) drafts.set(toolId, value);
  else drafts.delete(toolId);
}

export function clearApprovalFeedbackDraft(toolId: string) {
  drafts.delete(toolId);
}
