import { useEffect, useId, useRef, useState } from "react";
import { motion } from "motion/react";
import { Maximize2, Minus, Plus, RotateCcw, X } from "@/components/icons";
import clsx from "clsx";
import { getMermaid, invalidateMermaidTheme } from "@/lib/mermaidTheme";
import {
  EASE_DECELERATE,
  MOTION,
  RISE_IN,
  RISE_SETTLED,
} from "@/lib/tokens/motion";
import { useTimeoutFlag } from "@/lib/hooks";
import { copyText } from "@/lib/clipboard";
import { ICON } from "@/lib/icons";
import { CopyGlyph } from "@/components/ui/CopyGlyph";
import { IconButton } from "@/components/ui/IconButton";
import { PageModal } from "@/components/ui/PageModal";

const RENDER_DEBOUNCE_MS = 400;
const MIN_ZOOM = 0.1;
const MAX_ZOOM = 5;
const ZOOM_STEP = 1.2;

interface ViewState {
  zoom: number;
  x: number;
  y: number;
}

export function Mermaid({ code }: { code: string }) {
  const reactId = useId();
  const idRef = useRef(`m${reactId.replace(/:/g, "-")}`);
  const [svg, setSvg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [stableCode, setStableCode] = useState(code);

  useEffect(() => {
    if (code === stableCode) return;
    const handle = setTimeout(() => setStableCode(code), RENDER_DEBOUNCE_MS);
    return () => clearTimeout(handle);
  }, [code, stableCode]);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const mermaid = await getMermaid();
        const { svg: out } = await mermaid.render(idRef.current, stableCode);
        if (!cancelled) {
          setSvg(out);
          setError(null);
        }
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [stableCode]);

  useEffect(() => {
    const mq = matchMedia("(prefers-color-scheme: dark)");
    const handler = () => {
      invalidateMermaidTheme();
      setSvg((prev) => prev);
    };
    mq.addEventListener("change", handler);
    return () => mq.removeEventListener("change", handler);
  }, []);

  if (!svg && error) {
    return <MermaidErrorBlock source={code} message={error} />;
  }
  if (!svg) {
    return (
      <div className="my-[0.6em] px-4 py-3.5 border border-dashed border-line-soft rounded-xl text-faint text-xs italic text-center">
        Rendering…
      </div>
    );
  }
  // One-shot mount entrance so the panel doesn't hard-cut in when the SVG
  // resolves out of the "Rendering…" placeholder. The viewer sheet renders
  // through the shared modal layer, outside this wrapper.
  return (
    <motion.div
      initial={{ ...RISE_IN, y: 8 }}
      animate={RISE_SETTLED}
      transition={{ duration: MOTION.trace, ease: EASE_DECELERATE }}
    >
      <MermaidPanel svg={svg} source={code} />
    </motion.div>
  );
}

/** Top-level panel: the inline diagram stays in flow; the expanded viewer
 *  uses the shared bottom-sheet contract. Each variant remounts PanelInner
 *  so fit-to-view reads the correct surface size. */
function MermaidPanel({ svg, source }: { svg: string; source: string }) {
  const [fullscreen, setFullscreen] = useState(false);
  const toggle = () => setFullscreen((v) => !v);

  return (
    <>
      <PanelInner svg={svg} source={source} fullscreen={false} onToggleFullscreen={toggle} />
      <PageModal
        open={fullscreen}
        onClose={() => setFullscreen(false)}
        placement="bottom-sheet"
        bottomSheetFill
        grid="grid-rows-[minmax(0,1fr)]"
        ariaLabel="Diagram viewer"
      >
        <PanelInner svg={svg} source={source} fullscreen onToggleFullscreen={toggle} />
      </PageModal>
    </>
  );
}

