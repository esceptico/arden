import { afterEach, expect, test } from "bun:test";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { SettingsModal } from "@/features/settings/components/SettingsModal";
import { getState, setState } from "@/stores";

let root: Root | null = null;
const originalDesktop = window.ardenDesktop;
const originalTheme = getState().prefs.theme;

function bridgeResponse(data: unknown) {
  return {
    ok: true,
    status: 200,
    statusText: "OK",
    contentType: "application/json",
    data,
    text: "",
  };
}

afterEach(async () => {
  if (root) await act(async () => root?.unmount());
  root = null;
  window.ardenDesktop = originalDesktop;
  getState().setPref("theme", originalTheme);
  setState({ settingsOpen: false, settingsTab: null });
  document.body.replaceChildren();
});

test("Appearance navigation and its controls remain clickable", async () => {
  window.ardenDesktop = {
    api: {
      request: async (_config, request) => {
        if (request.path === "/config") {
          return bridgeResponse({
            chat_model: "test",
            chat_model_max_context: 128_000,
            compaction_token_limit: 100_000,
            compaction_token_trigger: 90_000,
            research_model: "test",
            memory_model: "test",
            embedding_model: "test",
            web_search: "none",
            web_search_provider: "none",
            max_depth: 1,
            reasoning_effort: null,
            reasoning_efforts: [],
            model_reasoning_efforts: {},
            compression_threshold: 0.8,
            max_messages: 100,
            compression_keep_ratio: 0.5,
            summary_max_tokens: 1_000,
            memory_enabled: true,
            integrations: {},
            tool_overrides: {},
          });
        }
        if (request.path === "/models") {
          return bridgeResponse({
            models: [],
            groups: [],
            reasoning_efforts: {},
            chat_model: "test",
            research_model: "test",
            workflow_model: "test",
            memory_model: "test",
          });
        }
        if (request.path === "/providers") return bridgeResponse({ providers: [] });
        return bridgeResponse({});
      },
    },
  } as Window["ardenDesktop"];

  const app = document.createElement("div");
  app.id = "app";
  const host = document.createElement("div");
  document.body.append(app, host);
  root = createRoot(host);
  setState({ settingsOpen: true, settingsTab: null });

  await act(async () => {
    root?.render(<SettingsModal />);
  });

  const appearance = app.querySelector<HTMLButtonElement>('[role="tab"][data-tab-value="appearance"]');
  expect(appearance).not.toBeNull();
  await act(async () => appearance?.click());

  expect(appearance?.getAttribute("aria-selected")).toBe("true");
  await act(async () => {
    await Bun.sleep(220);
  });
  expect(Array.from(app.querySelectorAll("h1")).at(-1)?.textContent).toBe("Appearance");
  const dark = app.querySelector<HTMLButtonElement>('[role="tab"][data-tab-value="dark"]');
  expect(dark).not.toBeNull();
  await act(async () => dark?.click());
  expect(getState().prefs.theme).toBe("dark");
});
