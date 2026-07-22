import { expect, test } from "bun:test";

import { groupSessions, primarySidebarSessions, type GroupOptions } from "@/features/sessions/lib/areas";
import type { Area, SessionListItem } from "@/api/types";

const areas: Area[] = [
  {
    area_id: "p1",
    name: "arden",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    default_cwd: "/repo",
    instructions: "Keep it small.",
    knowledge_scope: "area:p1",
    archived_at: null,
  },
  {
    area_id: "p2",
    name: "dex",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    default_cwd: null,
    instructions: null,
    knowledge_scope: "area:p2",
    archived_at: null,
  },
];

const sessions: SessionListItem[] = [
  session("s1", "arden bug", "p1"),
  session("s2", "loose note", null),
  session("s3", "dex review", "p2"),
];

function opts(overrides: Partial<GroupOptions> = {}): GroupOptions {
  return {
    groupBy: "area",
    unreadOnly: false,
    channelsOnly: false,
    pinned: new Set(),
    unread: new Set(),
    active: new Set(),
    ...overrides,
  };
}

test("area grouping keeps inbox and area folders separate", () => {
  expect(groupSessions(areas, sessions, opts())).toEqual([
    { key: "p1", label: "arden", area: areas[0], sessions: [sessions[0]] },
    { key: "p2", label: "dex", area: areas[1], sessions: [sessions[2]] },
    { key: "inbox", label: "Inbox", area: null, sessions: [sessions[1]] },
  ]);
});

test("empty area folders are dropped — a group earns its row through conversations", () => {
  // dex has no chats: no sidebar group (the area still lives on Home's strip).
  expect(groupSessions(areas, [sessions[0]], opts())).toEqual([
    { key: "p1", label: "arden", area: areas[0], sessions: [sessions[0]] },
  ]);
});

test("a filter drops empty area folders", () => {
  // unread filter that matches only s1 -> p2 (empty) should not appear
  const groups = groupSessions(areas, sessions, opts({ unreadOnly: true, unread: new Set(["s1"]) }));
  expect(groups).toEqual([
    { key: "p1", label: "arden", area: areas[0], sessions: [sessions[0]] },
  ]);
});

test("pinned sessions lift into a Pinned group and leave their normal group", () => {
  const groups = groupSessions(areas, sessions, opts({ pinned: new Set(["s1"]) }));
  expect(groups).toEqual([
    { key: "__pinned", label: "Pinned", area: null, sessions: [sessions[0]], pinned: true },
    // p1 emptied by the pin lift → no group row (its chat is right there in Pinned).
    { key: "p2", label: "dex", area: areas[1], sessions: [sessions[2]] },
    { key: "inbox", label: "Inbox", area: null, sessions: [sessions[1]] },
  ]);
});

test("pins survive an active filter (sticky pin-to-top)", () => {
  // channelsOnly would hide s1 (a chat), but it is pinned, so it stays.
  const groups = groupSessions(areas, sessions, opts({ channelsOnly: true, pinned: new Set(["s1"]) }));
  expect(groups).toEqual([
    { key: "__pinned", label: "Pinned", area: null, sessions: [sessions[0]], pinned: true },
  ]);
});

test("channels are machinery: hidden by default, listed under the Channels filter", () => {
  const channel = { ...session("c1", "alerts", null), session_type: "channel" as const };
  // Default view: the channel transcript never appears, in any grouping.
  const byType = groupSessions(areas, [sessions[0], channel], opts({ groupBy: "type" }));
  expect(byType.map((g) => [g.key, g.sessions.map((s) => s.session_id)])).toEqual([
    ["type:chat", ["s1"]],
  ]);
  // Channels filter: the explicit way to list them (and only them).
  const channelsView = groupSessions(areas, [sessions[0], channel], opts({ channelsOnly: true }));
  expect(channelsView.map((g) => [g.key, g.sessions.map((s) => s.session_id)])).toEqual([
    ["inbox", ["c1"]],
  ]);
});

test("group by status assigns each session to one bucket by priority", () => {
  // s1 active, s2 unread, s3 idle — active beats unread beats idle, no double-count.
  const groups = groupSessions(
    areas,
    sessions,
    opts({ groupBy: "status", active: new Set(["s1"]), unread: new Set(["s2"]) }),
  );
  expect(groups.map((g) => [g.key, g.sessions.map((s) => s.session_id)])).toEqual([
    ["status:active", ["s1"]],
    ["status:unread", ["s2"]],
    ["status:idle", ["s3"]],
  ]);
});

test("primary sidebar excludes child agent sessions", () => {
  const chat = session("chat", "main chat", "p1");
  const agent = {
    ...session("agent", "research child", "p1"),
    session_type: "agent" as const,
    parent_session_id: "chat",
  };

  expect(primarySidebarSessions([chat, agent])).toEqual([chat]);
});

function session(session_id: string, name: string, area_id: string | null): SessionListItem {
  return {
    session_id,
    name,
    area_id,
    started_at: "2026-01-01T00:00:00Z",
    last_activity: "2026-01-01T00:00:00Z",
    message_count: 1,
  };
}
