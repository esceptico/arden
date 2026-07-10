import { expect, test } from "bun:test";
import { getState } from "@/stores/index";
import {
  createAreasDomainState,
  reduceOverviewLoaded,
  reduceAskResolved,
  reduceOpenArea,
  reduceDetailLoaded,
  reduceAutonomyUpdated,
} from "@/stores/areas-domain";

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

test("autonomy update patches both cached detail and overview summary", () => {
  let s = reduceOverviewLoaded(createAreasDomainState(), overview);
  s = reduceDetailLoaded(s, detail);
  s = reduceAutonomyUpdated(s, "o-1a", "act");
  expect(s.detailByKey["o-1a"].autonomy).toBe("act");
  expect(s.overview?.areas[0].autonomy).toBe("act");
});

test("autonomy update is a no-op when the area has no cached detail", () => {
  const s = reduceAutonomyUpdated(createAreasDomainState(), "unknown", "act");
  expect(s.detailByKey["unknown"]).toBeUndefined();
});

test("setCurrentSession closes an open area room (navigating away from an area)", () => {
  getState().openArea("o-1a");
  expect(getState().areas.openAreaKey).toBe("o-1a");

  getState().setCurrentSession("s1");

  expect(getState().areas.openAreaKey).toBeNull();
});
