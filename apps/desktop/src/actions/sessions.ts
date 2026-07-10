import { apiWithConfig } from "@/api/core";
import { archiveAreaApi, archiveSessionApi, branchSessionApi, createAreaApi, listAreasApi, listArchivedSessionsApi, listPrimarySessionsApi, moveSessionToAreaApi, permanentlyDeleteSessionApi, renameSessionApi, updateSessionModelApi, restoreSessionApi, updateAreaApi } from "@/api/sessions";
import type { Area, SessionListItem } from "@/api/types";
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
 *  (quick-capture, new-area, in-area "new session" rows) still want
 *  an eagerly-provisioned session and are unaffected. */
export function goToNewSessionHome(): void {
  const s = getState();
  s.setCurrentSession(null);
  s.openArea(null);
}

export async function createSession(areaId?: string | null): Promise<void> {
  const s = getState();
  const targetAreaId =
    areaId !== undefined
      ? areaId
      : s.currentSessionId
        ? (s.sessions.find((session) => session.session_id === s.currentSessionId)?.area_id ?? null)
        : null;
  const session = await apiWithConfig<SessionListItem>(s.config, "/sessions", {
    method: "POST",
    body: JSON.stringify({ area_id: targetAreaId }),
  });
  s.prependSession(session);
  await switchSession(session.session_id);
}

export async function createArea(): Promise<Area> {
  const s = getState();
  const area = await createAreaApi(s.config, { name: "New area" });
  s.upsertAreaRecord(area);
  await createSession(area.area_id);
  return area;
}

export async function saveArea(
  areaId: string,
  patch: { name?: string; default_cwd?: string | null; instructions?: string | null; knowledge_scope?: string },
): Promise<Area> {
  const s = getState();
  const area = await updateAreaApi(s.config, areaId, patch);
  s.upsertAreaRecord(area);
  return area;
}

export async function archiveArea(areaId: string): Promise<void> {
  const s = getState();
  await archiveAreaApi(s.config, areaId);
  s.archiveAreaRecord(areaId);
}

export async function moveSessionToArea(sessionId: string, areaId: string | null): Promise<void> {
  const s = getState();
  await moveSessionToAreaApi(s.config, sessionId, areaId);
  s.setSessions(
    s.sessions.map((session) =>
      session.session_id === sessionId ? { ...session, area_id: areaId } : session,
    ),
  );
}

export async function refreshAreas(): Promise<void> {
  const s = getState();
  s.setAreaRecords(await listAreasApi(s.config));
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
