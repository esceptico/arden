import { afterEach, expect, test } from "bun:test";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { CustomModelsPanel } from "@/features/settings/components/CustomModelsPanel";
import {
  defaultCustomEmbeddingModelDraft,
  defaultCustomModelDraft,
} from "@/features/settings/lib/customModelDraft";

let root: Root | null = null;

afterEach(async () => {
  if (root) await act(async () => root?.unmount());
  root = null;
  document.body.replaceChildren();
});

test("shows configured custom embeddings and submits a valid OpenAI-compatible draft", async () => {
  const app = document.createElement("div");
  document.body.append(app);
  root = createRoot(app);
  let creates = 0;

  await act(async () => {
    root?.render(
      <CustomModelsPanel
        provider={{
          id: "custom",
          name: "Custom",
          connected: true,
          key_hint: null,
          from_env: false,
          models: [],
          embedding_models: ["Qwen3-Embedding-0.6B"],
          custom_embedding_models: [
            {
              id: "Qwen3-Embedding-0.6B",
              base_url: "http://127.0.0.1:8081/v1",
              dimensions: 1024,
            },
          ],
        }}
        draft={defaultCustomModelDraft()}
        embeddingDraft={{
          ...defaultCustomEmbeddingModelDraft(),
          model_id: "another-embedding-model",
          base_url: "https://embeddings.example/v1",
        }}
        pendingId={null}
        onDraftChange={() => undefined}
        onEmbeddingDraftChange={() => undefined}
        onCreate={() => undefined}
        onCreateEmbedding={() => {
          creates += 1;
        }}
        onDelete={() => undefined}
        onDeleteEmbedding={() => undefined}
      />,
    );
  });

  expect(app.textContent).toContain("Qwen3-Embedding-0.6B");
  expect(app.textContent).toContain("1,024 dimensions");
  const section = app.querySelector<HTMLElement>('[aria-labelledby="custom-embedding-models-title"]');
  const add = Array.from(section?.querySelectorAll<HTMLButtonElement>("button") ?? [])
    .find((button) => button.textContent?.includes("Add"));
  expect(add?.disabled).toBe(false);

  await act(async () => add?.click());
  expect(creates).toBe(1);
});
