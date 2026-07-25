export interface CustomModelDraft {
  model_id: string;
  base_url: string;
  context_window: number;
  max_output_tokens: number;
  api_key: string;
}

export const CUSTOM_MODEL_MAX_CONTEXT_WINDOW = 2_000_000;

function isValidTokenLimit(value: number, max: number): boolean {
  return Number.isInteger(value) && value >= 1 && value <= max;
}

export function defaultCustomModelDraft(): CustomModelDraft {
  return {
    model_id: "",
    base_url: "",
    context_window: 8192,
    max_output_tokens: 8192,
    api_key: "",
  };
}

export function canSaveCustomModelDraft(draft: CustomModelDraft): boolean {
  return (
    draft.model_id.trim().length > 0 &&
    draft.base_url.trim().length > 0 &&
    isValidTokenLimit(draft.context_window, CUSTOM_MODEL_MAX_CONTEXT_WINDOW) &&
    isValidTokenLimit(draft.max_output_tokens, draft.context_window)
  );
}

export function customModelContextPatch(
  draft: CustomModelDraft,
  contextWindow: number,
): Pick<CustomModelDraft, "context_window" | "max_output_tokens"> {
  return {
    context_window: contextWindow,
    max_output_tokens: Math.min(draft.max_output_tokens, contextWindow),
  };
}
