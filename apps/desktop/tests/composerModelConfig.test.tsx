import { expect, test } from "bun:test";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { ModelReasoningChip } from "@/components/ui/ModelPickers";
import type { ModelGroup, ServerConfig } from "@/api/types";
import { getState, setState } from "@/stores";

/* The composer's model control used to be ComposerModelConfigPicker — one
   trigger opening a combined model + effort menu with submenus. Chat now shares
   Settings' two controls through ModelReasoningChip: a searchable model
   combobox and a separate effort menu, wired to the store. These cover what the
   chip itself owns — which model it reads, and what each choice commits. */

const GROUPS: ModelGroup[] = [
  { provider: "anthropic", label: "Anthropic", models: ["anthropic/opus", "anthropic/sonnet"] },
  { provider: "openai", label: "OpenAI", models: ["openai/gpt"] },
];

const CONFIG: ServerConfig = {
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
  model_reasoning_efforts: { "anthropic/opus": "low" },
  compression_threshold: 0.8,
  max_messages: 100,
  compression_keep_ratio: 0.5,
  summary_max_tokens: 500,
  consolidation_interval: 60,
  memory_enabled: true,
  integrations: {},
  tool_overrides: {},
};

const SESSION = {
  session_id: "session-1",
  started_at: "2026-07-24T00:00:00Z",
  last_activity: "2026-07-24T00:00:00Z",
  name: "Picker test",
  message_count: 0,
  chat_model: "anthropic/opus",
};

function mount(): { el: HTMLElement; root: Root; restore: () => void } {
  const el = document.createElement("div");
  document.body.append(el);
  return { el, root: createRoot(el), restore: () => el.remove() };
}

/** Captures outbound requests and answers each with a valid config payload, so
 *  the response parser is exercised rather than bypassed. */
function stubApi(requests: Array<{ path: string; method?: string; body?: string }>) {
  const original = window.ardenDesktop;
  window.ardenDesktop = {
    ...original,
    api: {
      request: async (_config, request) => {
        requests.push(request);
        return {
          ok: true,
          status: 200,
          statusText: "OK",
          contentType: "application/json",
          data: CONFIG,
          text: JSON.stringify(CONFIG),
        };
      },
    },
  };
  return () => {
    window.ardenDesktop = original;
  };
}

function withStore<T>(patch: Record<string, unknown>, run: () => Promise<T>): Promise<T> {
  const previous = getState();
  setState(patch);
  return run().finally(() => {
    setState({
      serverConfig: previous.serverConfig,
      serverModels: previous.serverModels,
      currentSessionId: previous.currentSessionId,
      sessions: previous.sessions,
    });
  });
}

test("the chip reads the session's own model, not the global chat default", async () => {
  await withStore(
    {
      serverConfig: CONFIG,
      serverModels: { models: GROUPS.flatMap((g) => g.models), groups: GROUPS, reasoning_efforts: { "anthropic/opus": ["low", "high"] } },
      currentSessionId: "session-1",
      sessions: [SESSION],
    },
    async () => {
      const { el, root, restore } = mount();
      try {
        await act(async () => root.render(<ModelReasoningChip />));
        const modelTrigger = el.querySelector<HTMLElement>('[role="combobox"]')!;
        // config.chat_model is openai/gpt; the session pins anthropic/opus.
        expect(modelTrigger.textContent).toContain("opus");
        expect(modelTrigger.getAttribute("title")).toBe("anthropic/opus");
        // Effort comes from the per-model map, keyed by the SESSION's model.
        expect(el.querySelector('[aria-label="Reasoning effort: Low"]')).not.toBeNull();
      } finally {
        await act(async () => root.unmount());
        restore();
      }
    },
  );
});

