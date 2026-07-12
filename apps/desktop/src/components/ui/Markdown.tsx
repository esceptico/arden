import { Children, isValidElement, useContext, useMemo, type ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeHighlight from "rehype-highlight";
import rehypeKatex from "rehype-katex";
import rehypeSanitize, { defaultSchema } from "rehype-sanitize";
import { CopyGlyph } from "@/components/ui/CopyGlyph";
import { copyText } from "@/lib/clipboard";
import clsx from "clsx";
import bash from "highlight.js/lib/languages/bash";
import javascript from "highlight.js/lib/languages/javascript";
import json from "highlight.js/lib/languages/json";
import python from "highlight.js/lib/languages/python";
import typescript from "highlight.js/lib/languages/typescript";
import { Mermaid } from "@/components/ui/Mermaid";
import { ICON } from "@/lib/icons";
import { useTimeoutFlag } from "@/lib/hooks";
import { remarkProvenance, remarkWikiLink, WikiLinkContext } from "@/lib/wikilink";

const HL_LANGUAGES = {
  json,
  python,
  py: python,
  javascript,
  js: javascript,
  jsx: javascript,
  typescript,
  ts: typescript,
  tsx: typescript,
  bash,
  sh: bash,
  shell: bash,
  zsh: bash,
};

// rehype-highlight and rehype-katex both inject elements with classes;
// rehype-sanitize runs last to strip anything we don't whitelist. The
// schema below preserves:
//   - <span class="hljs-…"> from rehype-highlight
//   - <span class="katex…">, <math>, <mrow>, <mi>, etc. from rehype-katex
//   - inline `style` on KaTeX spans (used for character spacing / sizing)
//   - the standard math attributes MathML output needs
const MATH_TAGS = [
  "math", "annotation", "semantics",
  "mrow", "mi", "mo", "mn", "ms", "mtext", "mspace",
  "msup", "msub", "msubsup", "mfrac", "mroot", "msqrt",
  "mtable", "mtr", "mtd", "munder", "mover", "munderover",
  "menclose", "mphantom", "mpadded", "mfenced",
] as const;

const sanitizeSchema = {
  ...defaultSchema,
  tagNames: [...(defaultSchema.tagNames ?? []), ...MATH_TAGS],
  attributes: {
    ...defaultSchema.attributes,
    code: [...(defaultSchema.attributes?.code ?? []), ["className"]],
    span: [...(defaultSchema.attributes?.span ?? []), ["className"], "style"],
    div: [...(defaultSchema.attributes?.div ?? []), ["className"], "style"],
    // Wikilinks carry a className styling hook + the resolution target;
    // provenance chips reuse the same transport with data-prov.
    a: [...(defaultSchema.attributes?.a ?? []), ["className"], ["data-wikilink"], ["data-prov"]],
    // KaTeX uses MathML annotations and explicit display modes.
    math: [["xmlns"], "display"],
    annotation: [["encoding"]],
    // Most MathML presentation elements carry a `mathvariant` and/or
    // `displaystyle` — keep the common set so semantic rendering works.
    mi: [["mathvariant"]],
    mo: [["fence"], "lspace", "rspace", "stretchy"],
    mn: [],
    mfrac: [["linethickness"]],
    mtable: [["columnalign"], "rowspacing", "columnspacing"],
    mtd: [["columnalign"]],
  },
};

// NOTE: Vercel's Streamdown (a drop-in react-markdown replacement) was
// evaluated as a simplification — it would cut this file ~165 lines and ~5
// deps and improve incomplete-block stabilization while streaming. Rejected
// for now: it owns the look (Shiki highlighting + its own code-block/mermaid
// chrome), so matching our minimal aesthetic (corner ticks, streaming sheen,
// custom copy button) just relocates the complexity into CSS overrides while
// ceding control of the renderer. This component is already streaming-aware
// (skips rehype-highlight mid-stream; KaTeX tolerates partial `$$`). Revisit
// only if streaming-markdown perf becomes a real, profiled problem.
export function Markdown({
  content,
  className,
  streaming = false,
  provenance = false,
}: {
  content: string;
  className?: string;
  streaming?: boolean;
  /** Render memory-synthesizer source tags — `(from chat)`, `(inferred)` — as
   *  inline chips. Only the memory wiki view opts in; chat prose stays literal. */
  provenance?: boolean;
}) {
  return (
    <div className={clsx("md", className)}>
      <ReactMarkdown
        // remark-math parses $...$ (inline) and $$...$$ (display) into
        // math nodes; rehype-katex converts those into the spans/MathML
        // KaTeX needs for rendering. The order matters — katex MUST run
        // before sanitize so its output exists when sanitize walks the
        // tree, but the sanitize schema is extended above to keep the
        // tags/classes/attributes katex emits.
        remarkPlugins={provenance ? [remarkGfm, remarkMath, remarkWikiLink, remarkProvenance] : [remarkGfm, remarkMath, remarkWikiLink]}
        rehypePlugins={
          streaming
            ? [
                // During streaming we skip rehype-highlight (it's CPU-heavy
                // on partial content), but math still renders fine — katex
                // tolerates incomplete `$$` blocks by leaving them as text
                // until both delimiters are present.
                [rehypeKatex, { strict: false, throwOnError: false }],
                [rehypeSanitize, sanitizeSchema],
              ]
            : [
                [rehypeHighlight, { languages: HL_LANGUAGES, detect: false, ignoreMissing: true }],
                [rehypeKatex, { strict: false, throwOnError: false }],
                [rehypeSanitize, sanitizeSchema],
              ]
        }
        components={{ pre: streaming ? StreamingPreBlock : PreBlock, a: Anchor, code: InlineCode }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}

function Anchor({ href, children, ...rest }: React.AnchorHTMLAttributes<HTMLAnchorElement>) {
  const wiki = useContext(WikiLinkContext);
  const prov = (rest as Record<string, unknown>)["data-prov"] as string | undefined;
  if (prov != null) {
    // Not a link at all — a provenance chip riding the anchor transport.
    return <span className="prov">{children}</span>;
  }
  const target = (rest as Record<string, unknown>)["data-wikilink"] as string | undefined;
  if (target != null) {
    const { className, ...anchorRest } = rest;
    // No handlers wired (chat, traces) → inert styled text, no nav. With
    // handlers, a dangling target renders Obsidian-style "unresolved".
    const exists = wiki?.exists(target) ?? false;
    const interactive = wiki != null && exists;
    return (
      <a
        {...anchorRest}
        href={href}
        className={clsx("wikilink", !interactive && "wikilink--unresolved", className)}
        onClick={(e) => {
          e.preventDefault();
          if (interactive) wiki.onNavigate(target);
        }}
      >
        {children}
      </a>
    );
  }
  return (
    <a href={href} target="_blank" rel="noopener noreferrer" {...rest}>
      {children}
    </a>
  );
}

// Inline code that names an artifact path (`directives.md`, `entities/`,
// `changelog/2026.md`) renders as a clickable internal link in the memory view.
// Fenced blocks (language/hljs class, non-string children) and code in chat/traces
// (no WikiLinkContext) fall through to a plain <code>.
function InlineCode({ className, children, ...rest }: React.HTMLAttributes<HTMLElement>) {
  const wiki = useContext(WikiLinkContext);
  const text = typeof children === "string" ? children : null;
  const isInline = !className || (!className.includes("language-") && !className.includes("hljs"));
  if (wiki && isInline && text && (wiki.existsInline ?? wiki.exists)(text.trim())) {
    const target = text.trim();
    return (
      <a
        href="#wikilink"
        className="wikilink"
        onClick={(e) => {
          e.preventDefault();
          (wiki.onNavigateInline ?? wiki.onNavigate)(target);
        }}
      >
        {children}
      </a>
    );
  }
  return (
    <code className={className} {...rest}>
      {children}
    </code>
  );
}

function StreamingPreBlock({ children }: { children?: ReactNode }) {
  return <pre className="streaming-code">{children}</pre>;
}

/** Custom <pre> wrapper: pulls language + raw text from the inner <code>
 *  child and renders our header (language label + copy button) above the
 *  highlighted code. */
function PreBlock({ children }: { children?: ReactNode }) {
  // ReactMarkdown gives us a single <code> element child — extract its
  // className (carries the language) and the raw text before highlighting.
  const codeNode = Children.toArray(children).find(
    (child): child is React.ReactElement<{ className?: string; children?: ReactNode }> =>
      isValidElement(child) && (child as { type?: unknown }).type === "code",
  );
  const className = codeNode?.props.className ?? "";
  const lang = className.match(/(?:^|\s)language-(\S+)/)?.[1] ?? "";
  const rawText = useMemo(() => extractText(codeNode?.props.children), [codeNode]);

  if (lang === "mermaid" && rawText.trim()) {
    return <Mermaid code={rawText} />;
  }

  return (
    <div className="code-block">
      <span className="code-block-tick code-block-tick--tl" aria-hidden="true" />
      <span className="code-block-tick code-block-tick--tr" aria-hidden="true" />
      <div className="code-block-header">
        <span className="code-block-lang">{lang}</span>
        <CopyButton text={rawText} />
      </div>
      <pre>{children}</pre>
    </div>
  );
}

function CopyButton({ text }: { text: string }) {
  const [copied, flashCopied] = useTimeoutFlag(1200);

  const onClick = async () => {
    // Only flash "Copied" when the text actually landed — copyText uses
    // execCommand (a real success signal) rather than trusting
    // navigator.clipboard, which resolves without writing in this webview.
    if (await copyText(text)) flashCopied();
  };

  return (
    <button
      type="button"
      onClick={() => void onClick()}
      aria-label={copied ? "Copied" : "Copy code"}
      className={clsx("code-block-copy", copied && "copied")}
    >
      <CopyGlyph copied={copied} size={ICON.SM} />
    </button>
  );
}

function extractText(node: ReactNode): string {
  if (node == null || node === false) return "";
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(extractText).join("");
  if (isValidElement(node)) {
    const children = (node as React.ReactElement<{ children?: ReactNode }>).props.children;
    return extractText(children);
  }
  return "";
}
