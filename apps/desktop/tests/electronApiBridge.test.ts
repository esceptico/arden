import { expect, test } from "bun:test";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { parseApiResponseBody } = require("../electron/api-response.cjs") as {
  parseApiResponseBody(contentType: string, text: string): { data: unknown; text: string };
};

test("bridge response parsing retains valid JSON and the original text", () => {
  expect(parseApiResponseBody("application/json; charset=utf-8", '{"detail":"conflict"}')).toEqual({
    data: { detail: "conflict" },
    text: '{"detail":"conflict"}',
  });
});

test("bridge response parsing tolerates malformed JSON without losing its body", () => {
  expect(parseApiResponseBody("application/json", "{not-json")).toEqual({
    data: null,
    text: "{not-json",
  });
});

test("bridge response parsing retains plain text without pretending it is JSON", () => {
  expect(parseApiResponseBody("text/plain", "upstream unavailable")).toEqual({
    data: null,
    text: "upstream unavailable",
  });
});
