import { expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { renderToStaticMarkup } from "react-dom/server";
import { Markdown } from "@/components/ui/Markdown";

const chat = readFileSync(new URL("../src/design/chat.css", import.meta.url), "utf8");
const modelPickers = readFileSync(
  new URL("../src/design/model-pickers.css", import.meta.url),
  "utf8",
);
const typeset = readFileSync(new URL("../src/design/typeset.css", import.meta.url), "utf8");
const markdown = readFileSync(new URL("../src/components/ui/Markdown.tsx", import.meta.url), "utf8");
const markdownViewer = readFileSync(new URL("../src/components/ui/MarkdownViewer.tsx", import.meta.url), "utf8");
const automations = readFileSync(
  new URL("../src/features/automations/components/AutomationsModal.tsx", import.meta.url),
  "utf8",
);
const assistant = readFileSync(
  new URL("../src/features/chat/components/AssistantMessage.tsx", import.meta.url),
  "utf8",
);
const user = readFileSync(
  new URL("../src/features/chat/components/UserMessage.tsx", import.meta.url),
  "utf8",
);
const actions = readFileSync(
  new URL("../src/features/chat/components/MessageActions.tsx", import.meta.url),
  "utf8",
);
const chatHeader = readFileSync(
  new URL("../src/features/chat/components/Chat.tsx", import.meta.url),
  "utf8",
);
const budget = readFileSync(
  new URL("../src/features/chat/components/BudgetDial.tsx", import.meta.url),
  "utf8",
);
const selectors = readFileSync(
  new URL("../src/components/ui/ModelPickers.tsx", import.meta.url),
  "utf8",
);
const composer = readFileSync(
  new URL("../src/features/chat/components/Composer.tsx", import.meta.url),
  "utf8",
);
const composerToolbar = readFileSync(
  new URL("../src/features/chat/components/ComposerToolbar.tsx", import.meta.url),
  "utf8",
);

test("Chat assistant adopts the shared Board typeset contract", () => {
  expect(assistant).toMatch(/<Markdown[\s\S]*?codeChrome=\{false\}[\s\S]*?className="board-assistant__prose/s);
  // Prose follows the user's configured UI size; it used to sit a step above it.
  expect(typeset).toContain("--typeset-size: var(--text-base);");
  expect(typeset).toContain("--typeset-leading: 1.5;");
  expect(typeset).toContain("--typeset-flow: 1em;");
  expect(typeset).toContain("font-size: calc(var(--typeset-size) * 1.125);");
  expect(typeset).toContain(".md.typeset :where(ul, ol)");
  expect(typeset).toContain(".md.typeset :where(table)");
  expect(chat).not.toContain(".board-assistant .board-assistant__prose :where(ul, ol)");
  expect(chat).not.toContain(".board-assistant .board-assistant__prose :where(table)");
  const chatHeading = chat.match(/\.board-assistant \.board-assistant__prose :where\(h2\)\s*\{([^}]*)\}/)?.[1] ?? "";
  expect(chatHeading).not.toContain("font-size");
  expect(chatHeading).not.toContain("line-height");
  // chat.css loads AFTER typeset.css at equal specificity, so any flow
  // declaration here silently outranks the entire scale. It used to set
  // `p { margin: 0 }` and `h2 { margin: 0 0 8px }`, which is why chat showed a
  // heading glued to the block above it and no gap whatsoever after a list —
  // every typeset fix was real and invisible. Chat constrains measure and ink;
  // flow belongs to typeset.css alone.
  const chatProse = chat.match(/\.board-assistant \.board-assistant__prose :where\([^)]*\)\s*\{([^}]*)\}/g) ?? [];
  expect(chatProse.length).toBeGreaterThan(0);
  for (const rule of chatProse) expect(rule).not.toMatch(/(^|[\s;{])margin/);
  expect(chat).toContain("max-inline-size: 40.625rem");
});

test("Chat rich text uses the mock's unadorned code and note rhythm", () => {
  expect(markdown).toContain("streaming ? { pre: StreamingPreBlock } : codeChrome ? { pre: PreBlock } : { pre: TypesetPreBlock }");
  expect(typeset).toContain(".md.typeset.typeset-notes { font-size: var(--typeset-size); }");
  expect(typeset).toContain("padding: .125em .3em;");
  expect(typeset).toContain("tab-size: 2;");
  expect(typeset).toContain("border-inline-start: 2px solid var(--typeset-rule);");
  expect(typeset).toContain("border-collapse: separate;");
  expect(typeset).toContain("content: none;");

  const markup = renderToStaticMarkup(
    <Markdown codeChrome={false} content={"`inline`\n\n```ts\nconst answer = 42;\n```"} />,
  );
  expect(markup).toContain('class="md typeset typeset-notes"');
  expect(markup).toContain("<pre><code");
  expect(markup).not.toContain("code-block");
  expect(markup).not.toContain("Copy code");
});

