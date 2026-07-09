import { createProjectApi, moveSessionToSliceApi, triageSessionApi } from "@/api/sessions";
import { moveSessionToProject, refreshProjects } from "@/actions/sessions";
import { getState } from "@/stores";

/** Fire once when a just-started, unfiled, top-level chat gets its first
 *  assistant reply: ask the server where it belongs and, if there is a
 *  confident answer, surface a filing proposal. Silent on none/failure. The
 *  `seen` mark is set BEFORE the call so a slow round-trip can't double-fire. */
export async function maybeTriageChat(sessionId: string): Promise<void> {
  const s = getState();
  if (s.triage.seen.has(sessionId)) return;
  const session = s.sessions.find((x) => x.session_id === sessionId);
  if (!session) return;
  if (session.slice_key || session.project_id) return; // already filed
  if (session.session_type === "agent" || session.parent_session_id) return; // not top-level
  const hasAssistantReply = s.order.some((id) => s.messages.get(id)?.role === "assistant");
  if (!hasAssistantReply) return;

  s.markTriageSeen(sessionId);
  try {
    const decision = await triageSessionApi(s.config, sessionId);
    if (decision.decision !== "none") getState().setTriageProposal(sessionId, decision);
  } catch {
    /* additive feature — a failed triage surfaces nothing */
  }
}

/** Run the proposed filing. Keeps the chip on failure (toast + no clear) so the
 *  user can retry; the ChatHeader breadcrumb picks up the new home on success. */
export async function acceptTriage(sessionId: string): Promise<void> {
  const s = getState();
  const decision = s.triage.proposalBySession[sessionId];
  if (!decision) return;
  try {
    if (decision.decision === "move" && decision.target) {
      const { kind, key } = decision.target;
      if (kind === "slice") {
        const moved = await moveSessionToSliceApi(s.config, sessionId, key);
        getState().setSessions(
          getState().sessions.map((x) =>
            x.session_id === sessionId
              ? { ...x, slice_key: moved.slice_key, project_id: moved.project_id }
              : x,
          ),
        );
        // The server mints the slice's backing project on first filing —
        // pull the projects list when it hands back one we don't know, or
        // the sidebar can't render the new group and the chat "vanishes"
        // into Inbox (the venlafaxine bug).
        if (moved.project_id && !getState().projects.some((p) => p.project_id === moved.project_id)) {
          await refreshProjects();
        }
      } else {
        await moveSessionToProject(sessionId, key);
      }
    } else if (decision.decision === "create" && decision.new_title) {
      const project = await createProjectApi(s.config, { name: decision.new_title });
      getState().setProjects([project, ...getState().projects]);
      await moveSessionToProject(sessionId, project.project_id);
    }
    getState().clearTriageProposal(sessionId);
  } catch {
    getState().pushToast({
      id: `triage-fail:${sessionId}`,
      title: "Couldn’t file this chat",
      status: "failed",
      target: { kind: "session", sessionId },
    });
  }
}

export function dismissTriage(sessionId: string): void {
  getState().clearTriageProposal(sessionId);
}
