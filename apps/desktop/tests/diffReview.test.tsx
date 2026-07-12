import { afterEach, expect, test } from "bun:test";
import { act, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { DiffReview } from "@/components/ui/DiffReview";
import type {
  DiffReviewDecision,
  DiffReviewOperation,
} from "@/components/ui/diffReviewTypes";

const roots = new Set<Root>();

function setup() {
  const host = document.createElement("div");
  document.body.append(host);
  const root = createRoot(host);
  roots.add(root);
  return { host, root };
}

afterEach(async () => {
  for (const root of roots) await act(async () => root.unmount());
  roots.clear();
  document.body.replaceChildren();
});

const askOperation: DiffReviewOperation = {
  kind: "ASK",
  id: "preview-1:operation:1",
  question: "Does removing the old plan only edit the note, or forget the memory?",
  targetIds: ["record-old"],
};

const addOperation: DiffReviewOperation = {
  kind: "ADD",
  id: "preview-1:operation:0",
  text: "The replacement plan is durable.",
  memoryKind: "fact",
  scope: { kind: "user", key: null },
};

const files = {
  before: { path: "topics/a.md", content: "# A\n\nThe old plan." },
  after: { path: "topics/a.md", content: "# A\n\nThe replacement plan." },
};

test("requires an explicit decision for every ASK and labels server operations", async () => {
  const decisions: Array<[string, DiffReviewDecision]> = [];
  const { host, root } = setup();
  await act(async () => root.render(
    <DiffReview
      {...files}
      operations={[addOperation, askOperation]}
      decisions={{}}
      onDecision={(id, decision) => decisions.push([id, decision])}
    />,
  ));

  const apply = host.querySelector<HTMLButtonElement>('button[aria-label="Apply changes"]')!;
  const note = host.querySelector<HTMLElement>('[role="radio"][aria-label="Note only"]')!;
  const forget = host.querySelector<HTMLElement>('[role="radio"][aria-label="Forget memory"]')!;
  expect(apply.disabled).toBe(true);
  expect(note.getAttribute("aria-checked")).toBe("false");
  expect(forget.getAttribute("aria-checked")).toBe("false");
  expect(host.textContent).toContain("Add memory");
  expect(host.textContent).toContain("Needs decision");
  expect(host.textContent).toContain("The replacement plan is durable.");
  expect(host.textContent).toContain(askOperation.question);

  await act(async () => note.click());
  expect(decisions).toEqual([[
    askOperation.id,
    { choice: "note_only", targetIds: askOperation.targetIds },
  ]]);
});

test("decision radios support roving keyboard traversal with no default", async () => {
  const changes: DiffReviewDecision[] = [];
  function Harness() {
    const [decisions, setDecisions] = useState<Record<string, DiffReviewDecision>>({});
    return (
      <DiffReview
        {...files}
        operations={[askOperation]}
        decisions={decisions}
        onDecision={(id, decision) => {
          changes.push(decision);
          setDecisions((current) => ({ ...current, [id]: decision }));
        }}
      />
    );
  }

  const { host, root } = setup();
  await act(async () => root.render(<Harness />));
  const note = host.querySelector<HTMLElement>('[role="radio"][aria-label="Note only"]')!;
  const forget = host.querySelector<HTMLElement>('[role="radio"][aria-label="Forget memory"]')!;
  expect(note.tabIndex).toBe(0);
  expect(forget.tabIndex).toBe(-1);

  await act(async () => {
    note.focus();
    note.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true }));
  });

  expect(document.activeElement).toBe(forget);
  expect(forget.getAttribute("aria-checked")).toBe("true");
  expect(changes.at(-1)).toEqual({ choice: "forget_memory", targetIds: ["record-old"] });
  expect(host.querySelector<HTMLButtonElement>('button[aria-label="Apply changes"]')?.disabled).toBe(false);
});

