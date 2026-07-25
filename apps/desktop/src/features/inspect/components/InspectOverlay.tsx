import type { CSSProperties } from "react";
import { formatSides, measureElement, type ElementMetrics } from "@/features/inspect/lib/measure";

type ColorVarStyle = CSSProperties & { "--inspect-color": string };

export interface InspectTarget {
  el: Element;
  color: string;
  pinned: boolean;
}

/** Renders one highlight box + padding/gap strips + info panel per target.
 *  Pure presentation — Inspect.tsx owns targeting/pin state, this just draws
 *  whatever it's handed. Everything is `position: fixed` in viewport
 *  coordinates, matching what `getBoundingClientRect()` already returns. */
export function InspectOverlay({ targets }: { targets: InspectTarget[] }) {
  return (
    <div data-inspect-ui className="inspect-overlay">
      {targets.map((target) => (
        <InspectTargetView key={target.pinned ? elementKey(target.el) : "__hover__"} target={target} />
      ))}
    </div>
  );
}

const keys = new WeakMap<Element, string>();
let nextKey = 0;
function elementKey(el: Element): string {
  let key = keys.get(el);
  if (!key) {
    key = `inspect-${nextKey++}`;
    keys.set(el, key);
  }
  return key;
}

function InspectTargetView({ target }: { target: InspectTarget }) {
  const metrics = measureElement(target.el);
  const { rect } = metrics;
  const style: ColorVarStyle = { "--inspect-color": target.color };

  return (
    <div className="inspect-target" style={style} data-inspect-ui>
      <Guides rect={rect} />
      <div
        className="inspect-box"
        style={{ left: rect.left, top: rect.top, width: rect.width, height: rect.height, borderRadius: metrics.borderRadius }}
      />
      <PaddingStrips metrics={metrics} />
      <GapStrips metrics={metrics} />
      <InfoPanel metrics={metrics} pinned={target.pinned} />
    </div>
  );
}

/** Full-viewport alignment lines on each edge — the point of the tool is
 *  checking whether things line up with things elsewhere on screen, which
 *  a box drawn around one element alone can't answer. */
function Guides({ rect }: { rect: DOMRect }) {
  return (
    <>
      <span className="inspect-guide" data-axis="x" style={{ left: rect.left }} />
      <span className="inspect-guide" data-axis="x" style={{ left: rect.right }} />
      <span className="inspect-guide" data-axis="y" style={{ top: rect.top }} />
      <span className="inspect-guide" data-axis="y" style={{ top: rect.bottom }} />
    </>
  );
}

function PaddingStrips({ metrics }: { metrics: ElementMetrics }) {
  const { rect, padding } = metrics;
  const strips: { key: string; left: number; top: number; width: number; height: number; label: string }[] = [];
  if (padding.top > 0) {
    strips.push({ key: "t", left: rect.left, top: rect.top, width: rect.width, height: padding.top, label: `${padding.top}` });
  }
  if (padding.bottom > 0) {
    strips.push({
      key: "b",
      left: rect.left,
      top: rect.bottom - padding.bottom,
      width: rect.width,
      height: padding.bottom,
      label: `${padding.bottom}`,
    });
  }
  if (padding.left > 0) {
    strips.push({ key: "l", left: rect.left, top: rect.top, width: padding.left, height: rect.height, label: `${padding.left}` });
  }
  if (padding.right > 0) {
    strips.push({
      key: "r",
      left: rect.right - padding.right,
      top: rect.top,
      width: padding.right,
      height: rect.height,
      label: `${padding.right}`,
    });
  }
  return (
    <>
      {strips.map((s) => (
        <div key={s.key} className="inspect-padding-strip" style={{ left: s.left, top: s.top, width: s.width, height: s.height }}>
          {s.width > 20 && s.height > 14 && <span className="inspect-strip-label">{s.label}</span>}
        </div>
      ))}
    </>
  );
}

function GapStrips({ metrics }: { metrics: ElementMetrics }) {
  return (
    <>
      {metrics.gap.map((g, i) => (
        <div
          key={i}
          className="inspect-gap-strip"
          style={{ left: g.rect.left, top: g.rect.top, width: g.rect.width, height: g.rect.height }}
        >
          {(g.axis === "row" ? g.rect.width > 18 : g.rect.height > 14) && <span className="inspect-strip-label">{g.size}</span>}
        </div>
      ))}
    </>
  );
}

const PANEL_WIDTH = 260;
const PANEL_GAP = 10;
const PANEL_EDGE = 8;
/** 11px text at 1.55 line-height (see .inspect-panel in styles.css). */
const PANEL_ROW = 17;
/** 8px block padding ×2, plus the head row and its 2px margin. */
const PANEL_CHROME = 16 + PANEL_ROW + 2;

/** Places the readout clear of the element it describes — never on top of it,
 *  which would hide the thing you're measuring. Tries above, then below, then
 *  beside. Height is derived from the row count rather than measured, so
 *  placement is correct on first paint (no reflow flash while hover-tracking). */
function panelPlacement(rect: DOMRect, rows: number) {
  const height = PANEL_CHROME + rows * PANEL_ROW;
  const maxTop = window.innerHeight - height - PANEL_EDGE;
  const clampLeft = (x: number) => Math.max(PANEL_EDGE, Math.min(x, window.innerWidth - PANEL_WIDTH - PANEL_EDGE));

  const above = rect.top - height - PANEL_GAP;
  if (above >= PANEL_EDGE) return { left: clampLeft(rect.left), top: above };

  const below = rect.bottom + PANEL_GAP;
  if (below <= maxTop) return { left: clampLeft(rect.left), top: below };

  // Vertically boxed in — sit beside it instead, on whichever side has room.
  const top = Math.max(PANEL_EDGE, Math.min(rect.top, maxTop));
  const right = rect.right + PANEL_GAP;
  if (right + PANEL_WIDTH + PANEL_EDGE <= window.innerWidth) return { left: right, top };
  return { left: clampLeft(rect.left - PANEL_WIDTH - PANEL_GAP), top };
}

function InfoPanel({ metrics, pinned }: { metrics: ElementMetrics; pinned: boolean }) {
  const { rect } = metrics;
  const rows = 4 + (metrics.gap.length > 0 ? 1 : 0) + (metrics.font ? 2 : 0);
  const { left, top } = panelPlacement(rect, rows);

  return (
    <div className="inspect-panel" style={{ left, top }} data-inspect-ui>
      <div className="inspect-panel__head">
        <span className="inspect-panel__tag">
          {metrics.tag}
          {metrics.id ? `#${metrics.id}` : ""}
        </span>
        {pinned && <span className="inspect-panel__pinned">pinned</span>}
      </div>
      <div className="inspect-panel__row">
        {metrics.display}
        {metrics.flexDirection ? ` · ${metrics.flexDirection}` : ""} · {Math.round(rect.width)}×{Math.round(rect.height)}
      </div>
      <div className="inspect-panel__row">radius {metrics.borderRadius}</div>
      <div className="inspect-panel__row">padding {formatSides(metrics.padding)}</div>
      <div className="inspect-panel__row">margin {formatSides(metrics.margin)}</div>
      {metrics.gap.length > 0 && <div className="inspect-panel__row">gap {metrics.gap.map((g) => g.size).join(", ")}</div>}
      {metrics.font && (
        <>
          <div className="inspect-panel__row">
            {metrics.font.size}px/{metrics.font.lineHeight} · {metrics.font.family}
          </div>
          <div className="inspect-panel__row">
            {metrics.font.weight} · tracking {metrics.font.tracking} · {metrics.font.color}
          </div>
        </>
      )}
    </div>
  );
}