function PanelInner({
  svg,
  source,
  fullscreen,
  onToggleFullscreen,
}: {
  svg: string;
  source: string;
  fullscreen: boolean;
  onToggleFullscreen: () => void;
}) {
  const surfaceRef = useRef<HTMLDivElement>(null);
  const innerRef = useRef<HTMLDivElement>(null);
  const svgRef = useRef<SVGElement | null>(null);
  const naturalRef = useRef<{ w: number; h: number } | null>(null);
  const [view, setView] = useState<ViewState>({ zoom: 1, x: 0, y: 0 });
  const [dragging, setDragging] = useState(false);
  const [copied, flashCopied] = useTimeoutFlag(1200);
  const dragRef = useRef<{ startX: number; startY: number; viewX: number; viewY: number } | null>(null);

  const onCopy = async () => {
    if (await copyText(source)) flashCopied();
  };

  // Inject the SVG once, capture natural size, fit-to-view.
  useEffect(() => {
    const inner = innerRef.current;
    const surface = surfaceRef.current;
    if (!inner || !surface) return;
    inner.innerHTML = svg;
    const el = inner.querySelector("svg") as SVGElement | null;
    svgRef.current = el;
    if (!el) return;
    el.style.transformOrigin = "0 0";
    el.style.willChange = "transform";
    el.style.transition = "none"; // snappy

    const sBox = el.getBoundingClientRect();
    naturalRef.current = { w: sBox.width || 1, h: sBox.height || 1 };

    const fit = computeFit(surface, naturalRef.current);
    setView(fit);
  }, [svg]);

  // Apply the transform on every view change.
  useEffect(() => {
    const el = svgRef.current;
    if (!el) return;
    el.style.transform = `translate3d(${view.x}px, ${view.y}px, 0) scale(${view.zoom})`;
  }, [view]);

  // Wheel zoom (cursor-anchored). In inline mode require Cmd/Ctrl so the
  // chat keeps scrolling normally; in fullscreen plain wheel zooms.
  useEffect(() => {
    const surface = surfaceRef.current;
    if (!surface) return;
    const handler = (e: WheelEvent) => {
      const modifier = e.ctrlKey || e.metaKey;
      if (!fullscreen && !modifier) return;
      e.preventDefault();
      const rect = surface.getBoundingClientRect();
      const cx = e.clientX - rect.left;
      const cy = e.clientY - rect.top;
      const delta = e.deltaY > 0 ? 0.9 : 1.1;
      setView((prev) => zoomTowards(prev, prev.zoom * delta, cx, cy));
    };
    surface.addEventListener("wheel", handler, { passive: false });
    return () => surface.removeEventListener("wheel", handler);
  }, [fullscreen]);

  const onMouseDown = (e: React.MouseEvent) => {
    if (e.button !== 0) return;
    setDragging(true);
    dragRef.current = { startX: e.clientX, startY: e.clientY, viewX: view.x, viewY: view.y };
  };
  const onMouseMove = (e: React.MouseEvent) => {
    const drag = dragRef.current;
    if (!drag) return;
    setView((prev) => ({
      ...prev,
      x: drag.viewX + (e.clientX - drag.startX),
      y: drag.viewY + (e.clientY - drag.startY),
    }));
  };
  const endDrag = () => {
    setDragging(false);
    dragRef.current = null;
  };

  // Toolbar zoom anchors on the surface center so the diagram stays in
  // view rather than drifting toward the corner.
  const zoomBy = (factor: number) => {
    const surface = surfaceRef.current;
    if (!surface) return;
    const rect = surface.getBoundingClientRect();
    setView((prev) => zoomTowards(prev, prev.zoom * factor, rect.width / 2, rect.height / 2));
  };

  const fitToView = () => {
    const surface = surfaceRef.current;
    const natural = naturalRef.current;
    if (!surface || !natural) return;
    setView(computeFit(surface, natural));
  };

  return (
    <div
      className={clsx(
        "grid grid-rows-[auto_1fr] overflow-hidden",
        fullscreen
          ? "m-0 h-full bg-transparent"
          : "my-[0.6em] h-[480px] border border-line-soft rounded-xl bg-code-bg",
      )}
    >
      {fullscreen ? (
        <header className="flex min-h-12 shrink-0 items-center gap-2 border-b border-line px-3 pl-4">
          <strong className="text-base font-semibold text-ink">Workflow diagram</strong>
          <div className="ml-auto shrink-0">
            <IconButton
              onClick={onToggleFullscreen}
              aria-label="Close Mermaid viewer"
              title="Close Mermaid viewer"
            >
              <X size={ICON.SM} />
            </IconButton>
          </div>
        </header>
      ) : (
        <header className="flex items-center justify-between gap-2 py-1.5 pl-3 pr-2.5 border-b border-line-soft bg-surface">
          <div className="text-xs font-medium uppercase tracking-[0.08em] text-faint">Diagram</div>
          <div className="flex items-center gap-0.5">
            <ToolbarButton
              label="Zoom out"
              onClick={() => zoomBy(1 / ZOOM_STEP)}
              disabled={view.zoom <= MIN_ZOOM + 1e-3}
            >
              <Minus size={ICON.SM} strokeWidth={2} />
            </ToolbarButton>
            <span className="w-11 text-center text-xs tabular-nums text-muted select-none">{Math.round(view.zoom * 100)}%</span>
            <ToolbarButton
              label="Zoom in"
              onClick={() => zoomBy(ZOOM_STEP)}
              disabled={view.zoom >= MAX_ZOOM - 1e-3}
            >
              <Plus size={ICON.SM} strokeWidth={2} />
            </ToolbarButton>
            <ToolbarButton label="Fit to view" onClick={fitToView}>
              <RotateCcw size={ICON.XS} strokeWidth={2} />
            </ToolbarButton>
            <span className="w-px h-4 bg-line mx-1" />
            <ToolbarButton
              label={copied ? "Copied" : "Copy source"}
              onClick={() => void onCopy()}
            >
              <CopyGlyph copied={copied} size={ICON.SM} checkClassName="text-ok" />
            </ToolbarButton>
            <ToolbarButton label="Fullscreen" onClick={onToggleFullscreen}>
              <Maximize2 size={ICON.SM} strokeWidth={2} />
            </ToolbarButton>
          </div>
        </header>
      )}
      <div
        ref={surfaceRef}
        className={clsx(
          "relative overflow-hidden select-none touch-none",
          fullscreen ? "bg-transparent" : "bg-code-bg",
          dragging ? "cursor-grabbing" : "cursor-grab",
        )}
        role={fullscreen ? "img" : undefined}
        aria-label={fullscreen ? "Workflow diagram" : undefined}
        onMouseDown={onMouseDown}
        onMouseMove={onMouseMove}
        onMouseUp={endDrag}
        onMouseLeave={endDrag}
      >
        <div ref={innerRef} className="absolute inset-0 [&>svg]:block" />
      </div>
    </div>
  );
}

