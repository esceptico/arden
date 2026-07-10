import { afterEach, beforeEach, expect, test } from "bun:test";

import { archiveArea } from "@/actions/sessions";
import { createAreaOutcome, replyToAsk, updateAreaOutcome, updateAreaWorkItem } from "@/actions/areas";
import type { Area, SessionListItem } from "@/api/types";
import { getState, setState } from "@/stores/index";

const originalWindow = (globalThis as typeof globalThis & { window?: unknown }).window;

beforeEach(() => {
  setState({
    config: { serverUrl: "http://localhost:6877", apiKey: "" },
    areas: {
      ...getState().areas,
      recordsById: { p1: area("p1", "ntrp"), p2: area("p2", "dex") },
      recordOrder: ["p1", "p2"],
    },
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

test("archiveArea removes the area view but preserves session membership for restore", async () => {
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
  expect(getState().areas.recordOrder).toEqual(["p2"]);
  expect(getState().sessions.map((s) => [s.session_id, s.area_id])).toEqual([
    ["s1", "p1"],
    ["s2", "p2"],
    ["s3", null],
  ]);
});

test("replyToAsk targets the typed Custodian reply endpoint", async () => {
  const requests: { path: string; method?: string; body?: string; timeout?: number }[] = [];
  (globalThis as typeof globalThis & { window?: unknown }).window = {
    ntrpDesktop: {
      api: {
        request: async (_config: unknown, req: { path: string; method?: string; body?: string; timeout?: number }) => {
          requests.push(req);
          return {
            ok: true,
            status: 200,
            statusText: "OK",
            contentType: "application/json",
            data: {},
            text: "",
          };
        },
      },
    },
  };

  await replyToAsk("p1", "agent:p1:dose", "Use 5 mg.");

  expect(requests).toEqual([
    {
      path: "/areas/p1/asks/agent%3Ap1%3Adose/reply",
      method: "POST",
      body: JSON.stringify({ message: "Use 5 mg." }),
      timeout: 60_000,
    },
  ]);
});

test("area work actions use encoded typed paths and refresh room plus overview", async () => {
  const requests: { path: string; method?: string; body?: string; timeout?: number }[] = [];
  (globalThis as typeof globalThis & { window?: unknown }).window = {
    ntrpDesktop: {
      api: {
        request: async (_config: unknown, req: { path: string; method?: string; body?: string; timeout?: number }) => {
          requests.push(req);
          return {
            ok: true, status: 200, statusText: "OK", contentType: "application/json",
            data: req.path.endsWith("/overview")
              ? { areas: [], focus: [], brief: { done: [], in_progress: [], needs_you: [] } }
              : req.method
                ? {}
                : {
                    key: "p/1", title: "O-1A", autonomy: "observe", page_path: "topics/o-1a.md",
                    related: [], open_loops: [], updated: "", attention: "ambient", interrupts: "asks",
                    paused: false, agent: null, asks: [], sessions: [], automations: [],
                    work: { outcomes: [], work_items: [], events: [] },
                  },
            text: "",
          };
        },
      },
    },
  };

  await createAreaOutcome("p/1", {
    key: "petition-filed", title: "Petition filed", success_criteria: "Receipt exists", priority: 5,
  });
  await updateAreaOutcome("p/1", "petition-filed", { expected_updated_at: "v1", status: "paused" });
  await updateAreaWorkItem("p/1", "collect evidence", {
    expected_updated_at: "v2", status: "completed",
  });

  const mutations = requests.filter((request) => request.method !== "GET");
  expect(mutations).toEqual([
    {
      path: "/areas/p%2F1/outcomes", method: "POST",
      body: JSON.stringify({
        key: "petition-filed", title: "Petition filed", success_criteria: "Receipt exists", priority: 5,
      }), timeout: 60_000,
    },
    {
      path: "/areas/p%2F1/outcomes/petition-filed", method: "PATCH",
      body: JSON.stringify({ expected_updated_at: "v1", status: "paused" }), timeout: 60_000,
    },
    {
      path: "/areas/p%2F1/work/collect%20evidence", method: "PATCH",
      body: JSON.stringify({ expected_updated_at: "v2", status: "completed" }), timeout: 60_000,
    },
  ]);
  expect(requests.filter((request) => request.method === "GET")).toHaveLength(6);
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
    page_path: null,
    autonomy: null,
    attention: "ambient",
    interrupts: "asks",
    paused_at: null,
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
