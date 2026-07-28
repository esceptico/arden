import { afterEach, expect, test } from "bun:test";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { WikiMaintenanceReviewSheet, WikiMaintenanceReviewStatusSheet } from "@/features/memory/components/WikiMaintenanceReviewSheet";
import type { WikiMaintenanceReview } from "@/api/wikiMaintenance";

let root: Root | null = null;

function review(overrides: Partial<WikiMaintenanceReview> = {}): WikiMaintenanceReview {
  return {
    reviewId: "review-1",
    blockingCommitId: "commit-1",
    generation: 2,
    status: "needs_review",
    summary: "A page update needs your answer.",
    proposal: {
      kind: "maintenance_updates",
      summary: "Apply one small update.",
      updates: [{ pageId: "page-1", title: "Project", aliases: ["Work"], body: "# Project\n" }],
    },
    createdAt: "2026-07-28T00:00:00Z",
    updatedAt: "2026-07-28T00:00:00Z",
    resolvedAt: null,
    decisionNote: null,
    ...overrides,
  };
}

async function mount(props: Partial<React.ComponentProps<typeof WikiMaintenanceReviewSheet>> = {}) {
  const app = document.createElement("div");
  app.id = "app";
  document.body.append(app);
  root = createRoot(app);
  const resolves: unknown[] = [];
  const refreshes: string[] = [];
  await act(async () => {
    root?.render(
      <WikiMaintenanceReviewSheet
        config={{ serverUrl: "http://localhost:6877", apiKey: "" }}
        review={review()}
        position={1}
        total={1}
        pending={false}
        checking={false}
        reconciliationRequired={false}
        error={null}
        manual={false}
        note=""
        onManualChange={() => {}}
        onNoteChange={() => {}}
        onReconcile={() => { refreshes.push("refresh"); }}
        onResolve={(_item, decision) => { resolves.push(decision); }}
        {...props}
      />,
    );
  });
  return { app, resolves, refreshes };
}

function button(name: string): HTMLButtonElement {
  const found = [...document.querySelectorAll<HTMLButtonElement>("button")]
    .find((candidate) => candidate.textContent === name);
  if (!found) throw new Error(`Missing button: ${name}`);
  return found;
}

afterEach(async () => {
  if (root) {
    await act(async () => root?.unmount());
    root = null;
  }
  delete (globalThis.window as unknown as { ardenDesktop?: unknown }).ardenDesktop;
  document.body.replaceChildren();
});

test("maintenance update question is blocking and routes explicit answers", async () => {
  const { app, resolves } = await mount();
  expect(app.querySelector("[data-wiki-maintenance-review]")).not.toBeNull();
  expect(app.textContent).toContain("A page update needs your answer.");
  expect(app.textContent).toContain("Project");

  await act(async () => {
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true }));
  });
  expect(app.querySelector("[data-wiki-maintenance-review]")).not.toBeNull();

  await act(async () => {
    button("Reject").click();
    button("Accept change").click();
  });
  expect(resolves).toEqual([{ action: "reject" }, { action: "accept" }]);
});

test("initial check error remains blocking and retryable", async () => {
  const app = document.createElement("div");
  app.id = "app";
  document.body.append(app);
  root = createRoot(app);
  const refreshes: string[] = [];
  await act(async () => {
    root?.render(<WikiMaintenanceReviewStatusSheet error="offline" onRetry={() => { refreshes.push("refresh"); }} />);
  });
  expect(app.textContent).toContain("Couldn’t check Wiki Maintenance");
  expect(app.textContent).toContain("offline");
  await act(async () => { button("Retry check").click(); });
  expect(refreshes).toEqual(["refresh"]);
});

