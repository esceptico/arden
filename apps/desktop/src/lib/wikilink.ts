import { createContext } from "react";
import { visit } from "unist-util-visit";
import type { Root, Text } from "mdast";

// Obsidian-style [[Subject]] / [[target|Label]] wikilinks for memory notes. The
// matched target becomes a `link` mdast node carrying className + data-wikilink,
// so it renders as an <a> that the Markdown Anchor intercepts (no browser nav).
const WIKILINK_RE = /\[\[([^[\]|]+)(?:\|([^[\]]+))?\]\]/g;

/** remark plugin: split `text` nodes on [[Subject]] and inject inert links. */
export function remarkWikiLink() {
  return (tree: Root) => {
    visit(tree, "text", (node: Text, index, parent) => {
      if (!parent || index == null || !node.value.includes("[[")) return;
      const parts: Array<Text | WikiLinkNode> = [];
      let last = 0;
      WIKILINK_RE.lastIndex = 0;
      let m: RegExpExecArray | null;
      while ((m = WIKILINK_RE.exec(node.value))) {
        if (m.index > last) parts.push({ type: "text", value: node.value.slice(last, m.index) });
        const target = m[1].trim();
        const label = (m[2] ?? m[1]).trim();
        parts.push({
          type: "link",
          url: "#wikilink",
          data: { hProperties: { className: "wikilink", "data-wikilink": target } },
          children: [{ type: "text", value: label }],
        });
        last = m.index + m[0].length;
      }
      if (parts.length === 0) return;
      if (last < node.value.length) parts.push({ type: "text", value: node.value.slice(last) });
      parent.children.splice(index, 1, ...parts);
      return index + parts.length;
    });
  };
}

// Provenance tags the memory synthesizer writes into page prose — `(from chat)`,
// `(from gmail + calendar)`, `(inferred)`, `(from chat + inferred)`. Source names
// are single machine tokens joined by " + ", so ordinary prose parentheticals
// ("(from the Replika era)") never match. Rendered as a subtle inline chip.
const PROV_RE = /\((from [a-z0-9_.:-]+(?: \+ [a-z0-9_.:-]+)*|inferred(?: \+ [a-z0-9_.:-]+)*)\)/g;

/** remark plugin: turn synthesizer provenance tags into `.prov` chips (spans
 *  via the Markdown Anchor, same transport as wikilinks). */
export function remarkProvenance() {
  return (tree: Root) => {
    visit(tree, "text", (node: Text, index, parent) => {
      if (!parent || index == null || !node.value.includes("(")) return;
      const parts: Array<Text | WikiLinkNode> = [];
      let last = 0;
      PROV_RE.lastIndex = 0;
      let m: RegExpExecArray | null;
      while ((m = PROV_RE.exec(node.value))) {
        // trailing space before the tag belongs to the tag's margin, not the text
        const lead = node.value.slice(last, m.index).replace(/ $/, "");
        if (lead) parts.push({ type: "text", value: lead });
        parts.push({
          type: "link",
          url: "#prov",
          data: { hProperties: { className: "prov", "data-prov": m[1] } },
          children: [{ type: "text", value: m[1] }],
        });
        last = m.index + m[0].length;
      }
      if (parts.length === 0) return;
      if (last < node.value.length) parts.push({ type: "text", value: node.value.slice(last) });
      parent.children.splice(index, 1, ...parts);
      return index + parts.length;
    });
  };
}

type WikiLinkNode = {
  type: "link";
  url: string;
  data: { hProperties: { className: string } & ({ "data-wikilink": string } | { "data-prov": string }) };
  children: Text[];
};

export type WikiLinkHandlers = {
  /** Navigate to the target subject page. */
  onNavigate: (target: string) => void;
  /** Whether a target resolves to an existing note (drives Obsidian-style
   *  muted "unresolved" rendering for dangling links). */
  exists: (target: string) => boolean;
  /** Exact artifact-path navigation for inline code. Kept separate so
   * wikilink aliases remain server-resolved. */
  existsInline?: (target: string) => boolean;
  onNavigateInline?: (target: string, anchor?: string | null) => void;
};

/** Resolution handlers for [[Subject]] clicks. Default undefined so wikilinks
 *  are inert styled text everywhere except the memory view. */
export const WikiLinkContext = createContext<WikiLinkHandlers | undefined>(undefined);

export interface MemoryArtifactHref {
  path: string;
  anchor: string | null;
}

/** Parse only explicit relative memory-artifact links. External URLs, protocol
 * links, and same-page anchors remain normal Markdown links. */
export function parseMemoryArtifactHref(href: string): MemoryArtifactHref | null {
  if (!href || href.startsWith("#") || href.startsWith("//") || /^[a-z][a-z\d+.-]*:/i.test(href)) {
    return null;
  }
  const [rawPath, rawAnchor] = href.split("#", 2);
  let path: string;
  let anchor: string | null;
  try {
    path = decodeURIComponent(rawPath).replace(/^\.\/+/, "").replace(/^\/+/, "");
    anchor = rawAnchor ? decodeURIComponent(rawAnchor) : null;
  } catch {
    return null;
  }
  if (!path.endsWith(".md")) return null;
  return { path, anchor };
}
