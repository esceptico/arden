import { expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { createMainProcessFetch, createSessionFetch } = require("../electron/main-process-fetch.cjs") as {
  createMainProcessFetch(
    electronNet: { fetch(input: URL, init?: RequestInit): Promise<Response> },
  ): (input: URL, init?: RequestInit) => Promise<Response>;
  createSessionFetch(
    electronSession: {
      fromPartition(partition: string): { fetch(input: URL, init?: RequestInit): Promise<Response> };
    },
    partition: string,
  ): (input: URL, init?: RequestInit) => Promise<Response>;
};

test("main-process fetch uses Electron net and preserves streaming request options", async () => {
  const expectedResponse = new Response("data: event\n\n");
  const controller = new AbortController();
  const init = { headers: { Authorization: "Bearer test" }, signal: controller.signal };
  const calls: Array<{ input: URL; init?: RequestInit }> = [];
  const electronNet = {
    async fetch(input: URL, requestInit?: RequestInit) {
      calls.push({ input, init: requestInit });
      return expectedResponse;
    },
  };

  const mainProcessFetch = createMainProcessFetch(electronNet);
  const input = new URL("http://localhost:6877/chat/events/session?stream=true");
  const response = await mainProcessFetch(input, init);

  expect(response).toBe(expectedResponse);
  expect(calls).toEqual([{ input, init }]);
  expect(await response.text()).toBe("data: event\n\n");
});

test("Electron main process never falls back to Node global fetch", () => {
  const source = readFileSync(new URL("../electron/main.cjs", import.meta.url), "utf8");

  expect(source).not.toMatch(/await\s+fetch\s*\(/);
  expect(source.match(/await\s+mainProcessFetch\s*\(/g)).toHaveLength(1);
  expect(source.match(/await\s+fetchApi\s*\(/g)).toHaveLength(1);
});

test("regular API requests use a network session isolated from long-lived streams", async () => {
  const calls: string[] = [];
  const expectedResponse = new Response('{"pages":[]}');
  const apiFetch = createSessionFetch({
    fromPartition(partition) {
      calls.push(partition);
      return {
        async fetch() {
          return expectedResponse;
        },
      };
    },
  }, "arden-api");

  expect(await apiFetch(new URL("http://localhost:6877/admin/wiki/pages"))).toBe(expectedResponse);
  expect(calls).toEqual(["arden-api"]);
});

test("main-process fetch fails closed when Electron net.fetch is unavailable", () => {
  expect(() => createMainProcessFetch({} as never)).toThrow("Electron net.fetch is unavailable");
});

test("session fetch fails closed when Electron partitioning is unavailable", () => {
  expect(() => createSessionFetch({} as never, "arden-api"))
    .toThrow("Electron session.fromPartition is unavailable");
});
