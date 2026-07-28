import { afterEach, expect, test } from "bun:test";
import type { AppConfig } from "@/api/core";
import { pinToMemoryApi } from "@/api/chat";

const config: AppConfig = { serverUrl: "http://localhost:6877", apiKey: "test-key" };
const originalDesktop = window.ardenDesktop;

afterEach(() => {
  window.ardenDesktop = originalDesktop;
});

test("Pin to memory writes once through the canonical fact endpoint", async () => {
  const requests: Array<{ path: string; method: string; body: unknown }> = [];
  window.ardenDesktop = {
    api: {
      request: async (_config, request) => {
        requests.push({
          path: request.path,
          method: request.method ?? "GET",
          body: request.body ? JSON.parse(request.body) : null,
        });
        return {
          ok: true,
          status: 201,
          statusText: "Created",
          contentType: "application/json",
          data: { written: false, fact_id: "fact-1" },
          text: "",
        };
      },
    },
  } as Window["ardenDesktop"];

  expect(await pinToMemoryApi(config, "  Exact agent result  ")).toEqual({ written: false });
  expect(requests).toHaveLength(1);
  expect(requests[0]).toMatchObject({
    path: "/admin/facts",
    method: "POST",
    body: { text: "Exact agent result" },
  });
  expect((requests[0]?.body as { request_id: string }).request_id).toBeString();

  expect(await pinToMemoryApi(config, "   ")).toEqual({ written: false });
  expect(requests).toHaveLength(1);
});
