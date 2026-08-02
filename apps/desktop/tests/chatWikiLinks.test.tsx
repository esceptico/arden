import { expect, test } from "bun:test";
import { renderToStaticMarkup } from "react-dom/server";
import { Markdown } from "@/components/ui/Markdown";
import { ChatWikiLinks } from "@/features/chat/components/ChatWikiLinks";

// The chat wiki-link contract: an agent-cited relative `.md` href — the exact
// path the wiki tools return, no `wiki/` prefix — renders as an in-app memory
// link (data-memory-path) with the hover peek, while every other link shape
// stays a normal anchor. Rendering is static; hover/click behavior lives in
// MemoryPeekLink and the store.

function chat(content: string): string {
  return renderToStaticMarkup(
    <ChatWikiLinks>
      <Markdown content={content} />
    </ChatWikiLinks>,
  );
}

test("relative .md link renders as a memory link", () => {
  const html = chat("See [Health](health.md).");
  expect(html).toContain('data-memory-path="health.md"');
  expect(html).toContain("wikilink");
  expect(html).not.toContain('target="_blank"');
});

test("nested directory paths keep the exact tool path", () => {
  const html = chat("See [Acme](projects/acme.md).");
  expect(html).toContain('data-memory-path="projects/acme.md"');
});

test("a heading anchor rides along on the link", () => {
  const html = chat("See [Sleep](health.md#sleep-quality).");
  expect(html).toContain('data-memory-path="health.md"');
  expect(html).toContain('data-memory-anchor="sleep-quality"');
});

test("backticked bare path renders as a memory link", () => {
  const html = chat("The custodian writes `areas/finance.md` nightly.");
  expect(html).toContain('data-memory-path="areas/finance.md"');
});

test("backticked code with whitespace stays plain code", () => {
  const html = chat("Run `git add README.md` first.");
  expect(html).not.toContain("data-memory-path");
});

test("external URLs ending in .md stay external links", () => {
  const html = chat("Docs at [spec](https://example.com/spec.md).");
  expect(html).not.toContain("data-memory-path");
  expect(html).toContain('target="_blank"');
});

test("same-page anchors and non-.md hrefs stay normal links", () => {
  const html = chat("Jump to [section](#details) or [site](https://example.com).");
  expect(html).not.toContain("data-memory-path");
});

test("without the chat provider, relative .md links fall through to external", () => {
  const html = renderToStaticMarkup(<Markdown content="See [Health](health.md)." />);
  expect(html).not.toContain("data-memory-path");
  expect(html).toContain('target="_blank"');
});
