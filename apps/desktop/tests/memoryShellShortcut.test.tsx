import { afterEach, expect, test } from "bun:test";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { App } from "@/app/App";
import type { AppConfig } from "@/api/core";
import { useStore } from "@/stores";

const config: AppConfig = { serverUrl: "http://localhost:6877", apiKey: "test-key" };
let root: Root | null = null;

function response(data: unknown) {
  return { ok: true, status: 200, statusText: "OK", contentType: "application/json", data, text: "" };
}

function installBridge() {
  window.ardenDesktop = {
    // App bootstrap is unrelated to this shortcut path. Keep it pending so
    // its background state writes cannot race this focused interaction test.
    config: { get: () => new Promise<AppConfig>(() => {}), set: async () => config },
    api: {
      request: async (_config, request) => {
        if (request.path === "/health") return response({ auth: true, version: "test" });
        if (request.path.startsWith("/sessions?")) return response({ sessions: [] });
        if (request.path === "/areas") return response({ areas: [] });
        if (request.path === "/areas/overview") {
          return response({ areas: [], focus: [], brief: { done: [], in_progress: [], needs_you: [] } });
        }
        if (request.path === "/admin/wiki/pages") return response({ repository_head: "wiki-head-1", pages: [] });
        if (request.path === "/admin/wiki/rename-approvals") return response({ approvals: [] });
        if (request.path.startsWith("/admin/facts?")) return response({ facts: [], has_more: false, next_after: null });
        if (request.path.startsWith("/automations")) return response({ automations: [] });
        return response({});
      },
    },
  } as Window["ardenDesktop"];
}

async function settle(delay = 0) {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, delay));
  });
}

afterEach(async () => {
  if (root) await act(async () => root?.unmount());
  root = null;
  document.body.replaceChildren();
});

test("Cmd/Ctrl+B belongs to Memory instead of toggling the covered app shell", async () => {
  const originalState = useStore.getState();
  const originalDesktop = window.ardenDesktop;
  const originalFetch = globalThis.fetch;
  const app = document.createElement("div");
  app.id = "app";
  document.body.append(app);

  try {
    installBridge();
    globalThis.fetch = (async () => new Response("", { status: 401 })) as typeof fetch;
    useStore.setState({
      ...originalState,
      config,
      connected: false,
      currentSessionId: null,
      memoryOpen: true,
      prefs: { ...originalState.prefs, theme: "light", sidebarHidden: false },
    }, true);

    root = createRoot(app);
    await act(async () => root?.render(<App />));
    await settle(100);

    const memory = app.querySelector<HTMLElement>('[data-memory-layout="notebook"]')!;
    expect(memory).not.toBeNull();
    const shortcut = new KeyboardEvent("keydown", { key: "b", ctrlKey: true, bubbles: true, cancelable: true });
    await act(async () => window.dispatchEvent(shortcut));

    expect(shortcut.defaultPrevented).toBe(true);
    expect(memory.classList.contains("rail-hidden")).toBe(true);
    expect(useStore.getState().prefs.sidebarHidden).toBe(false);
  } finally {
    if (root) await act(async () => root?.unmount());
    root = null;
    globalThis.fetch = originalFetch;
    window.ardenDesktop = originalDesktop;
    useStore.setState(originalState, true);
  }
});
