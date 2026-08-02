import {
  Children,
  createContext,
  isValidElement,
  useContext,
  useMemo,
  type ReactNode,
} from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeHighlight from "rehype-highlight";
import rehypeKatex from "rehype-katex";
import rehypeSanitize, { defaultSchema } from "rehype-sanitize";
import { CopyGlyph } from "@/components/ui/CopyGlyph";
import { Globe02 } from "@/components/icons";
import { copyText } from "@/lib/clipboard";
import clsx from "clsx";
import bash from "highlight.js/lib/languages/bash";
import javascript from "highlight.js/lib/languages/javascript";
import json from "highlight.js/lib/languages/json";
import python from "highlight.js/lib/languages/python";
import typescript from "highlight.js/lib/languages/typescript";
import { MemoryPeekLink } from "@/components/ui/MemoryLinkPeek";
import { Mermaid } from "@/components/ui/Mermaid";
import { ICON } from "@/lib/icons";
import { useTimeoutFlag } from "@/lib/hooks";
import {
  parseMemoryArtifactHref,
  remarkProvenance,
  remarkWikiLink,
  WikiLinkContext,
} from "@/lib/wikilink";

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

const REMARK_MATH: [typeof remarkMath, { singleDollarTextMath: boolean }] = [
  remarkMath,
  { singleDollarTextMath: false },
];

const ExternalLinkFaviconContext = createContext(false);

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
  codeChrome = true,
  provenance = false,
  externalLinkFavicons = false,
}: {
  content: string;
  className?: string;
  streaming?: boolean;
  /** Wrap fenced code in the utility chrome — language strip, registration
   *  ticks, copy control. Reading surfaces that want bare code opt out.
   *  Prose itself is not optional: every Markdown surface is `.md.typeset`. */
  codeChrome?: boolean;
  /** Render memory-synthesizer source tags — `(from chat)`, `(inferred)` — as
   *  inline chips. Only the memory wiki view opts in; chat prose stays literal. */
  provenance?: boolean;
  /** Opt chat prose into the explicit conversation-outline contract. */
  /** Decorate completed assistant-response links with cached website icons. */
  externalLinkFavicons?: boolean;
}) {
  const components = {
    // The Chat mock uses the Board typeset pre directly: it has no language
    // strip, registration ticks, or copy control. Other Markdown surfaces
    // retain that richer utility chrome.
    ...(streaming ? { pre: StreamingPreBlock } : codeChrome ? { pre: PreBlock } : { pre: TypesetPreBlock }),
    a: Anchor,
    code: InlineCode,
    td: TableCell,
  };
  return (
    <div className={clsx("md typeset typeset-notes", className)}>
      <ExternalLinkFaviconContext.Provider value={externalLinkFavicons && !streaming}>
        <ReactMarkdown
        // Single-dollar math is disabled because ordinary prose commonly
        // contains multiple currency amounts. Display math remains `$$…$$`.
        // rehype-katex converts those math nodes into the spans/MathML
        // KaTeX needs for rendering. The order matters — katex MUST run
        // before sanitize so its output exists when sanitize walks the
        // tree, but the sanitize schema is extended above to keep the
        // tags/classes/attributes katex emits.
        remarkPlugins={provenance
          ? [remarkGfm, REMARK_MATH, remarkWikiLink, remarkProvenance]
          : [remarkGfm, REMARK_MATH, remarkWikiLink]}
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
        components={components}
      >
        {content}
        </ReactMarkdown>
      </ExternalLinkFaviconContext.Provider>
    </div>
  );
}