test("choosing a model pins it to the session rather than editing the global default", async () => {
  const requests: Array<{ path: string; method?: string; body?: string }> = [];
  const restoreApi = stubApi(requests);
  await withStore(
    {
      serverConfig: CONFIG,
      serverModels: { models: GROUPS.flatMap((g) => g.models), groups: GROUPS, reasoning_efforts: { "anthropic/opus": ["low", "high"] } },
      currentSessionId: "session-1",
      sessions: [SESSION],
    },
    async () => {
      const { el, root, restore } = mount();
      try {
        await act(async () => root.render(<ModelReasoningChip />));
        await act(async () => el.querySelector<HTMLElement>('[role="combobox"]')!.click());

        const catalog = document.querySelector<HTMLElement>('[role="dialog"][aria-label="Choose model"]')!;
        expect(catalog.textContent).toContain("Anthropic");
        const sonnet = Array.from(catalog.querySelectorAll<HTMLElement>('[role="option"]'))
          .find((item) => item.textContent?.trim() === "anthropic/sonnet")!;
        await act(async () => {
          sonnet.click();
          await Promise.resolve();
        });

        expect(requests).toEqual([{
          path: "/sessions/session-1/model",
          method: "PUT",
          body: JSON.stringify({ chat_model: "anthropic/sonnet" }),
          timeout: 60_000,
        }]);
      } finally {
        await act(async () => root.unmount());
        restore();
      }
    },
  );
  restoreApi();
});

test("choosing an effort commits it against the model it was chosen for", async () => {
  const requests: Array<{ path: string; method?: string; body?: string }> = [];
  const restoreApi = stubApi(requests);
  await withStore(
    {
      serverConfig: CONFIG,
      serverModels: { models: GROUPS.flatMap((g) => g.models), groups: GROUPS, reasoning_efforts: { "anthropic/opus": ["low", "high"] } },
      currentSessionId: "session-1",
      sessions: [SESSION],
    },
    async () => {
      const { el, root, restore } = mount();
      try {
        await act(async () => root.render(<ModelReasoningChip />));
        await act(async () => el.querySelector<HTMLElement>('[aria-label="Reasoning effort: Low"]')!.click());

        const menu = document.querySelector<HTMLElement>('[role="menu"][aria-label="Reasoning effort"]')!;
        const high = Array.from(menu.querySelectorAll<HTMLElement>('[role="menuitemradio"]'))
          .find((item) => item.textContent?.trim() === "High")!;
        await act(async () => {
          high.click();
          await Promise.resolve();
        });

        expect(requests).toHaveLength(1);
        expect(requests[0]?.path).toBe("/config");
        expect(requests[0]?.method).toBe("PATCH");
        // Against the session's model, not config.chat_model.
        expect(JSON.parse(requests[0]!.body!)).toEqual({
          reasoning_model: "anthropic/opus",
          reasoning_effort: "high",
        });
      } finally {
        await act(async () => root.unmount());
        restore();
      }
    },
  );
  restoreApi();
});

test("the chip stays out of the composer until the server contract is there", async () => {
  await withStore({ serverConfig: null, serverModels: null, currentSessionId: null, sessions: [] }, async () => {
    const { el, root, restore } = mount();
    try {
      await act(async () => root.render(<ModelReasoningChip />));
      expect(el.innerHTML).toBe("");
    } finally {
      await act(async () => root.unmount());
      restore();
    }
  });

  // A server that predates per-model efforts renders nothing rather than a
  // control that cannot commit anything.
  const stale = { ...CONFIG } as Record<string, unknown>;
  Reflect.deleteProperty(stale, "model_reasoning_efforts");
  await withStore({ serverConfig: stale, serverModels: null, currentSessionId: "session-1", sessions: [SESSION] }, async () => {
    const { el, root, restore } = mount();
    try {
      await act(async () => root.render(<ModelReasoningChip />));
      expect(el.innerHTML).toBe("");
    } finally {
      await act(async () => root.unmount());
      restore();
    }
  });
});

test("without a session there is nothing to pin a model to, so the control is inert", async () => {
  await withStore(
    {
      serverConfig: CONFIG,
      serverModels: { models: GROUPS.flatMap((g) => g.models), groups: GROUPS, reasoning_efforts: {} },
      currentSessionId: null,
      sessions: [],
    },
    async () => {
      const { el, root, restore } = mount();
      try {
        await act(async () => root.render(<ModelReasoningChip />));
        expect(el.querySelector<HTMLButtonElement>('[role="combobox"]')!.disabled).toBe(true);
      } finally {
        await act(async () => root.unmount());
        restore();
      }
    },
  );
});