test("Board typeset preserves Mermaid rendering without utility code chrome", () => {
  expect(markdown).toContain('if (lang === "mermaid" && rawText.trim()) return <Mermaid code={rawText} />;');
  const markup = renderToStaticMarkup(
    <Markdown codeChrome={false} content={"```mermaid\ngraph TD; A-->B\n```"} />,
  );
  expect(markup).not.toContain('class="language-mermaid"');
  expect(markup).toContain("Rendering…");
});

test("utility Markdown still detects fenced languages and keeps its copy chrome", () => {
  const markup = renderToStaticMarkup(
    <Markdown content={"```ts\nconst answer = 42;\n```"} />,
  );
  expect(markup).toContain('class="code-block"');
  expect(markup).toContain('class="code-block-lang">ts</span>');
  expect(markup).toContain('aria-label="Copy code"');
});

test("Markdown sheets and automation results opt into the shared typeset", () => {
  expect(markdownViewer).toContain("<Markdown content={view.content} codeChrome={false} />");
  expect(automations).toContain("<Markdown content={content} codeChrome={false} />");
  expect(markdownViewer).not.toContain("text-md leading-[1.6] text-ink");
  expect(automations).not.toContain("text-md leading-[1.6] text-ink");
});

test("Chat user bubbles keep the mock's reading size and padding", () => {
  const bubble = chat.match(/\.board-user__bubble\s*\{([^}]*)\}/)?.[1] ?? "";
  expect(bubble).toContain("padding: 10px 14px");
  // The bubble is chrome — geometry and surface only. Its type comes from
  // typeset.css like every other prose surface, so a prompt and the answer
  // below it read at the same size instead of the bubble running a step ahead.
  expect(bubble).not.toContain("font-size");
  expect(bubble).not.toContain("line-height");
  expect(bubble).toContain("border-radius: var(--r-shell)");
  expect(user).not.toContain("px-3.5 py-2");
  expect(user).not.toContain('leading-[1.5]');
});

test("Chat user metadata clears the bubble by the mock's final 6px gap", () => {
  const content = chat.match(/\.board-user__content\s*\{([^}]*)\}/)?.[1] ?? "";
  const metadata = chat.match(/\.board-message-actions--user\s*\{([^}]*)\}/)?.[1] ?? "";

  expect(user).toContain('className="board-user__content flex max-w-full flex-col items-end"');
  expect(content).toContain("margin-block-end: .875rem");
  expect(metadata).toContain("height: 1.625rem");
  expect(metadata).toContain("margin: calc(-1 * var(--space-2)) 0 var(--space-2)");
  expect(actions).toContain('"board-message-actions flex items-center gap-1"');
  expect(actions).not.toContain('size="sm"');
  expect(actions).toContain('role === "user" ? "order-first mr-0.5 text-2xs"');
  expect(user).not.toContain("mb-1.5");
});

test("Chat header leaves scroll veiling to the shared progressive blur", () => {
  const header = chat.match(/\.board-chat__header\s*\{([^}]*)\}/)?.[1] ?? "";
  expect(header).toContain("background: transparent");
  expect(header).not.toContain("linear-gradient");
});

test("Chat keeps the compact budget control and shared model picker classes", () => {
  expect(chatHeader).toContain("board-chat__header-content chat-head-inner");
  expect(budget).toContain('"budget-trigger composer-toolbar-control inline-flex');
  expect(selectors).toContain('className="model-picker__trigger model-picker__model-trigger group"');
  expect(selectors).toContain('className="model-picker__trigger model-picker__effort-trigger group"');
  expect(selectors).toContain('className="model-picker__current-effort"');
  expect(chat).toContain(
    ".chat-head-inner { padding-left: calc(var(--chrome-chat-title-inset) - .125rem); }",
  );
  expect(chat).toContain(".board-composer .budget-trigger {\n    width: var(--icon-button-size);");
  // A declared variant, not the shared sheet keying off a feature class.
  expect(modelPickers).toContain(".model-picker--inline {");
  expect(modelPickers).not.toContain(".board-composer .model-picker");
});

test("Chat composer keeps the mock's 66px input and 42px toolbar geometry", () => {
  expect(composer).toContain("board-composer__input-row flex min-h-[66px]");
  expect(composerToolbar).toContain('className="composer-tool-button composer-toolbar-control"');
  expect(composerToolbar).toContain('className="composer-tool-button composer-toolbar-control composer-mode-button"');
  expect(chat).toContain(".board-composer__input-row { min-height: 4.125rem; }");
  expect(chat).toContain(".board-composer__toolbar { height: 2.625rem; min-height: 2.625rem; flex: none; }");
  expect(chat).toContain(".board-composer .composer-tool-button { min-height: var(--control-size-large); }");
});

test("Chat composer uses the mock's single surface focus ring", () => {
  const composerRule = chat.match(/\.board-composer\s*\{([^}]*)\}/)?.[1] ?? "";
  const focusRule = chat.match(/\.board-composer:focus-within\s*\{([^}]*)\}/)?.[1] ?? "";
  const inputRule = chat.match(/\.board-composer__input\s*\{([^}]*)\}/)?.[1] ?? "";

  expect(composerRule).toContain("box-shadow: var(--composer-shadow)");
  expect(focusRule).toContain("0 0 0 var(--border-width)");
  expect(focusRule).not.toContain("inset");
  expect(inputRule).toContain("border: 0");
  expect(inputRule).toContain("outline: 0");
  expect(inputRule).toContain("box-shadow: none");
});