function Anchor({ href, children, ...rest }: React.AnchorHTMLAttributes<HTMLAnchorElement>) {
  const wiki = useContext(WikiLinkContext);
  const showExternalFavicon = useContext(ExternalLinkFaviconContext);
  const faviconUrl = showExternalFavicon ? externalFaviconUrl(href) : null;
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
  const artifact = href ? parseMemoryArtifactHref(href) : null;
  if (wiki && artifact && (wiki.existsInline ?? wiki.exists)(artifact.path)) {
    const navigate = () => {
      if (wiki.onNavigateInline) wiki.onNavigateInline(artifact.path, artifact.anchor);
      else wiki.onNavigate(artifact.path);
    };
    if (wiki.peek) {
      return (
        <MemoryPeekLink
          path={artifact.path}
          anchor={artifact.anchor}
          href={href}
          className={rest.className}
          onOpen={navigate}
        >
          {children}
        </MemoryPeekLink>
      );
    }
    return (
      <a
        href={href}
        className={clsx("wikilink", rest.className)}
        data-memory-path={artifact.path}
        data-memory-anchor={artifact.anchor ?? undefined}
        onClick={(event) => {
          event.preventDefault();
          navigate();
        }}
      >
        {children}
      </a>
    );
  }
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      {...rest}
      className={clsx(rest.className, faviconUrl && "external-link--favicon")}
    >
      {faviconUrl && (
        <span className="external-link__icon" aria-hidden="true">
          <Globe02 className="external-link__fallback" />
          <img
            className="external-link__favicon"
            src={faviconUrl}
            alt=""
            decoding="async"
            referrerPolicy="no-referrer"
            // The globe is a fallback, not a backdrop: a favicon with any
            // transparency (most of them) leaves it showing through from
            // underneath. Retire it the moment the real icon arrives, and keep
            // it when none does.
            onLoad={(event) => event.currentTarget.parentElement?.setAttribute("data-favicon", "loaded")}
            onError={(event) => event.currentTarget.remove()}
          />
        </span>
      )}
      {children}
    </a>
  );
}

function externalFaviconUrl(href: string | undefined): string | null {
  if (!href) return null;
  try {
    const url = new URL(href);
    return url.protocol === "https:" ? `${url.origin}/favicon.ico` : null;
  } catch {
    return null;
  }
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
    if (wiki.peek) {
      return (
        <MemoryPeekLink
          path={target}
          href="#wikilink"
          onOpen={() => (wiki.onNavigateInline ?? wiki.onNavigate)(target)}
        >
          {children}
        </MemoryPeekLink>
      );
    }
    return (
      <a
        href="#wikilink"
        className="wikilink"
        data-memory-path={target}
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

// A cell holding one short token (a date, a version) must not break at its
// hyphens — CSS alone can't forbid that; long or multi-word content wraps.
function TableCell({ children, ...rest }: React.TdHTMLAttributes<HTMLTableCellElement>) {
  const text = extractText(children).trim();
  const nowrap = text.length > 0 && text.length <= 24 && !/\s/.test(text);
  return <td {...rest} style={nowrap ? { whiteSpace: "nowrap" } : undefined}>{children}</td>;
}

function StreamingPreBlock({ children }: { children?: ReactNode }) {
  return <pre className="streaming-code">{children}</pre>;
}

function firstCodeChild(children: ReactNode) {
  return Children.toArray(children).find(
    (child): child is React.ReactElement<{ className?: string; children?: ReactNode }> =>
      isValidElement<{ className?: string; children?: ReactNode }>(child),
  );
}

/** Board typeset keeps code blocks visually bare, but Mermaid fences still
 * need to reach the shared diagram renderer instead of becoming raw code. */
function TypesetPreBlock({ children }: { children?: ReactNode }) {
  const codeNode = firstCodeChild(children);
  const className = codeNode?.props.className ?? "";
  const lang = className.match(/(?:^|\s)language-(\S+)/)?.[1] ?? "";
  const rawText = extractText(codeNode?.props.children);

  if (lang === "mermaid" && rawText.trim()) return <Mermaid code={rawText} />;
  return <pre>{children}</pre>;
}

/** Custom <pre> wrapper: pulls language + raw text from the inner <code>
 *  child and renders our header (language label + copy button) above the
 *  highlighted code. */
function PreBlock({ children }: { children?: ReactNode }) {
  // ReactMarkdown gives us a single code-renderer element child — extract its
  // className (carries the language) and raw text before highlighting. Its
  // React type is InlineCode, not the literal string "code".
  const codeNode = firstCodeChild(children);
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
