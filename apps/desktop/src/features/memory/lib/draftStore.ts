const LIMIT = 50;
const drafts = new Map<string, string>();

export function draftKey(path: string, baseRevision: string): string {
  return `${path}\u0000${baseRevision}`;
}

export function getDraft(path: string, baseRevision: string): string | null {
  const key = draftKey(path, baseRevision);
  const value = drafts.get(key);
  if (value === undefined) return null;
  drafts.delete(key);
  drafts.set(key, value);
  return value;
}

export function setDraft(path: string, baseRevision: string, content: string): void {
  const key = draftKey(path, baseRevision);
  drafts.delete(key);
  drafts.set(key, content);
  while (drafts.size > LIMIT) {
    const oldest = drafts.keys().next().value;
    if (oldest == null) break;
    drafts.delete(oldest);
  }
}

export function clearDraft(path: string, baseRevision: string): void {
  drafts.delete(draftKey(path, baseRevision));
}

export function clearDraftIfMatches(path: string, baseRevision: string, expectedContent: string): boolean {
  const key = draftKey(path, baseRevision);
  if (drafts.get(key) !== expectedContent) return false;
  return drafts.delete(key);
}

export function clearDrafts(): void {
  drafts.clear();
}