test("exposes rendered and raw modes with ntrp split and stacked layout inputs", async () => {
  const { host, root } = setup();
  await act(async () => root.render(
    <DiffReview {...files} operations={[]} decisions={{}} onDecision={() => {}} layout="split" />,
  ));

  const review = host.querySelector<HTMLElement>('[data-diff-review]')!;
  expect(review.dataset.layout).toBe("split");
  expect(host.querySelector('[role="tab"][aria-selected="true"]')?.textContent).toBe("Rendered");
  expect(host.querySelector('[role="tabpanel"]')?.getAttribute("aria-label")).toBe("Rendered changes");
  expect(host.querySelector('[data-rendered-prose-diff]')).not.toBeNull();

  const raw = host.querySelector<HTMLButtonElement>('[role="tab"][aria-label="Raw Markdown"]')!;
  await act(async () => raw.click());
  expect(raw.getAttribute("aria-selected")).toBe("true");
  expect(host.querySelector('[role="tabpanel"]')?.getAttribute("aria-label")).toBe("Raw Markdown changes");
  expect(host.querySelector('[data-raw-diff-renderer]')).not.toBeNull();

  await act(async () => root.render(
    <DiffReview {...files} operations={[]} decisions={{}} onDecision={() => {}} layout="stacked" />,
  ));
  expect(host.querySelector<HTMLElement>('[data-diff-review]')?.dataset.layout).toBe("stacked");
});

test("rendered prose uses semantic headings and word marks without flattening context", async () => {
  const unchanged = Array.from({ length: 12 }, (_, index) => `Shared paragraph ${index + 1}.`);
  const before = { path: "topics/long.md", content: ["# Topic", ...unchanged, "The old durable fact."].join("\n") };
  const after = { path: "topics/long.md", content: ["# Topic", ...unchanged, "The new durable fact."].join("\n") };
  const { host, root } = setup();
  await act(async () => root.render(
    <DiffReview before={before} after={after} operations={[]} decisions={{}} onDecision={() => {}} />,
  ));

  expect(host.querySelector("h1")?.textContent).toBe("Topic");
  expect(host.querySelector('[data-change="removed"]')?.textContent).toContain("old");
  expect(host.querySelector('[data-change="added"]')?.textContent).toContain("new");
  expect(host.textContent).toContain("Shared paragraph 6.");
});

test("rendered prose preserves real context between separate changes", async () => {
  const middle = Array.from({ length: 10 }, (_, index) => `Stable middle ${index + 1}.`);
  const before = { path: "topics/two.md", content: ["# Topic", "Old introduction.", ...middle, "Old ending."].join("\n") };
  const after = { path: "topics/two.md", content: ["# Topic", "New introduction.", ...middle, "New ending."].join("\n") };
  const { host, root } = setup();
  await act(async () => root.render(
    <DiffReview before={before} after={after} operations={[]} decisions={{}} onDecision={() => {}} />,
  ));

  expect(host.textContent).toContain("Old introduction.");
  expect(host.textContent).toContain("New introduction.");
  expect(host.textContent).toContain("Old ending.");
  expect(host.textContent).toContain("New ending.");
  expect(host.textContent).toContain("Stable middle 5.");
});

test("rendered prose omits raw frontmatter syntax", async () => {
  const { host, root } = setup();
  await act(async () => root.render(
    <DiffReview
      before={{ path: "topic.md", content: "---\nscope: user\n---\n# Topic\nOld prose." }}
      after={{ path: "topic.md", content: "---\nscope: area\n---\n# Topic\nNew prose." }}
      operations={[]}
      decisions={{}}
      onDecision={() => {}}
    />,
  ));
  const prose = host.querySelector('[data-rendered-prose-diff]')!;
  expect(prose.textContent).not.toContain("scope:");
  expect(prose.textContent).not.toContain("---");
  expect(prose.textContent).toContain("New prose.");
});

