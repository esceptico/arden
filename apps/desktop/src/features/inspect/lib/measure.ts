/** Pure DOM measurement for the Inspect overlay — no React, no store. */

export interface BoxSides {
  top: number;
  right: number;
  bottom: number;
  left: number;
}

export interface GapStrip {
  axis: "row" | "column";
  size: number;
  rect: DOMRect;
}

export interface FontMetrics {
  size: number;
  lineHeight: string;
  family: string;
  weight: string;
  tracking: string;
  color: string;
}

export interface ElementMetrics {
  tag: string;
  id: string | null;
  rect: DOMRect;
  display: string;
  flexDirection: string | null;
  padding: BoxSides;
  margin: BoxSides;
  borderRadius: string;
  gap: GapStrip[];
  font: FontMetrics | null;
}

function paddingSides(cs: CSSStyleDeclaration): BoxSides {
  return {
    top: Math.round(parseFloat(cs.paddingTop) || 0),
    right: Math.round(parseFloat(cs.paddingRight) || 0),
    bottom: Math.round(parseFloat(cs.paddingBottom) || 0),
    left: Math.round(parseFloat(cs.paddingLeft) || 0),
  };
}

function marginSides(cs: CSSStyleDeclaration): BoxSides {
  return {
    top: Math.round(parseFloat(cs.marginTop) || 0),
    right: Math.round(parseFloat(cs.marginRight) || 0),
    bottom: Math.round(parseFloat(cs.marginBottom) || 0),
    left: Math.round(parseFloat(cs.marginLeft) || 0),
  };
}

/** Collapses a 4-side box to CSS shorthand form for display (`16px`,
 *  `8px 16px`, or the full 4-value form) — mirrors how you'd write it by hand. */
export function formatSides(box: BoxSides): string {
  const { top, right, bottom, left } = box;
  if (top === right && right === bottom && bottom === left) return `${top}px`;
  if (top === bottom && left === right) return `${top}px ${right}px`;
  return `${top}px ${right}px ${bottom}px ${left}px`;
}

function rgbToHex(rgb: string): string {
  const parts = rgb.match(/[\d.]+/g);
  if (!parts || parts.length < 3) return rgb;
  const [r, g, b] = parts.map((n) => Math.round(Number(n)));
  return `#${[r, g, b].map((n) => n.toString(16).padStart(2, "0")).join("")}`;
}

/** This app ships variable fonts (Geist Variable), which flatten
 *  `font-weight` to a static value — the real weight lives in
 *  `font-variation-settings`'s `wght`/`opsz` axes. Prefer those when set. */
function fontWeightLabel(cs: CSSStyleDeclaration): string {
  const variation = cs.fontVariationSettings;
  if (variation && variation !== "normal") {
    const axes = variation
      .split(",")
      .map((part) => part.trim().match(/["']?(\w+)["']?\s+([\d.]+)/))
      .filter((m): m is RegExpMatchArray => Boolean(m))
      .map((m) => `${m[1]} ${Number(m[2])}`);
    if (axes.length > 0) return axes.join(" · ");
  }
  return cs.fontWeight;
}

function hasDirectText(el: Element): boolean {
  for (const node of el.childNodes) {
    if (node.nodeType === Node.TEXT_NODE && (node.textContent ?? "").trim().length > 0) return true;
  }
  return false;
}

const GAP_DISPLAYS = new Set(["flex", "inline-flex", "grid", "inline-grid"]);

/** No CSS API exposes the resolved gap between a specific pair of children —
 *  diff consecutive children's rects instead. Row-major only (grid's
 *  per-track gap isn't attempted; the common single-row/column case covers
 *  what this tool is for). */
function computeGapStrips(el: Element, display: string): GapStrip[] {
  if (!GAP_DISPLAYS.has(display)) return [];
  const children = Array.from(el.children).filter((c) => !c.hasAttribute("data-inspect-ui"));
  if (children.length < 2) return [];
  const isRow = display.includes("flex") ? getComputedStyle(el).flexDirection.startsWith("row") : true;
  const strips: GapStrip[] = [];
  for (let i = 0; i < children.length - 1; i++) {
    const a = children[i].getBoundingClientRect();
    const b = children[i + 1].getBoundingClientRect();
    if (isRow) {
      const size = b.left - a.right;
      if (size > 0.5) {
        const top = Math.min(a.top, b.top);
        strips.push({ axis: "row", size: Math.round(size), rect: new DOMRect(a.right, top, size, Math.max(a.bottom, b.bottom) - top) });
      }
    } else {
      const size = b.top - a.bottom;
      if (size > 0.5) {
        const left = Math.min(a.left, b.left);
        strips.push({ axis: "column", size: Math.round(size), rect: new DOMRect(left, a.bottom, Math.max(a.right, b.right) - left, size) });
      }
    }
  }
  return strips;
}

export function measureElement(el: Element): ElementMetrics {
  const cs = getComputedStyle(el);
  const display = cs.display;
  return {
    tag: el.tagName.toLowerCase(),
    id: el.id || null,
    rect: el.getBoundingClientRect(),
    display,
    flexDirection: display.includes("flex") ? cs.flexDirection : display.includes("grid") ? "grid" : null,
    padding: paddingSides(cs),
    margin: marginSides(cs),
    borderRadius: cs.borderRadius,
    gap: computeGapStrips(el, display),
    font: hasDirectText(el)
      ? {
          size: Math.round(parseFloat(cs.fontSize)),
          lineHeight: cs.lineHeight === "normal" ? "normal" : `${Math.round(parseFloat(cs.lineHeight))}px`,
          family: cs.fontFamily.split(",")[0]?.trim().replace(/['"]/g, "") ?? cs.fontFamily,
          weight: fontWeightLabel(cs),
          tracking: cs.letterSpacing === "normal" ? "0" : cs.letterSpacing,
          color: rgbToHex(cs.color),
        }
      : null,
  };
}
