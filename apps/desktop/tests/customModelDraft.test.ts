import { expect, test } from "bun:test";
import {
  canSaveCustomModelDraft,
  CUSTOM_MODEL_MAX_CONTEXT_WINDOW,
  customModelContextPatch,
  defaultCustomModelDraft,
} from "@/features/settings/lib/customModelDraft";

test("requires a model id, base URL, and positive limits before saving", () => {
  expect(canSaveCustomModelDraft(defaultCustomModelDraft())).toBe(false);

  expect(
    canSaveCustomModelDraft({
      model_id: "local/qwen",
      base_url: "http://localhost:11434/v1",
      context_window: 8192,
      max_output_tokens: 4096,
      api_key: "",
    }),
  ).toBe(true);
});

test("output tokens cannot exceed context and lowering context clamps them together", () => {
  const draft = {
    model_id: "local/qwen",
    base_url: "http://localhost:11434/v1",
    context_window: 8192,
    max_output_tokens: 4096,
    api_key: "",
  };
  expect(
    canSaveCustomModelDraft({ ...draft, context_window: 2048, max_output_tokens: 4096 }),
  ).toBe(false);
  expect(customModelContextPatch(draft, 2048)).toEqual({
    context_window: 2048,
    max_output_tokens: 2048,
  });
});

test("token limits must be whole numbers within the server contract", () => {
  const draft = {
    model_id: "local/qwen",
    base_url: "http://localhost:11434/v1",
    context_window: 8192,
    max_output_tokens: 4096,
    api_key: "",
  };
  expect(canSaveCustomModelDraft({ ...draft, context_window: 1.5 })).toBe(false);
  expect(
    canSaveCustomModelDraft({
      ...draft,
      context_window: CUSTOM_MODEL_MAX_CONTEXT_WINDOW + 1,
    }),
  ).toBe(false);
  expect(canSaveCustomModelDraft({ ...draft, max_output_tokens: 1.5 })).toBe(false);
});
