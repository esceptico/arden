import { expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { ComposerModelConfigPicker, ModelReasoningChip } from "@/components/ui/ModelPickers";
import type { ModelGroup, ServerConfig } from "@/api/types";
import { getState, setState } from "@/stores";

const GROUPS: ModelGroup[] = [
  { provider: "anthropic", label: "Anthropic", models: ["anthropic/opus", "anthropic/sonnet"] },
  { provider: "openai", label: "OpenAI", models: ["openai/gpt"] },
];

function mount(): { el: HTMLElement; root: Root; restore: () => void } {
  const el = document.createElement("div");
  document.body.append(el);
  return { el, root: createRoot(el), restore: () => el.remove() };
}

function picker({
  onSelectModel = () => {},
  onSelectEffort = () => {},
}: {
  onSelectModel?: (model: string) => void;
  onSelectEffort?: (model: string, effort: string | null) => void;
} = {}) {
  return (
    <ComposerModelConfigPicker
      currentModel="anthropic/opus"
      currentEffort={null}
      modelReasoningEfforts={{
        "anthropic/sonnet": "medium",
        "openai/gpt": "low",
      }}
      reasoningEfforts={{
        "anthropic/opus": ["low", "medium", "high"],
        "anthropic/sonnet": ["low", "medium", "high"],
        "openai/gpt": ["low", "high"],
      }}
      models={GROUPS.flatMap((group) => group.models)}
      onSelectModel={onSelectModel}
      onSelectEffort={onSelectEffort}
    />
  );
}

test("Chat model control combines the current model and effort in one menu trigger", async () => {
  const selectedModels: string[] = [];
  const { el, root, restore } = mount();
  try {
    await act(async () => {
      root.render(picker({ onSelectModel: (model) => selectedModels.push(model) }));
    });

    const trigger = el.querySelector<HTMLElement>('[aria-label^="Model configuration:"]')!;
    expect(trigger).not.toBeNull();
    expect(trigger.textContent).toContain("opus");
    expect(trigger.textContent).toContain("Default");
    expect(trigger.getAttribute("aria-haspopup")).toBe("menu");

    await act(async () => {
      trigger.click();
    });

    const menu = document.querySelector<HTMLElement>('[role="menu"][aria-label="Model configuration"]')!;
    expect(menu).not.toBeNull();
    expect(menu.querySelector('[role="combobox"]')).toBeNull();
    expect(menu.querySelectorAll('[role="menuitemradio"]')).toHaveLength(3);
    expect(menu.textContent).toContain("opus");
    expect(menu.textContent).toContain("sonnet");
    expect(menu.textContent).toContain("gpt");
    expect(menu.textContent).not.toContain("anthropic/");

    const sonnet = Array.from(menu.querySelectorAll<HTMLElement>('[role="menuitemradio"]'))
      .find((item) => item.textContent?.trim() === "sonnet")!;
    await act(async () => {
      sonnet.click();
    });
    expect(selectedModels).toEqual(["anthropic/sonnet"]);
    expect(trigger.getAttribute("aria-expanded")).toBe("true");
  } finally {
    await act(async () => root.unmount());
    restore();
  }
});

test("an effort submenu activates its model and commits that model's effort", async () => {
  const models: string[] = [];
  const efforts: Array<[string, string | null]> = [];
  const { el, root, restore } = mount();
  try {
    await act(async () => {
      root.render(picker({
        onSelectModel: (model) => models.push(model),
        onSelectEffort: (model, effort) => efforts.push([model, effort]),
      }));
    });

    const trigger = el.querySelector<HTMLElement>('[aria-label^="Model configuration:"]')!;
    await act(async () => {
      trigger.click();
    });

    const effortTrigger = document.querySelector<HTMLElement>(
      '[aria-label="Set reasoning effort for openai/gpt: Low"]',
    )!;
    await act(async () => {
      effortTrigger.click();
    });

    expect(models).toEqual(["openai/gpt"]);
    const effortMenu = document.querySelector<HTMLElement>(
      '[role="menu"][aria-label="Reasoning effort for openai/gpt"]',
    )!;
    expect(effortMenu).not.toBeNull();
    const high = Array.from(effortMenu.querySelectorAll<HTMLElement>('[role="menuitemradio"]'))
      .find((item) => item.textContent?.trim() === "High")!;
    await act(async () => {
      high.click();
    });

    expect(efforts).toEqual([["openai/gpt", "high"]]);
    expect(effortTrigger.getAttribute("aria-expanded")).toBe("false");
    expect(trigger.getAttribute("aria-expanded")).toBe("true");
  } finally {
    await act(async () => root.unmount());
    restore();
  }
});

test("the Chat picker opens with configured models when the live catalog is unavailable", async () => {
  const previous = getState();
  const originalDesktop = window.ardenDesktop;
  const requests: Array<{ path: string; method?: string; body?: string; timeout?: number }> = [];
  window.ardenDesktop = {
    ...originalDesktop,
    api: {
      request: async (_config, request) => {
        requests.push(request);
        return {
          ok: true,
          status: 204,
          statusText: "No Content",
          contentType: "",
          data: null,
          text: "",
        };
      },
    },
  };
  const serverConfig: ServerConfig = {
    chat_model: "openai/gpt",
    chat_model_max_context: 200_000,
    compaction_token_limit: 160_000,
    compaction_token_trigger: 150_000,
    research_model: "anthropic/opus",
    workflow_model: "anthropic/sonnet",
    memory_model: "anthropic/opus",
    embedding_model: "text-embedding-3-small",
    web_search: "auto",
    web_search_provider: "none",
    max_depth: 8,
    reasoning_effort: "low",
    reasoning_efforts: ["low", "high"],
    model_reasoning_efforts: { "openai/gpt": "low" },
    compression_threshold: 0.8,
    max_messages: 100,
    compression_keep_ratio: 0.5,
    summary_max_tokens: 500,
    consolidation_interval: 60,
    memory_enabled: true,
    integrations: {},
    tool_overrides: {},
  };
  setState({
    serverConfig,
    serverModels: null,
    currentSessionId: "session-1",
    sessions: [{
      session_id: "session-1",
      started_at: "2026-07-24T00:00:00Z",
      last_activity: "2026-07-24T00:00:00Z",
      name: "Picker test",
      message_count: 0,
      chat_model: "openai/gpt",
    }],
  });

  const { el, root, restore } = mount();
  try {
    await act(async () => root.render(<ModelReasoningChip />));
    const trigger = el.querySelector<HTMLButtonElement>('[aria-label^="Model configuration:"]')!;
    expect(trigger.disabled).toBe(false);

    await act(async () => trigger.click());
    const menu = document.querySelector<HTMLElement>('[role="menu"][aria-label="Model configuration"]')!;
    expect(menu.textContent).toContain("gpt");
    expect(menu.textContent).toContain("opus");
    expect(menu.textContent).toContain("sonnet");

    const opus = Array.from(menu.querySelectorAll<HTMLElement>('[role="menuitemradio"]'))
      .find((item) => item.textContent?.trim() === "opus")!;
    await act(async () => {
      opus.click();
      await Promise.resolve();
    });
    expect(requests).toEqual([{
      path: "/sessions/session-1/model",
      method: "PUT",
      body: JSON.stringify({ chat_model: "anthropic/opus" }),
      timeout: 60_000,
    }]);
    expect(getState().sessions[0]?.chat_model).toBe("anthropic/opus");
  } finally {
    await act(async () => root.unmount());
    restore();
    setState({
      serverConfig: previous.serverConfig,
      serverModels: previous.serverModels,
      currentSessionId: previous.currentSessionId,
      sessions: previous.sessions,
    });
    window.ardenDesktop = originalDesktop;
  }
});

test("the split picker remains available outside Chat and the composer collapses to one chevron at 620px", () => {
  const selectors = readFileSync(new URL("../src/components/ui/ModelPickers.tsx", import.meta.url), "utf8");
  const pickerCss = readFileSync(new URL("../src/design/model-pickers.css", import.meta.url), "utf8");
  const chatPicker = selectors.slice(
    selectors.indexOf("export function ComposerModelConfigPicker"),
    selectors.indexOf("/** Board's combined configuration control"),
  );

  expect(selectors).toContain("export function ModelReasoningPicker");
  expect(selectors).toContain("<Combobox.Root");
  expect(selectors).toContain("function ReasoningMenu");
  expect(chatPicker).toContain("<Menu.SubmenuRoot");
  expect(chatPicker).toContain("anchor={menuAnchor}");
  expect(chatPicker).not.toContain("<Combobox.Root");
  expect(chatPicker).not.toContain("<ReasoningMenu");
  expect(pickerCss).toMatch(/\.board-composer \.model-config-trigger \{[\s\S]*?height: var\(--control-size-large\);[\s\S]*?justify-content: center;[\s\S]*?font-size: var\(--text-control\);[\s\S]*?font-weight: 520;/);
  expect(pickerCss).toMatch(/@media \(max-width: 38\.75rem\)[\s\S]*?\.board-composer \.model-current,[\s\S]*?\.board-composer \.effort-current \{[\s\S]*?display: none;/);
  expect(pickerCss).toMatch(/\.board-composer \.model-config-trigger \{[\s\S]*?width: 34px;[\s\S]*?justify-content: center;/);
});
