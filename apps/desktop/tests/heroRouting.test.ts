import { expect, test } from "bun:test";
import { routeHeroInput } from "@/features/home/lib/heroRouting";

const ctx = {
  sessions: [{ session_id: "s1", name: "counsel requirements" }],
  areas: [{ key: "o-1a", title: "O-1A" }],
  automations: [{ task_id: "t1", name: "morning-digest" }],
  skills: [{ name: "research", description: "" }],
};

test("plain text routes to chat first", () => {
  const s = routeHeroInput("book flights", ctx);
  expect(s[0].kind).toBe("chat");
});

test("prefix matches surface areas, sessions, automations", () => {
  const s = routeHeroInput("o-1", ctx);
  expect(s.some((x) => x.kind === "area" && x.ref === "o-1a")).toBe(true);
  const t = routeHeroInput("morning", ctx);
  expect(t.some((x) => x.kind === "automation" && x.ref === "t1")).toBe(true);
});

test("empty query still returns a chat suggestion with empty label", () => {
  const s = routeHeroInput("", ctx);
  expect(s.length).toBe(1);
  expect(s[0]).toEqual({ kind: "chat", label: "", ref: "" });
});

test("chat suggestion is always first even when other kinds match", () => {
  const s = routeHeroInput("research", ctx);
  expect(s[0].kind).toBe("chat");
  expect(s.some((x) => x.kind === "skill" && x.ref === "research")).toBe(true);
});

test("matches session names case-insensitively", () => {
  const s = routeHeroInput("COUNSEL", ctx);
  expect(s.some((x) => x.kind === "session" && x.ref === "s1")).toBe(true);
});

test("caps suggestions at 6", () => {
  const manyAreas = Array.from({ length: 10 }, (_, i) => ({ key: `area-${i}`, title: `Area ${i}` }));
  const s = routeHeroInput("area", { ...ctx, areas: manyAreas });
  expect(s.length).toBeLessThanOrEqual(6);
});

test("no matches returns only the chat suggestion", () => {
  const s = routeHeroInput("zzz-nonexistent-zzz", ctx);
  expect(s).toEqual([{ kind: "chat", label: "zzz-nonexistent-zzz", ref: "" }]);
});

test("a question mentioning an area's distinctive word surfaces that area", () => {
  const ctx2 = { ...ctx, areas: [{ key: "apartment-hunt", title: "Apartment hunt" }] };
  const s = routeHeroInput("should I apply to the apartment on Elsie", ctx2);
  expect(s[0].kind).toBe("chat"); // chat still default
  expect(s.some((x) => x.kind === "area" && x.ref === "apartment-hunt")).toBe(true);
});

test("short filler words never trigger an area match", () => {
  const ctx2 = { ...ctx, areas: [{ key: "aside", title: "Aside" }] };
  // "the" / "a" / "on" are < 4 chars; nothing distinctive from Aside appears.
  expect(routeHeroInput("what a day on the road", ctx2).some((x) => x.kind === "area")).toBe(false);
});