test("rendered mode uses the real Markdown pipeline for rich block semantics", async () => {
  const before = [
    "---", "scope: user", "---",
    "# Heading one", "## Heading two", "### Heading three",
    "#### Heading four", "##### Heading five", "###### Heading six",
    "", "1. First", "2. Second", "",
    "[Source](https://example.com/source)", "",
    "| Name | State |", "| --- | --- |", "| ntrp | old |", "",
    "```ts", "const state = 'old';", "```", "",
    "![Diagram](https://example.com/diagram.png)",
  ].join("\n");
  const after = before.replace("| ntrp | old |", "| ntrp | new |").replace("'old'", "'new'");
  const { host, root } = setup();
  await act(async () => root.render(
    <DiffReview
      before={{ path: "rich.md", content: before }}
      after={{ path: "rich.md", content: after }}
      operations={[]}
      decisions={{}}
      onDecision={() => {}}
    />,
  ));

  const prose = host.querySelector('[data-rendered-prose-diff]')!;
  expect(prose.querySelectorAll(".md h1").length).toBe(2);
  expect(prose.querySelectorAll(".md h2").length).toBe(2);
  expect(prose.querySelectorAll(".md h3").length).toBe(2);
  expect(prose.querySelectorAll(".md h4").length).toBe(2);
  expect(prose.querySelectorAll(".md h5").length).toBe(2);
  expect(prose.querySelectorAll(".md h6").length).toBe(2);
  expect(prose.querySelectorAll("ol").length).toBe(2);
  expect(prose.querySelectorAll("table").length).toBe(2);
  expect(prose.querySelectorAll('code[class*="language-ts"]').length).toBe(2);
  expect(prose.querySelector<HTMLAnchorElement>('a[href="https://example.com/source"]')?.target).toBe("_blank");
  expect(prose.querySelector<HTMLImageElement>('img[alt="Diagram"]')?.src).toBe("https://example.com/diagram.png");
  expect(prose.querySelector('[data-change="removed"]')?.textContent).toContain("old");
  expect(prose.querySelector('[data-change="added"]')?.textContent).toContain("new");
  expect(prose.textContent).not.toContain("scope:");
});

test("memory effects own a bounded accessible scroll region", async () => {
  const operations: DiffReviewOperation[] = Array.from({ length: 60 }, (_, index) => ({
    kind: "ADD",
    id: `operation-${index}`,
    text: `Durable operation ${index}`,
    memoryKind: "fact",
    scope: { kind: "user", key: null },
  }));
  const { host, root } = setup();
  await act(async () => root.render(
    <DiffReview {...files} operations={operations} decisions={{}} onDecision={() => {}} />,
  ));
  const effects = host.querySelector<HTMLElement>('[data-memory-effects-scroll]')!;
  expect(effects).not.toBeNull();
  expect(effects.getAttribute("role")).toBe("region");
  expect(effects.getAttribute("aria-label")).toBe("Memory effects");
  expect(effects.tabIndex).toBe(0);
  expect(effects.className).toContain("overflow-y-auto");
  expect(effects.className).toContain("min-h-0");
  expect(effects.textContent).toContain("Durable operation 59");
});

test("reduced motion disables review transitions", async () => {
  const originalMatchMedia = window.matchMedia;
  window.matchMedia = ((query: string) => ({
    matches: query === "(prefers-reduced-motion: reduce)",
    media: query,
    onchange: null,
    addEventListener() {},
    removeEventListener() {},
    addListener() {},
    removeListener() {},
    dispatchEvent: () => false,
  })) as typeof window.matchMedia;
  const { host, root } = setup();
  await act(async () => root.render(
    <DiffReview {...files} operations={[]} decisions={{}} onDecision={() => {}} />,
  ));
  expect(host.querySelector<HTMLElement>('[data-diff-review]')?.dataset.motion).toBe("reduced");
  expect(host.querySelector<HTMLElement>('[data-rendered-prose-diff]')?.style.transition).toBe("none");
  window.matchMedia = originalMatchMedia;
});
