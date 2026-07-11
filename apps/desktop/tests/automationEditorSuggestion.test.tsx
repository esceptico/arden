import { beforeEach, expect, mock, test } from "bun:test";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import type { AutomationSuggestion, CreateAutomationPayload } from "@/api/types";
import { suggestionToPayload } from "@/api/automations";

// Capture what the detail pane hands to the create action. The component
// imports `createAutomation` from `@/actions/automations`, which resolves to
// the same module file we mock here, so the stub intercepts the real submit
// path.
let created: CreateAutomationPayload[] = [];
let updated: { taskId: string; patch: unknown }[] = [];

mock.module("@/actions/automations", () => ({
  createAutomation: async (payload: CreateAutomationPayload) => {
    created.push(payload);
  },
  updateAutomation: async (taskId: string, patch: unknown) => {
    updated.push({ taskId, patch });
  },
  deleteAutomation: async () => {},
  runAutomation: async () => {},
  toggleAutomation: async () => {},
}));

// Imported after the mock is registered so the component picks up the stub.
const { AutomationDetail } = await import("@/features/automations/components/AutomationDetail");
const { formFromPreset, buildPayload } = await import("@/features/automations/lib/schedule");

beforeEach(() => {
  created = [];
  updated = [];
});

function suggestion(overrides: Partial<AutomationSuggestion> = {}): AutomationSuggestion {
  return {
    id: "sugg-123",
    name: "Weekly PR digest",
    description: "Summarize merged PRs this week.",
    triggers: [{ type: "time", at: "09:00", days: "mon" }],
    rationale: "You review PRs most mornings",
    evidence: ["recent PR reviews"],
    category: "Status reports",
    icon: "GitPullRequest",
    ...overrides,
  };
}

// ─── Unit: the form ↔ payload round-trip carries from_suggestion_id ──────

test("formFromPreset captures from_suggestion_id and buildPayload echoes it", () => {
  const preset = suggestionToPayload(suggestion());
  expect(preset.from_suggestion_id).toBe("sugg-123");

  const form = formFromPreset(preset);
  expect(buildPayload(form).from_suggestion_id).toBe("sugg-123");
});

test("editing the form fields does not clear from_suggestion_id", () => {
  const form = formFromPreset(suggestionToPayload(suggestion()));
  // Mirror what the component does on input: replace name/prompt/schedule.
  form.name = "Tweaked name";
  form.prompt = "Summarize merged PRs and tag reviewers.";
  form.auto_approve = true;

  const payload = buildPayload(form);
  expect(payload.description).toBe("Summarize merged PRs and tag reviewers.");
  expect(payload.from_suggestion_id).toBe("sugg-123");
});

test("a preset without a suggestion id yields no from_suggestion_id", () => {
  const form = formFromPreset({ name: "Manual", description: "do x", trigger_type: "time", at: "08:00" });
  expect("from_suggestion_id" in buildPayload(form)).toBe(false);
});

// ─── Integration: clicking Create submits the suggestion id ─────────────

test("a draft seeded from a suggestion preset submits with from_suggestion_id", async () => {
  const { appEl, root, restore } = setupDom();
  try {
    await act(async () => {
      root.render(
        <AutomationDetail
          seed={{ kind: "draft", preset: suggestionToPayload(suggestion()) }}
          onClose={() => {}}
          onCreated={() => {}}
        />,
      );
    });

    await clickCreate(appEl);

    expect(created).toHaveLength(1);
    expect(created[0].from_suggestion_id).toBe("sugg-123");

    await act(async () => root.unmount());
  } finally {
    restore();
  }
});

test("a plain draft (no suggestion) submits without from_suggestion_id", async () => {
  const { appEl, root, restore } = setupDom();
  try {
    // Seed via a preset that carries no suggestion id so the prompt is valid
    // (valid submit needs a non-empty prompt) but the id stays absent.
    await act(async () => {
      root.render(
        <AutomationDetail
          seed={{
            kind: "draft",
            preset: { name: "Morning brief", description: "Do a thing every morning." },
          }}
          onClose={() => {}}
          onCreated={() => {}}
        />,
      );
    });

    await clickCreate(appEl);

    expect(created).toHaveLength(1);
    expect(created[0].from_suggestion_id).toBeUndefined();

    await act(async () => root.unmount());
  } finally {
    restore();
  }
});

// ─── helpers ─────────────────────────────────────────────────────────

async function clickCreate(appEl: HTMLElement) {
  const create = [...appEl.querySelectorAll("button")].find((b) => b.textContent?.trim() === "Create");
  if (!create) throw new Error("missing Create button");
  await act(async () => {
    create.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
  // Let the async submit() microtasks settle so the stub records the payload.
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

function setupDom() {
  const appEl = document.createElement("div");
  appEl.id = "app";
  document.body.append(appEl);
  const root: Root = createRoot(appEl);
  const restore = () => {
    appEl.remove();
  };
  return { appEl, root, restore };
}
