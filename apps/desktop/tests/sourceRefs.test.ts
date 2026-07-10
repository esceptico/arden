import { expect, test } from "bun:test";
import type { UiMessage } from "@/stores/types";
import { normalizeSourceRefs, sourceRefsForTurn } from "@/stores/sourceRefs";

const source = (overrides: Record<string, unknown> = {}) => ({
  provider: "slack",
  kind: "message",
  ref: "C1:1.0",
  title: "Decision",
  url: "https://example.slack.com/archives/C1/p1",
  ...overrides,
});

test("normalizes untrusted source refs and associates the matching tool call", () => {
  const refs = normalizeSourceRefs(
    [
      source({
        provider: " slack ",
        kind: " message ",
        ref: " C1:1.0 ",
        title: ` ${"x".repeat(300)} `,
        url: " https://example.slack.com/archives/C1/p1 ",
        toolCallId: "forged-wire-id",
      }),
      source({ ref: "missing-title", title: "   " }),
      source({ ref: "oversized-provider", provider: "x".repeat(65) }),
      source({ ref: "oversized-kind", kind: "x".repeat(65) }),
      source({ ref: "x".repeat(2049) }),
      null,
      "not-an-object",
    ],
    "tool-actual",
  );

  expect(refs).toEqual([
    {
      provider: "slack",
      kind: "message",
      ref: "C1:1.0",
      title: "x".repeat(256),
      url: "https://example.slack.com/archives/C1/p1",
      toolCallId: "tool-actual",
    },
  ]);
  expect(normalizeSourceRefs(source(), "tool-actual")).toEqual([]);
});

test("omits unsafe URLs without discarding their source refs", () => {
  const prefix = "https://example.com/";
  const maxLengthUrl = prefix + "x".repeat(4096 - prefix.length);
  const urls = [
    "javascript:alert(1)",
    "ftp://example.com/file",
    "http:example.com",
    "https:///missing-host",
    "https://[bad]/path",
    "https://exam／ple.com/path",
    "https://exam＠ple.com/path",
    "https://exam？ple.com/path",
    "https://exam＃ple.com/path",
    "https://exam：ple.com/path",
    "https://@example.com/private",
    "https://user@example.com/private",
    "https://user:secret@example.com/private",
    `https://example.com/${"x".repeat(4090)}`,
    "not a url",
  ];

  const refs = normalizeSourceRefs([
    ...urls.map((url, index) => source({ ref: `unsafe-${index}`, url })),
    source({ ref: "max-url", url: ` ${maxLengthUrl} ` }),
  ]);

  expect(refs.slice(0, urls.length).every((ref) => ref.url === undefined)).toBe(true);
  expect(refs.at(-1)?.url).toBe(maxLengthUrl);
});

test("does not impose URL rules beyond the server source contract", () => {
  const urls = [
    "HTTPS://example.com:opaque-port/path",
    "https://[v1.future-host]/path",
    "https://[::1]junk/path",
  ];

  expect(
    normalizeSourceRefs(urls.map((url, index) => source({ ref: `safe-${index}`, url }))).map(
      (ref) => ref.url,
    ),
  ).toEqual(urls);
});

test("validates after stripping embedded CR LF and TAB but retains the original URL", () => {
  const url = "ht\ttps://exa\r\nmple.com/path";

  expect(normalizeSourceRefs([source({ url })])[0]?.url).toBe(url);
});

test("validates after left-stripping WHATWG C0 controls but retains the original URL", () => {
  const url = "\x00https://example.com/path";

  expect(normalizeSourceRefs([source({ url })])[0]?.url).toBe(url);
});

test("deduplicates normalized identities before capping each payload at 50", () => {
  const refs = normalizeSourceRefs([
    source({ title: "First" }),
    source({ provider: " slack ", ref: " C1:1.0 ", title: "Duplicate" }),
    ...Array.from({ length: 54 }, (_, index) =>
      source({ ref: `C1:${index + 1}`, title: `Message ${index + 1}` }),
    ),
  ]);

  expect(refs).toHaveLength(50);
  expect(refs[0]?.title).toBe("First");
  expect(refs.slice(0, 3).map((ref) => ref.ref)).toEqual(["C1:1.0", "C1:1", "C1:2"]);
  expect(refs.at(-1)?.ref).toBe("C1:49");
});

test("aggregates only ordered turn children and keeps the first source identity", () => {
  const messages = new Map<string, UiMessage>([
    [
      "outside-activity",
      {
        id: "outside-activity",
        role: "activity",
        content: "",
        activity: {
          label: "Called",
          done: true,
          items: [
            {
              id: "outside-tool",
              kind: "search",
              target: "Search",
              sourceRefs: [source({ ref: "outside" })],
            },
          ],
        },
      },
    ],
    [
      "turn-activity-1",
      {
        id: "turn-activity-1",
        role: "activity",
        content: "",
        activity: {
          label: "Called",
          done: true,
          items: [
            {
              id: "tool-1",
              kind: "slack_thread",
              target: "Slack thread",
              sourceRefs: [
                source({ toolCallId: "forged", title: "First" }),
                source({
                  provider: "web",
                  kind: "page",
                  ref: "https://docs.example.test/a",
                  title: "Docs",
                  url: "https://docs.example.test/a",
                }),
              ],
            },
          ],
        },
      },
    ],
    ["assistant-1", { id: "assistant-1", role: "assistant", content: "Done" }],
    [
      "turn-activity-2",
      {
        id: "turn-activity-2",
        role: "activity",
        content: "",
        activity: {
          label: "Called",
          done: true,
          items: [
            {
              id: "tool-2",
              kind: "slack_search",
              target: "Slack search",
              sourceRefs: [
                source({ title: "Duplicate" }),
                source({ ref: "C1:2.0", title: "Second" }),
              ],
            },
          ],
        },
      },
    ],
  ]);

  const refs = sourceRefsForTurn(messages, ["turn-activity-1", "assistant-1", "turn-activity-2"]);

  expect(refs.map((ref) => ref.ref)).toEqual([
    "C1:1.0",
    "https://docs.example.test/a",
    "C1:2.0",
  ]);
  expect(refs[0]).toMatchObject({ title: "First", toolCallId: "tool-1" });
});

test("does not cap aggregation across activity items in one turn", () => {
  const activityItems = Array.from({ length: 2 }, (_, batch) => ({
    id: `tool-${batch}`,
    kind: "search",
    target: "Search",
    sourceRefs: Array.from({ length: 50 }, (_, index) => ({
      provider: "web",
      kind: "page",
      ref: `https://example.test/${batch}/${index}`,
      title: `Result ${batch}-${index}`,
    })),
  }));
  const messages = new Map<string, UiMessage>([
    [
      "turn-activity",
      {
        id: "turn-activity",
        role: "activity",
        content: "",
        activity: { label: "Called", done: true, items: activityItems },
      },
    ],
  ]);

  expect(sourceRefsForTurn(messages, ["turn-activity"])).toHaveLength(100);
});