test("a link's favicon covers its fallback globe instead of dropping below it", () => {
  // The favicon is chrome absolutely positioned inside its own 1em box, not
  // prose content — so it is excluded from the flow rhythm every other image
  // gets. It sets `margin: 0` itself, but at equal specificity and EARLIER in
  // the file, so the generic rule won: inside a list item, where neither the
  // `p img` nor the `li > :first-child` exception reaches it, the leading
  // margin pushed it a full line below the globe and both showed at once.
  expect(typeset).toContain(
    ".md.typeset :where(img:not(.external-link__favicon), video) { max-width: 100%; height: auto; margin-block: var(--typeset-flow) 0;",
  );
  expect(typeset).toMatch(
    /\.md :where\(\.external-link__favicon\) \{[^}]*position: absolute;[^}]*inset: 0;[^}]*margin: 0;/,
  );
  expect(typeset).toMatch(
    /\.md :where\(\.external-link__icon\) \{[^}]*position: relative;[^}]*width: 1em;[^}]*height: 1em;/,
  );

  // And the globe is a fallback, not a backdrop — a favicon with any
  // transparency showed it through from underneath. It goes when the real icon
  // arrives, and stays when none does (onError removes the img instead).
  const markdown = readFileSync(new URL("../src/components/ui/Markdown.tsx", import.meta.url), "utf8");
  expect(markdown).toContain(
    'onLoad={(event) => event.currentTarget.parentElement?.setAttribute("data-favicon", "loaded")}',
  );
  expect(markdown).toContain("onError={(event) => event.currentTarget.remove()}");
  expect(typeset).toContain(
    '.md :where(.external-link__icon[data-favicon="loaded"] .external-link__fallback) { display: none; }',
  );
});

test("the composer toolbar keeps one gap across the whole row", () => {
  const pickerCss = readFileSync(new URL("../src/design/model-pickers.css", import.meta.url), "utf8");
  const toolbar = readFileSync(new URL("../src/features/chat/components/ComposerToolbar.tsx", import.meta.url), "utf8");

  // The row's gap is the toolbar's gap-1.5. The picker's `inline` variant
  // matches it; the `field` default carries Settings' wider --space-2, which
  // otherwise put 8px between model and effort against 6px across the row.
  expect(toolbar).toContain('className="board-composer__toolbar flex items-center gap-1.5');
  expect(pickerCss).toMatch(/\.model-picker--inline \{[^}]*width: auto;[^}]*gap: var\(--space-1-5\);/);
  // Settings keeps the default geometry; only the inline variant narrows.
  expect(pickerCss).toMatch(/\.model-picker--field \{[^}]*width: 276px;[^}]*gap: var\(--space-2\);/);
});

test("typeset.css is the only prose system", () => {
  // Prose is not opt-in: every Markdown surface, chrome or not, is .md.typeset.
  // The utility variant (copy control, language strip) still gets it.
  expect(renderToStaticMarkup(<Markdown content={"text"} />)).toContain(
    'class="md typeset typeset-notes"',
  );

  // base.css carried a full shadcn/typeset port and a second `.typeset-notes`
  // var block. Both lost every cascade — `typeset` is only ever emitted with
  // `md`, so `.md.typeset` outranked them, and `@layer components` lost to the
  // unlayered copy regardless. 78 rules, zero computed-style effect.
  const base = readFileSync(new URL("../src/design/base.css", import.meta.url), "utf8");
  expect(base).not.toMatch(/^\s*\.typeset(-notes)?[\s,{]/m);

  // styles.css keeps only chrome typeset does not own.
  const styles = readFileSync(new URL("../src/styles.css", import.meta.url), "utf8");
  for (const prose of [".md p", ".md ul", ".md ol", ".md li", ".md blockquote", ".md table", ".md h1", ".md pre", ".md code"]) {
    expect(styles).not.toContain(prose + " {");
  }
  expect(styles).toContain(".md .code-block {");
  expect(styles).toContain(".md a.wikilink {");
});

test("a user prompt renders as Markdown but keeps the lines it was typed with", () => {
  const userSrc = readFileSync(
    new URL("../src/features/chat/components/UserMessage.tsx", import.meta.url),
    "utf8",
  );
  // Was a raw <span class="whitespace-pre-wrap">{text}</span>.
  expect(userSrc).not.toContain('<span className="whitespace-pre-wrap">');
  expect(userSrc).toContain('className="typeset-verbatim"');

  const markup = renderToStaticMarkup(
    <Markdown codeChrome={false} className="typeset-verbatim" content={"do this:\n- alpha\n- beta"} />,
  );
  expect(markup).toContain("<ul>");
  expect(markup).toContain("typeset-verbatim");

  // Markdown collapses a single newline; someone who pressed Enter meant a
  // break, so the paragraph keeps its whitespace instead of reflowing.
  expect(typeset).toContain(
    ".md.typeset.typeset-verbatim :where(p) { white-space: pre-wrap; }",
  );
});
