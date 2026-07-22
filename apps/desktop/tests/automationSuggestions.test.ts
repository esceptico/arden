import { afterEach, expect, test } from "bun:test";
import { suggestionToPayload } from "@/api/automations";
import type { AutomationSuggestion } from "@/api/types";
import { dismissSuggestion } from "@/actions/automations";
import { getState, setState } from "@/stores/index";

const originalWindow = (globalThis as typeof globalThis & { window?: unknown }).window;

afterEach(() => {
  (globalThis as typeof globalThis & { window?: unknown }).window = originalWindow;
  setState({ automationSuggestions: null });
});

function suggestion(overrides: Partial<AutomationSuggestion> = {}): AutomationSuggestion {
  return {
    id: "s1",
    name: "Weekly arden PR digest",
    description: "Summarize merged PRs in arden this week.",
    triggers: [{ type: "time", at: "09:00", days: "mon" }],
    rationale: "You review arden PRs most mornings",
    evidence: ["recent PR reviews"],
    category: "Status reports",
    icon: "GitPullRequest",
    ...overrides,
  };
}

test("suggestionToPayload maps a time trigger to flat schedule fields", () => {
  const payload = suggestionToPayload(
    suggestion({ triggers: [{ type: "time", at: "09:00", days: "mon", every: undefined }] }),
  );

  expect(payload).toEqual({
    name: "Weekly arden PR digest",
    description: "Summarize merged PRs in arden this week.",
    from_suggestion_id: "s1",
    trigger_type: "time",
    at: "09:00",
    days: "mon",
    every: undefined,
  });
});

test("suggestionToPayload maps an event trigger to flat schedule fields", () => {
  const payload = suggestionToPayload(
    suggestion({
      id: "s2",
      triggers: [{ type: "event", event_type: "approaching", lead_minutes: 15 }],
    }),
  );

  expect(payload).toEqual({
    name: "Weekly arden PR digest",
    description: "Summarize merged PRs in arden this week.",
    from_suggestion_id: "s2",
    trigger_type: "event",
    event_type: "approaching",
    lead_minutes: 15,
  });
});

test("dismissSuggestion removes from state and calls the API", async () => {
  let request: { path: string; method?: string; body?: string } | null = null;
  (globalThis as typeof globalThis & { window?: unknown }).window = {
    ardenDesktop: {
      api: {
        request: async (_config: unknown, req: typeof request) => {
          request = req;
          return { ok: true, status: 204, statusText: "No Content", contentType: "", data: null, text: "" };
        },
      },
    },
  };

  setState({ automationSuggestions: [suggestion({ id: "s1" }), suggestion({ id: "s2" })] });

  await dismissSuggestion("s1");

  expect(getState().automationSuggestions?.map((s) => s.id)).toEqual(["s2"]);
  expect(request?.path).toBe("/automations/suggestions/s1/dismiss");
  expect(request?.method).toBe("POST");
});
