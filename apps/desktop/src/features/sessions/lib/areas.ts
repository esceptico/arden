import type { Area, SessionListItem } from "@/api/types";
import type { SidebarGroupBy } from "@/stores/types";

export interface AreaSessionGroup {
  /** Stable key for React + per-group collapse/expand state. */
  key: string;
  /** Header label. */
  label: string;
  /** Set only in area mode — drives the per-group settings/+ row actions. */
  area: Area | null;
  sessions: SessionListItem[];
  /** The synthetic "Pinned" group (rendered with a pin glyph, no area actions). */
  pinned?: boolean;
}

export interface GroupOptions {
  groupBy: SidebarGroupBy;
  unreadOnly: boolean;
  channelsOnly: boolean;
  pinned: Set<string>;
  unread: Set<string>;
  active: Set<string>;
}

export function primarySidebarSessions(sessions: SessionListItem[]): SessionListItem[] {
  return sessions.filter((session) => session.session_type !== "agent");
}

export function groupSessions(
  areas: Area[],
  sessions: SessionListItem[],
  opts: GroupOptions,
): AreaSessionGroup[] {
  const groups: AreaSessionGroup[] = [];

  // Pins are sticky: extract them from the UNFILTERED list so "pin to top"
  // survives an active Unread/Channels filter; filters apply to the remainder.
  const pinned = sessions.filter((s) => opts.pinned.has(s.session_id));
  let rest = sessions.filter((s) => !opts.pinned.has(s.session_id));
  // The sidebar answers "what was I talking about?" — agent/automation
  // channel transcripts are machinery, reachable from their rooms and the
  // Automations modal, and their news reaches the user as asks on Home.
  // The Channels filter is the explicit way to list them here.
  rest = rest.filter((s) => (s.session_type === "channel") === opts.channelsOnly);
  if (opts.unreadOnly) rest = rest.filter((s) => opts.unread.has(s.session_id));

  if (pinned.length) {
    groups.push({ key: "__pinned", label: "Pinned", area: null, sessions: pinned, pinned: true });
  }

  switch (opts.groupBy) {
    case "time":
      groups.push(...groupByTime(rest));
      break;
    case "type":
      groups.push(...groupByType(rest));
      break;
    case "status":
      groups.push(...groupByStatus(rest, opts.active, opts.unread));
      break;
    default:
      groups.push(...groupByArea(areas, rest));
  }
  return groups;
}

function groupByArea(
  areas: Area[],
  sessions: SessionListItem[],
): AreaSessionGroup[] {
  const areaById = new Map(areas.map((p) => [p.area_id, p]));
  const byArea = new Map<string | null, SessionListItem[]>();
  for (const session of sessions) {
    const areaId =
      session.area_id && areaById.has(session.area_id) ? session.area_id : null;
    const bucket = byArea.get(areaId) ?? [];
    bucket.push(session);
    byArea.set(areaId, bucket);
  }

  const groups: AreaSessionGroup[] = [];
  for (const area of areas) {
    const areaSessions = byArea.get(area.area_id) ?? [];
    // A group earns its sidebar row through conversations — an empty area
    // still lives on Home's strip and its room is one click away there.
    if (areaSessions.length) {
      groups.push({ key: area.area_id, label: area.name, area, sessions: areaSessions });
    }
  }
  const inbox = byArea.get(null);
  if (inbox?.length) groups.push({ key: "inbox", label: "Inbox", area: null, sessions: inbox });
  return groups;
}

const TIME_BUCKETS: { key: string; label: string; maxDays: number }[] = [
  { key: "today", label: "Today", maxDays: 1 },
  { key: "week", label: "This week", maxDays: 7 },
  { key: "month", label: "This month", maxDays: 30 },
  { key: "older", label: "Older", maxDays: Infinity },
];

function groupByTime(sessions: SessionListItem[]): AreaSessionGroup[] {
  const now = Date.now();
  const buckets = new Map<string, SessionListItem[]>();
  for (const session of sessions) {
    const days = (now - new Date(session.last_activity).getTime()) / 86_400_000;
    const bucket = TIME_BUCKETS.find((b) => days < b.maxDays)!;
    const list = buckets.get(bucket.key) ?? [];
    list.push(session);
    buckets.set(bucket.key, list);
  }
  return TIME_BUCKETS.filter((b) => buckets.get(b.key)?.length).map((b) => ({
    key: `time:${b.key}`,
    label: b.label,
    area: null,
    sessions: buckets.get(b.key)!,
  }));
}

function groupByType(sessions: SessionListItem[]): AreaSessionGroup[] {
  const chats = sessions.filter((s) => s.session_type !== "channel");
  const channels = sessions.filter((s) => s.session_type === "channel");
  const groups: AreaSessionGroup[] = [];
  if (chats.length) groups.push({ key: "type:chat", label: "Chats", area: null, sessions: chats });
  if (channels.length) groups.push({ key: "type:channel", label: "Channels", area: null, sessions: channels });
  return groups;
}

function groupByStatus(
  sessions: SessionListItem[],
  active: Set<string>,
  unread: Set<string>,
): AreaSessionGroup[] {
  const buckets: Record<"active" | "unread" | "idle", SessionListItem[]> = {
    active: [],
    unread: [],
    idle: [],
  };
  for (const session of sessions) {
    if (active.has(session.session_id)) buckets.active.push(session);
    else if (unread.has(session.session_id)) buckets.unread.push(session);
    else buckets.idle.push(session);
  }
  const groups: AreaSessionGroup[] = [];
  if (buckets.active.length) groups.push({ key: "status:active", label: "Active", area: null, sessions: buckets.active });
  if (buckets.unread.length) groups.push({ key: "status:unread", label: "Unread", area: null, sessions: buckets.unread });
  if (buckets.idle.length) groups.push({ key: "status:idle", label: "Idle", area: null, sessions: buckets.idle });
  return groups;
}