function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value));
}

function zoomTowards(prev: ViewState, requestedZoom: number, anchorX: number, anchorY: number): ViewState {
  const next = clamp(requestedZoom, MIN_ZOOM, MAX_ZOOM);
  if (next === prev.zoom) return prev;
  const ratio = next / prev.zoom;
  return {
    zoom: next,
    x: anchorX + (prev.x - anchorX) * ratio,
    y: anchorY + (prev.y - anchorY) * ratio,
  };
}

function computeFit(surface: HTMLElement, natural: { w: number; h: number }): ViewState {
  const rect = surface.getBoundingClientRect();
  // Margin so the diagram isn't flush against the surface edges.
  const pad = 16;
  const fit = Math.min(
    (rect.width - pad * 2) / natural.w,
    (rect.height - pad * 2) / natural.h,
    1,
  );
  return {
    zoom: fit,
    x: (rect.width - natural.w * fit) / 2,
    y: (rect.height - natural.h * fit) / 2,
  };
}

function MermaidErrorBlock({ source, message }: { source: string; message: string }) {
  const [copied, flashCopied] = useTimeoutFlag(1200);
  const onCopy = async () => {
    if (await copyText(source)) flashCopied();
  };
  return (
    <div className="my-[0.6em] px-3.5 py-3 border border-bad/20 rounded-xl bg-bad-soft">
      <div className="flex items-center justify-between gap-2 mb-1.5">
        <strong className="text-xs font-semibold text-bad">Couldn't render diagram</strong>
        <IconButton
          size="xs"
          danger
          onClick={() => void onCopy()}
          aria-label={copied ? "Copied" : "Copy source"}
          title={copied ? "Copied" : "Copy source"}
          className="border border-bad/25 !text-bad hover:!bg-bad-soft"
        >
          <CopyGlyph copied={copied} size={ICON.XS} />
        </IconButton>
      </div>
      <pre className="m-0 text-xs text-ink-soft whitespace-pre-wrap break-words">{source}</pre>
      {message && (
        <div className="mt-1.5 text-xs text-bad font-mono opacity-80">{message}</div>
      )}
    </div>
  );
}

function ToolbarButton({
  label,
  onClick,
  disabled,
  children,
}: {
  label: string;
  onClick: () => void;
  disabled?: boolean;
  children: React.ReactNode;
}) {
  return (
    <IconButton
      onClick={onClick}
      disabled={disabled}
      aria-label={label}
      title={label}
      className="relative [&_*]:pointer-events-none"
    >
      {children}
    </IconButton>
  );
}
