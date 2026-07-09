import type { TriageDecision } from "@/api/sessions";

/** Ephemeral chat-filing proposals. `seen` gates the once-per-session trigger
 *  (a chat is triaged once per app-session, whether it was proposed, filed, or
 *  dismissed); `proposalBySession` holds the live proposal for the chip. Both
 *  reset on restart — triage is a live-trigger feature, nothing persists. */
export interface TriageDomainState {
  proposalBySession: Record<string, TriageDecision>;
  seen: Set<string>;
}

export function createTriageDomainState(): TriageDomainState {
  return { proposalBySession: {}, seen: new Set() };
}

export function reduceTriageSeen(state: TriageDomainState, sessionId: string): TriageDomainState {
  return { ...state, seen: new Set(state.seen).add(sessionId) };
}

export function reduceTriageProposal(
  state: TriageDomainState,
  sessionId: string,
  decision: TriageDecision,
): TriageDomainState {
  return { ...state, proposalBySession: { ...state.proposalBySession, [sessionId]: decision } };
}

export function reduceTriageCleared(state: TriageDomainState, sessionId: string): TriageDomainState {
  const proposalBySession = { ...state.proposalBySession };
  delete proposalBySession[sessionId];
  return { ...state, proposalBySession };
}
