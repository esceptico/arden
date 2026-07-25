import { expect, test } from "bun:test";
import { renderToStaticMarkup } from "react-dom/server";
import { Markdown } from "../src/components/ui/Markdown";
import { WikiLinkContext } from "../src/lib/wikilink";

test("provenance tags render as chips only when the memory view opts in", () => {
  const prose = "You live in Yerevan. (from chat) Ride frequency is unknown. (inferred + gmail)";
  const wiki = renderToStaticMarkup(<Markdown content={prose} provenance />);
  expect(wiki).toContain('class="prov"');
  expect(wiki).toContain(">from chat</span>");
  expect(wiki).toContain(">inferred + gmail</span>");
  expect(wiki).not.toContain("(from chat)");

  // chat prose stays literal — no chips without the opt-in
  const chat = renderToStaticMarkup(<Markdown content={prose} />);
  expect(chat).toContain("(from chat)");
  expect(chat).not.toContain('class="prov"');
});

test("ordinary parentheticals never become provenance chips", () => {
  const prose = "Lessons (from the Replika era) still apply (from my perspective).";
  const html = renderToStaticMarkup(<Markdown content={prose} provenance />);
  expect(html).not.toContain('class="prov"');
});

test("piped wikilinks show the label and carry the target", () => {
  const html = renderToStaticMarkup(<Markdown content="See [[topics/dex|Dex]] and [[arden]]." />);
  expect(html).toContain('data-wikilink="topics/dex"');
  expect(html).toContain(">Dex</a>");
  expect(html).toContain('data-wikilink="arden"');
  expect(html).not.toContain("topics/dex|Dex");
});

test("existing Markdown artifact links use the internal navigation and preview contract", () => {
  const html = renderToStaticMarkup(
    <WikiLinkContext.Provider value={{
      exists: () => false,
      onNavigate: () => {},
      existsInline: (path) => path === "topics/o-1a.md",
      onNavigateInline: () => {},
    }}>
      <Markdown content="[O-1A](topics/o-1a.md)" />
    </WikiLinkContext.Provider>,
  );
  expect(html).toContain('class="wikilink"');
  expect(html).toContain('data-memory-path="topics/o-1a.md"');
  expect(html).not.toContain('target="_blank"');
});

test("currency amounts never consume the prose between them as inline math", () => {
  const html = renderToStaticMarkup(
    <Markdown content="Owed $5,930.99 for work through Friday; total earnings were $41,097.66." />,
  );
  expect(html).toContain("$5,930.99 for work through Friday; total earnings were $41,097.66.");
  expect(html).not.toContain('class="katex"');
});

test("double-dollar display math remains supported", () => {
  const html = renderToStaticMarkup(<Markdown content={"$$\nx^2\n$$"} />);
  expect(html).toContain('class="katex');
});
