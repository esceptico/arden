import { apiWithConfig } from "@/api/core";
import { archiveProjectApi, archiveSessionApi, branchSessionApi, createProjectApi, listProjectsApi, listArchivedSessionsApi, listPrimarySessionsApi, moveSessionToProjectApi, permanentlyDeleteSessionApi, renameSessionApi, updateSessionModelApi, restoreSessionApi, updateProjectApi } from "@/api/sessions";
import type { Project, SessionListItem } from "@/api/types";
import { getState } from "@/stores";
import { fetchGoal } from "@/actions/goals";
import { loadHistory, type LoadHistoryOptions } from "@/actions/history";

export async function switchSession(sessionId: string, historyOptions: LoadHistoryOptions = {}): Promise<void> {
  const s = getState();
  s.setCurrentSession(sessionId);
  // Cache is only a fast visual restore. Always reconcile with canonical
  // history so a stale/corrupted projection cannot survive until Cmd+R.
  await loadHistory(sessionId, historyOptions);
  await fetchGoal(sessionId);
}

/** "New session" as the sidebar nav row / ⌘N / command palette entry mean
 *  it: land on Home rather than eagerly provisioning an empty session.
 *  Home's hero input creates the actual session lazily on first send (see
 *  sendMessage) — this just clears the current session/room so Home's
 *  no-session branch in App.tsx renders. Other createSession() callers
 *  (quick-capture, new-project, in-project "new session" rows) still want
 *  an eagerly-provisioned session and are unaffected. */
export function goToNewSessionHome(): void {
  const s = getState();
  s.setCurrentSession(null);
  s.openSlice(null);
}

export async function createSession(projectId?: string | null): Promise<void> {
  const s = getState();
  const targetProjectId =
    projectId !== undefined
      ? projectId
      : s.currentSessionId
        ? (s.sessions.find((session) => session.session_id === s.currentSessionId)?.project_id ?? null)
        : null;
  const session = await apiWithConfig<SessionListItem>(s.config, "/sessions", {
    method: "POST",
    body: JSON.stringify({ project_id: targetProjectId }),
  });
  s.prependSession(session);
  await switchSession(session.session_id);
}

export async function createProject(): Promise<Project> {
  const s = getState();
  const project = await createProjectApi(s.config, { name: "New slice" });
  s.setProjects([project, ...s.projects]);
  await createSession(project.project_id);
  return project;
}

export async function saveProject(
  projectId: string,
  patch: { name?: string; default_cwd?: string | null; instructions?: string | null; knowledge_scope?: string },
): Promise<Project> {
  const s = getState();
  const project = await updateProjectApi(s.config, projectId, patch);
  s.setProjects(s.projects.map((item) => (item.project_id === project.project_id ? project : item)));
  return project;
}

export async function archiveProject(projectId: string): Promise<void> {
  const s = getState();
  await archiveProjectApi(s.config, projectId);
  s.setProjects(s.projects.filter((project) => project.project_id !== projectId));
  s.setSessions(
    s.sessions.map((session) =>
      session.project_id === projectId ? { ...session, project_id: null } : session,
    ),
  );
}

export async function moveSessionToProject(sessionId: string, projectId: string | null): Promise<void> {
  const s = getState();
  await moveSessionToProjectApi(s.config, sessionId, projectId);
  s.setSessions(
    s.sessions.map((session) =>
      session.session_id === sessionId ? { ...session, project_id: projectId } : session,
    ),
  );
}

export async function refreshProjects(): Promise<void> {
  const s = getState();
  s.setProjects(await listProjectsApi(s.config));
}

export async function refreshSessions(): Promise<void> {
  const s = getState();
  s.setSessions(await listPrimarySessionsApi(s.config));
}

export async function updateSessionModelAction(sessionId: string, chatModel: string): Promise<void> {
  const s = getState();
  s.setSessions(
    s.sessions.map((sess) =>
      sess.session_id === sessionId ? { ...sess, chat_model: chatModel } : sess,
    ),
  );
  await updateSessionModelApi(s.config, sessionId, chatModel);
}

export async function renameSession(sessionId: string, name: string): Promise<void> {
  const s = getState();
  const trimmed = name.trim();
  if (!trimmed) return;
  await renameSessionApi(s.config, sessionId, trimmed);
  s.setSessions(
    s.sessions.map((sess) =>
      sess.session_id === sessionId ? { ...sess, name: trimmed } : sess,
    ),
  );
}

export async function archiveSession(sessionId: string): Promise<void> {
  const s = getState();
  await archiveSessionApi(s.config, sessionId);
  const remaining = s.sessions.filter((sess) => sess.session_id !== sessionId);
  s.setSessions(remaining);
  // A pinned session that's been archived no longer exists in the live list,
  // so drop it from the pins rather than letting the id linger in prefs.
  if (s.prefs.pinnedSessionIds.includes(sessionId)) {
    s.setPref("pinnedSessionIds", s.prefs.pinnedSessionIds.filter((id) => id !== sessionId));
  }
  // Invalidate archived list so the next open re-fetches.
  s.setArchivedSessions(null);
  if (s.currentSessionId === sessionId) {
    if (remaining.length > 0) {
      await switchSession(remaining[0].session_id);
    } else {
      await createSession();
    }
  }
}

export async function fetchArchivedSessions(): Promise<void> {
  const s = getState();
  if (!s.connected) return;
  try {
    const sessions = await listArchivedSessionsApi(s.config);
    s.setArchivedSessions(sessions);
  } catch {
    s.setArchivedSessions([]);
  }
}

export async function restoreArchivedSession(sessionId: string): Promise<void> {
  const s = getState();
  await restoreSessionApi(s.config, sessionId);
  // Move back into the live sessions list — easiest is a refresh of both.
  const archived = s.archivedSessions ?? [];
  s.setArchivedSessions(archived.filter((a) => a.session_id !== sessionId));
  s.setSessions(await listPrimarySessionsApi(s.config));
}

export async function permanentlyDeleteSession(sessionId: string): Promise<void> {
  const s = getState();
  await permanentlyDeleteSessionApi(s.config, sessionId);
  const archived = s.archivedSessions ?? [];
  s.setArchivedSessions(archived.filter((a) => a.session_id !== sessionId));
}

export async function branchAtMessage(messageId: string): Promise<void> {
  const s = getState();
  if (!s.currentSessionId) return;
  const branched = await branchSessionApi(s.config, s.currentSessionId, {
    up_to_message_id: messageId,
  });
  s.setSessions(await listPrimarySessionsApi(s.config));
  await switchSession(branched.session_id);
}
