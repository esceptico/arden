import { afterEach, beforeEach, expect, test } from "bun:test";

import { archiveArea } from "@/actions/sessions";
import type { Area, SessionListItem } from "@/api/types";
import { getState, setState } from "@/stores/index";

const originalWindow = (globalThis as typeof globalThis & { window?: unknown }).window;

beforeEach(() => {
  setState({
    config: { serverUrl: "http://localhost:6877", apiKey: "" },
    areaRecords: [area("p1", "ntrp"), area("p2", "dex")],
    sessions: [
      session("s1", "ntrp bug", "p1"),
      session("s2", "dex bug", "p2"),
      session("s3", "loose note", null),
    ],
  });
});

afterEach(() => {
  (globalThis as typeof globalThis & { window?: unknown }).window = originalWindow;
});

test("archiveArea removes the area and moves its sessions to Inbox locally", async () => {
  const requests: { path: string; method?: string; body?: string; timeout?: number }[] = [];
  (globalThis as typeof globalThis & { window?: unknown }).window = {
    ntrpDesktop: {
      api: {
        request: async (
          _config: unknown,
          req: { path: string; method?: string; body?: string; timeout?: number },
        ) => {
          requests.push(req);
          return {
            ok: true,
            status: 200,
            statusText: "OK",
            contentType: "application/json",
            data: { status: "archived", area_id: "p1" },
            text: "",
          };
        },
      },
    },
  };

  await archiveArea("p1");

  expect(requests).toEqual([{ path: "/areas/p1", method: "DELETE", body: undefined, timeout: 60_000 }]);
  expect(getState().areaRecords.map((p) => p.area_id)).toEqual(["p2"]);
  expect(getState().sessions.map((s) => [s.session_id, s.area_id])).toEqual([
    ["s1", null],
    ["s2", "p2"],
    ["s3", null],
  ]);
});

function area(area_id: string, name: string): Area {
  return {
    area_id,
    name,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    default_cwd: null,
    instructions: null,
    knowledge_scope: `area:${area_id}`,
    archived_at: null,
  };
}

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