test("manual evidence is paginated, retryable, and requires a note", async () => {
  const calls: string[] = [];
  let failedOnce = true;
  (globalThis.window as unknown as { ardenDesktop: unknown }).ardenDesktop = {
    api: {
      request: async (_config: unknown, request: { path: string }) => {
        calls.push(request.path);
        if (failedOnce) {
          failedOnce = false;
          return { ok: false, status: 503, statusText: "Unavailable", contentType: "application/json", text: "", data: { detail: "temporarily unavailable" } };
        }
        const second = request.path.includes("change_index=1");
        return {
          ok: true,
          status: 200,
          statusText: "OK",
          contentType: "application/json",
          text: "",
          data: {
            review_id: "review-1",
            generation: 2,
            actor: "user:desktop",
            origin: "user",
            reason: "manual review",
            occurred_at: "2026-07-28T00:00:00Z",
            changeIndex: second ? 1 : 0,
            changeCount: 2,
            diffOffset: 0,
            diffEndOffset: 20,
            moreInChange: false,
            previousCursor: second ? { changeIndex: 0, diffOffset: 0 } : null,
            nextCursor: second ? null : { changeIndex: 1, diffOffset: 0 },
            change: { resourceId: second ? "page-2" : "page-1", path: second ? "topics/next.md" : "topics/current.md", action: "modify", unifiedDiff: "- old\n+ new", displayLossy: false },
          },
        };
      },
    },
  };
  const { app, resolves } = await mount({
    manual: true,
    review: review({
      summary: "The evidence is too large.",
      proposal: {
        kind: "manual_evidence_review",
        section: "History",
        actualBytes: 120,
        actualBytesAtLeast: false,
        limitBytes: 100,
      },
    }),
  });
  await act(async () => {});
  expect(app.textContent).toContain("Couldn’t load evidence");
  await act(async () => {
    button("Retry evidence").click();
    await Bun.sleep(0);
  });
  expect(app.textContent).toContain("topics/current.md");
  expect(calls[1]).toContain("change_index=0");

  await act(async () => {
    button("More evidence").click();
    await Bun.sleep(0);
  });
  expect(app.textContent).toContain("topics/next.md");
  expect(calls.at(-1)).toContain("change_index=1");

  await act(async () => { button("Resolve manually").click(); });
  expect(app.textContent).toContain("A manual resolution note is required.");
  expect(resolves).toEqual([]);

  await act(async () => {
    root?.render(
      <WikiMaintenanceReviewSheet
        config={{ serverUrl: "http://localhost:6877", apiKey: "" }}
        review={review({
          summary: "The evidence is too large.",
          proposal: { kind: "manual_evidence_review", section: "History", actualBytes: 120, actualBytesAtLeast: false, limitBytes: 100 },
        })}
        position={1}
        total={1}
        pending={false}
        checking={false}
        reconciliationRequired={false}
        error={null}
        manual={true}
        note="Trim the obsolete evidence manually."
        onManualChange={() => {}}
        onNoteChange={() => {}}
        onReconcile={() => {}}
        onResolve={(_item, decision) => { resolves.push(decision); }}
      />,
    );
  });
  await act(async () => { button("Resolve manually").click(); });
  expect(resolves).toEqual([{ action: "resolve-manually", note: "Trim the obsolete evidence manually." }]);
});

test("answer controls are disabled while status is checking or reconciling", async () => {
  const { app } = await mount({ checking: true });
  expect(button("Reject").disabled).toBe(true);
  expect(button("Accept change").disabled).toBe(true);

  await act(async () => {
    root?.render(
      <WikiMaintenanceReviewSheet
        config={{ serverUrl: "http://localhost:6877", apiKey: "" }}
        review={review()}
        position={1}
        total={1}
        pending={false}
        checking={false}
        reconciliationRequired
        error="outcome unknown"
        manual={false}
        note=""
        onManualChange={() => {}}
        onNoteChange={() => {}}
        onReconcile={() => {}}
        onResolve={() => {}}
      />,
    );
  });
  expect(app.textContent).toContain("Check request status");
  expect(app.querySelector("[data-wiki-maintenance-review]")).not.toBeNull();
});

test("empty proposals have no accept action and manual cancel stays in the review", async () => {
  const manualChanges: boolean[] = [];
  const { app } = await mount({
    review: review({ proposal: { kind: "maintenance_updates", summary: "Nothing executable.", updates: [] } }),
  });
  expect([...app.querySelectorAll("button")].some((item) => item.textContent?.startsWith("Accept"))).toBe(false);

  await act(async () => {
    root?.render(
      <WikiMaintenanceReviewSheet
        config={{ serverUrl: "http://localhost:6877", apiKey: "" }}
        review={review({ proposal: null })}
        position={1}
        total={1}
        pending={false}
        checking={false}
        reconciliationRequired={false}
        error={null}
        manual={true}
        note=""
        onManualChange={(value) => { manualChanges.push(value); }}
        onNoteChange={() => {}}
        onReconcile={() => {}}
        onResolve={() => {}}
      />,
    );
  });
  await act(async () => { button("Cancel").click(); });
  expect(manualChanges).toEqual([false]);
  expect(app.querySelector("[data-wiki-maintenance-review]")).not.toBeNull();
});
