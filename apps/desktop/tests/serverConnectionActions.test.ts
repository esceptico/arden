import { afterEach, expect, test } from "bun:test";
import { saveAndReconnect } from "@/actions/server";
import { getState, setState } from "@/stores/index";

const originalFetch = globalThis.fetch;

afterEach(() => {
  globalThis.fetch = originalFetch;
});

test("successful reconnect clears the saving guard before closing Settings", async () => {
  globalThis.fetch = async (input) => {
    const path = new URL(String(input)).pathname;
    const body =
      path === "/health"
        ? { auth: true, version: "test", has_providers: true }
        : path === "/areas"
          ? { areas: [] }
          : path === "/sessions"
            ? { sessions: [] }
            : {};
    return new Response(JSON.stringify(body), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  };

  setState({
    settingsOpen: true,
    connectionSaving: false,
    connectionError: null,
    connected: false,
  });

  await saveAndReconnect({ serverUrl: "http://127.0.0.1:6878", apiKey: "qa-key" });

  expect(getState().connected).toBe(true);
  expect(getState().connectionSaving).toBe(false);
  expect(getState().settingsOpen).toBe(false);
  expect(getState().connectionError).toBeNull();
});
