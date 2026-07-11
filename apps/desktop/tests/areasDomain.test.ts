import { expect, test } from "bun:test";
import { getState } from "@/stores/index";
import {
  createAreasDomainState,
  reduceOverviewLoaded,
  reduceAskResolved,
  reduceOpenArea,
  reduceDetailLoaded,
  reduceRecordsLoaded,
  reduceRecordUpserted,
  reduceRecordArchived,
} from "@/stores/areas-domain";
import type { Area } from "@/api/types";

const ask = {
  id: "a1",
  area_key: "o-1a",
  text: "t",
  kind: "review" as const,
  source: "agent",
  actions: [],
  state: "active",
  created_at: "2026-07-06",
  snoozed_until: null,
};
const overview = {
  areas: [
    {
      key: "o-1a",
      title: "O-1A",
      autonomy: "observe" as const,
      live: true,
      updated: "",
      ask_count: 1,
    },
  ],
  focus: [ask],
};

test("overview load + ask resolve removes from focus", () => {
  let s = reduceOverviewLoaded(createAreasDomainState(), overview);
  expect(s.overview?.focus.length).toBe(1);
  s = reduceAskResolved(s, "o-1a", "a1");
  expect(s.overview?.focus.length).toBe(0);
});

test("openArea sets and clears the room", () => {
  let s = reduceOpenArea(createAreasDomainState(), "o-1a");
  expect(s.openAreaKey).toBe("o-1a");
  expect(reduceOpenArea(s, null).openAreaKey).toBeNull();
});

const detail = {
  key: "o-1a",
  title: "O-1A",
  autonomy: "observe" as const,
  page_path: "topics/o-1a.md",
  related: [],
  open_loops: [],
  updated: "",
  asks: [],
  sessions: [],
  automations: [],
};

test("setCurrentSession closes an open area room (navigating away from an area)", () => {
  getState().openArea("o-1a");
  expect(getState().areas.openAreaKey).toBe("o-1a");

  getState().setCurrentSession("s1");

  expect(getState().areas.openAreaKey).toBeNull();
});

const record = (area_id: string, name: string): Area => ({
  area_id,
  name,
  default_cwd: null,
  instructions: null,
  knowledge_scope: `area:${area_id}`,
  page_path: null,
  autonomy: null,
  attention: "ambient",
  interrupts: "asks",
  paused_at: null,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
  archived_at: null,
});

test("canonical area records load, upsert, and archive all cached projections", () => {
  let s = reduceRecordsLoaded(createAreasDomainState(), [record("a1", "Health"), record("a2", "Dex")]);
  s = reduceOverviewLoaded(s, {
    ...overview,
    areas: [{ ...overview.areas[0], key: "a1", title: "Health" }],
  });
  s = reduceDetailLoaded(s, { ...detail, key: "a1", title: "Health" });
  s = reduceOpenArea(s, "a1");

  s = reduceRecordUpserted(s, { ...record("a1", "Wellbeing"), updated_at: "2026-02-01" });
  expect(s.recordsById.a1.name).toBe("Wellbeing");
  expect(s.overview?.areas[0].title).toBe("Wellbeing");
  expect(s.detailByKey.a1.title).toBe("Wellbeing");

  s = reduceRecordArchived(s, "a1");
  expect(s.recordsById.a1).toBeUndefined();
  expect(s.overview?.areas).toEqual([]);
  expect(s.detailByKey.a1).toBeUndefined();
  expect(s.openAreaKey).toBeNull();
});
