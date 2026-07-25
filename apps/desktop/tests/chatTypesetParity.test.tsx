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
  expect(assistant).toMatch(/<Markdown[\s\S]*?typeset[\s\S]*?className="board-assistant__prose/s);
  expect(typeset).toContain("--typeset-size: var(--text-md);");
  expect(typeset).toContain("--typeset-leading: 1.6;");
  expect(typeset).toContain("--typeset-flow: 1em;");
  expect(typeset).toContain("font-size: calc(var(--typeset-size) * 1.125);");
  expect(typeset).toContain(".md.typeset :where(ul, ol)");
  expect(typeset).toContain(".md.typeset :where(table)");
  expect(chat).not.toContain(".board-assistant .board-assistant__prose :where(ul, ol)");
  expect(chat).not.toContain(".board-assistant .board-assistant__prose :where(table)");
  const chatHeading = chat.match(/\.board-assistant \.board-assistant__prose :where\(h2\)\s*\{([^}]*)\}/)?.[1] ?? "";
  expect(chatHeading).not.toContain("font-size");
  expect(chatHeading).not.toContain("line-height");
  expect(chat).toContain("margin: 0 0 8px;");
  expect(chat).toContain("margin-top: 13px;");
});

test("Chat rich text uses the mock's unadorned code and note rhythm", () => {
  expect(markdown).toContain("streaming ? { pre: StreamingPreBlock } : typeset ? { pre: TypesetPreBlock } : { pre: PreBlock }");
  expect(typeset).toContain(".md.typeset.typeset-notes { font-size: var(--typeset-size); }");
  expect(typeset).toContain("padding: .125em .3em;");
  expect(typeset).toContain("tab-size: 2;");
  expect(typeset).toContain("border-inline-start: 2px solid var(--typeset-rule);");
  expect(typeset).toContain("border-collapse: separate;");
  expect(typeset).toContain("content: none;");

  const markup = renderToStaticMarkup(
    <Markdown typeset content={"`inline`\n\n```ts\nconst answer = 42;\n```"} />,
  );
  expect(markup).toContain('class="md typeset typeset-notes"');
  expect(markup).toContain("<pre><code");
  expect(markup).not.toContain("code-block");
  expect(markup).not.toContain("Copy code");
});

test("Board typeset preserves Mermaid rendering without utility code chrome", () => {
  expect(markdown).toContain('if (lang === "mermaid" && rawText.trim()) return <Mermaid code={rawText} />;');
  const markup = renderToStaticMarkup(
    <Markdown typeset content={"```mermaid\ngraph TD; A-->B\n```"} />,
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
  expect(markdownViewer).toContain("<Markdown content={view.content} typeset />");
  expect(automations).toContain("<Markdown content={content} typeset />");
  expect(markdownViewer).not.toContain("text-md leading-[1.6] text-ink");
  expect(automations).not.toContain("text-md leading-[1.6] text-ink");
});

test("Chat user bubbles keep the mock's reading size and padding", () => {
  const bubble = chat.match(/\.board-user__bubble\s*\{([^}]*)\}/)?.[1] ?? "";
  expect(bubble).toContain("padding: 10px 14px");
  expect(bubble).toContain("font-size: var(--text-md)");
  expect(bubble).toContain("line-height: 1.48");
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

test("Chat keeps the mock's icon-only 620px composer controls", () => {
  const compact = modelPickers.slice(modelPickers.indexOf("@media (max-width: 38.75rem)"));

  expect(chatHeader).toContain("board-chat__header-content chat-head-inner");
  expect(budget).toContain('"budget-trigger inline-flex');
  expect(selectors).toContain('className="model-config-trigger group"');
  expect(selectors).toContain('className="composer-model-effort-trigger"');
  expect(selectors).toContain('className="effort-current"');
  expect(chat).toContain(
    ".chat-head-inner { padding-left: calc(var(--chrome-chat-title-inset) - .125rem); }",
  );
  expect(compact).toContain(".board-composer .model-current,");
  expect(compact).toContain(".board-composer .effort-current {");
  expect(chat).toContain(".board-composer .budget-trigger {\n    width: var(--icon-button-size);");
  expect(compact).toContain(".board-composer .model-config-trigger {\n    width: 34px;");
});

test("Chat composer keeps the mock's 66px input and 42px toolbar geometry", () => {
  expect(composer).toContain("board-composer__input-row flex min-h-[66px]");
  expect(composerToolbar).toContain('className="composer-tool-button"');
  expect(composerToolbar).toContain('className="composer-tool-button composer-mode-button"');
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
