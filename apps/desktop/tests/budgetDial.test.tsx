import { afterEach, expect, test } from "bun:test";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import type { ServerConfig } from "@/api/types";
import { BudgetDial } from "@/features/chat/components/BudgetDial";
import { getState, setState } from "@/stores";
import { cachedSessionFromHistory } from "@/stores/history-response";

let root: Root | null = null;
const initialState = getState();

afterEach(async () => {
  if (root) await act(async () => root?.unmount());
  root = null;
  document.body.replaceChildren();
  setState({
    currentSessionId: initialState.currentSessionId,
    serverConfig: initialState.serverConfig,
    usage: initialState.usage,
  });
});

test("history caches budget counters with their session model", () => {
  const cached = cachedSessionFromHistory(
    "session-1",
    {
      messages: [],
      active_run_id: null,
      context_budget: {
        model: "session/model",
        hard_limit: 200_000,
        compaction_trigger: 152_000,
        message_limit: 100,
        input_tokens: 50_000,
        message_count: 12,
      },
    },
    undefined,
    false,
  );

  expect(cached.usage).toMatchObject({
    contextInputTokens: 50_000,
    messageCount: 12,
    contextBudget: {
      model: "session/model",
      hardLimit: 200_000,
      compactionTrigger: 152_000,
      messageLimit: 100,
    },
  });
});

test("keeps an exact zero active message count", async () => {
  const cached = cachedSessionFromHistory(
    "legacy-session",
    {
      messages: [],
      active_run_id: null,
      context_budget: {
        model: "session/model",
        hard_limit: 200_000,
        compaction_trigger: 152_000,
        message_limit: 100,
        input_tokens: 50_000,
        message_count: 0,
      },
    },
    undefined,
    false,
  );
  expect(cached.usage?.messageCount).toBe(0);

  setState({ currentSessionId: "legacy-session", usage: cached.usage });
  const app = document.createElement("div");
  app.id = "app";
  const host = document.createElement("div");
  app.append(host);
  document.body.append(app);
  root = createRoot(host);

  await act(async () => root?.render(<BudgetDial />));
  const trigger = host.querySelector<HTMLButtonElement>('[aria-label="Context budget"]')!;
  expect(trigger.title).toContain("0 / 100 msgs");

  await act(async () => trigger.click());
  expect(app.querySelector<HTMLElement>('[role="dialog"]')?.textContent).toContain("0 / 100");
});

test("shows the selected session's hard window and distinct compaction trigger", async () => {
  const serverConfig = {
    chat_model: "global/model",
    chat_model_max_context: 999_000,
    compaction_token_limit: 799_200,
    compaction_token_trigger: 759_240,
    max_messages: 120,
  } as ServerConfig;
  setState({
    currentSessionId: "session-1",
    serverConfig,
    usage: {
      contextInputTokens: 50_000,
      observedPromptTokens: 40_000,
      observedCompletionTokens: 10_000,
      observedCacheReadTokens: 15_000,
      observedCacheWriteTokens: 5_000,
      totalTokens: 70_000,
      totalCost: 0.25,
      messageCount: 12,
      contextBudget: {
        model: "session/model",
        hardLimit: 200_000,
        compactionTrigger: 152_000,
        messageLimit: 100,
      },
    },
  });
  const app = document.createElement("div");
  app.id = "app";
  const host = document.createElement("div");
  app.append(host);
  document.body.append(app);
  root = createRoot(host);

  await act(async () => root?.render(<BudgetDial />));
  const trigger = host.querySelector<HTMLButtonElement>('[aria-label="Context budget"]')!;
  expect(trigger.title).toContain("50k / 200k context tokens");
  expect(trigger.title).not.toContain("999k");

  await act(async () => trigger.click());
  const dialog = app.querySelector<HTMLElement>('[role="dialog"]')!;
  expect(dialog.textContent).toContain("session/model");
  expect(dialog.textContent).toContain("50k / 200k");
  expect(dialog.textContent).toContain("Compacts at 152k");
  expect(dialog.textContent).toContain("Observed tokens");
  expect(dialog.textContent).toContain("Observed cost");
  expect(dialog.textContent).toContain("Prompt");
  expect(dialog.textContent).toContain("Output");
  expect(dialog.textContent).toContain("Cache read");
  expect(dialog.textContent).toContain("Cache write");
  expect(dialog.textContent).not.toContain("global/model");
  expect(dialog.textContent).not.toContain("799k");
});

test("a loaded default-model session keeps its server budget snapshot", async () => {
  setState({
    currentSessionId: "session-1",
    serverConfig: {
      chat_model: "new/default",
      chat_model_max_context: 300_000,
      compaction_token_trigger: 228_000,
      max_messages: 120,
    } as ServerConfig,
    usage: {
      ...initialState.usage,
      contextInputTokens: 30_000,
      contextBudget: {
        model: "old/default",
        hardLimit: 100_000,
        compactionTrigger: 76_000,
        messageLimit: 100,
      },
    },
  });
  const app = document.createElement("div");
  app.id = "app";
  const host = document.createElement("div");
  app.append(host);
  document.body.append(app);
  root = createRoot(host);

  await act(async () => root?.render(<BudgetDial />));

  const trigger = host.querySelector<HTMLButtonElement>('[aria-label="Context budget"]')!;
  expect(trigger.title).toContain("30k / 100k context tokens");
  expect(trigger.title).not.toContain("300k");
});
