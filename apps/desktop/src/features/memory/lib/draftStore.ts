const drafts = new Map<string, string>();

export function draftKey(path: string, baseRevision: string): string {
  return `${path}\u0000${baseRevision}`;
}

export function getDraft(path: string, baseRevision: string): string | null {
  return drafts.get(draftKey(path, baseRevision)) ?? null;
}

export function setDraft(path: string, baseRevision: string, content: string): void {
  drafts.set(draftKey(path, baseRevision), content);
}

export function clearDraft(path: string, baseRevision: string): void {
  drafts.delete(draftKey(path, baseRevision));
}

export function clearDrafts(): void {
  drafts.clear();
}
