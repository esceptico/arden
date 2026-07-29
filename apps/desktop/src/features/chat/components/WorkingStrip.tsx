import { useEffect, useRef } from "react";
import { BlurSwap } from "@/components/ui/BlurSwap";
import { textLaneOpacity } from "@/lib/textLane";
import type { WorkingLabel } from "@/features/chat/lib/workingLabel";

/** Quantisation steps and grid pitch of the field. Ordered dither needs a
 *  gradient to quantise — a flat input renders nothing but the lattice — so
 *  the sweep is always the light source, never a uniform wash. */
const LEVELS = 4;
const CELL_PX = 4;
/** Dot side within the cell. The gutter is what keeps this reading as a
 *  dithered field rather than tiles — past ~0.7 of the cell the gaps close on
 *  a retina grid (5px of an 8px cell here) and the texture goes blocky. */
const DOT_PX = 2.4;
/** Sweep half-width and travel, in CSS px and px/s. */
const SWEEP_PX = 110;
const SWEEP_SPEED = 150;
/** The field ramps in over this distance past the label, so the protected
 *  lane ends in a gradient rather than a visible cut. */
const LANE_FADE_PX = 70;
/** Amplitude of the resting field the sweep travels over. Two slow waves at
 *  different rates, so the band is never empty between passes and never
 *  repeats on a readable period. Kept low enough that only the top dither
 *  level is ever reached — the ambient is texture, the sweep is the event. */
const AMBIENT = 0.16;

/** Per-cell white noise. Stable in grid space, so cells light in a scattered
 *  order as the sweep passes instead of marching in a lattice. */
function cellNoise(x: number, y: number): number {
  let hash = Math.imul(x, 374761393) ^ Math.imul(y, 668265263);
  hash = Math.imul(hash ^ (hash >>> 13), 1274126177);
  return ((hash ^ (hash >>> 16)) >>> 0) / 4294967296;
}

export function WorkingStrip({ active, label }: { active: boolean; label: WorkingLabel }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const labelRef = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    const labelNode = labelRef.current;
    if (!canvas || !labelNode || !active) return;
    const context = canvas.getContext("2d");
    if (!context) return;

    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)");
    let width = 0;
    let height = 0;
    let cell = CELL_PX;
    let dot = DOT_PX;
    let inset = 0;
    let laneEnd = 0;
    let ratio = 1;
    let ink = "255,255,255";
    let frame = 0;

    const measure = () => {
      ratio = Math.min(window.devicePixelRatio || 1, 2);
      const rect = canvas.getBoundingClientRect();
      const nextWidth = Math.round(rect.width * ratio);
      const nextHeight = Math.round(rect.height * ratio);
      // Assigning width/height wipes the canvas, so only do it on a real
      // resize — a label change remeasures every step and would otherwise
      // blank a frame each time, reading as a stutter in the sweep.
      if (canvas.width !== nextWidth || canvas.height !== nextHeight) {
        canvas.width = nextWidth;
        canvas.height = nextHeight;
      }
      width = nextWidth;
      height = nextHeight;
      cell = Math.max(2, Math.round(CELL_PX * ratio));
      dot = Math.max(1, Math.round(DOT_PX * ratio));
      inset = Math.floor((cell - dot) / 2);
      laneEnd = labelNode.getBoundingClientRect().right - rect.left;
      // Sample the resolved ink token: the field is the theme's own colour,
      // and `--ink` is an oklab mix that only a paint can flatten to rgb.
      const probe = getComputedStyle(canvas).color;
      const match = /(\d+)\D+(\d+)\D+(\d+)/.exec(probe);
      if (match) ink = `${match[1]},${match[2]},${match[3]}`;
    };

    const draw = (elapsed: number) => {
      context.clearRect(0, 0, width, height);
      const cssWidth = width / ratio;
      const period = cssWidth + SWEEP_PX * 2;
      const sweepX = ((elapsed * SWEEP_SPEED) % period) - SWEEP_PX;

      context.fillStyle = `rgb(${ink})`;
      for (let row = 0, y = 0; y < height; row += 1, y += cell) {
        for (let column = 0, x = 0; x < width; column += 1, x += cell) {
          const cx = (x + cell / 2) / ratio;
          const cy = (y + cell / 2) / ratio;
          const offset = (cx - sweepX) / SWEEP_PX;
          const ambient =
            (Math.sin(cx * 0.013 - elapsed * 0.55) * 0.5 + 0.5) * 0.62 +
            (Math.sin(cx * 0.034 + cy * 0.06 + elapsed * 0.95) * 0.5 + 0.5) * 0.38;
          const lit =
            (Math.exp(-offset * offset * 2.2) + AMBIENT * ambient) *
            textLaneOpacity(cx, laneEnd, cssWidth, LANE_FADE_PX);
          if (lit <= 0.002) continue;
          const scaled = Math.min(lit, 1) * LEVELS;
          const floor = Math.floor(scaled);
          const step = (scaled - floor > cellNoise(column, row) ? floor + 1 : floor) / LEVELS;
          if (step <= 0) continue;
          context.globalAlpha = step;
          context.fillRect(x + inset, y + inset, dot, dot);
        }
      }
      context.globalAlpha = 1;
    };

    // One still frame stands in for the sweep whenever motion is unwanted or
    // unseen. The loop is not merely slowed — it is not scheduled at all, so
    // a strip that sits open for a long run costs nothing while the window is
    // in the background.
    const paintStill = () => {
      measure();
      draw(SWEEP_PX / SWEEP_SPEED);
    };

    let start = 0;
    const step = (now: number) => {
      if (!start) start = now;
      measure();
      draw((now - start) / 1000);
      frame = requestAnimationFrame(step);
    };

    const stop = () => {
      if (frame) cancelAnimationFrame(frame);
      frame = 0;
    };

    const sync = () => {
      stop();
      if (reduced.matches || document.hidden || !document.hasFocus()) {
        paintStill();
        return;
      }
      start = 0;
      frame = requestAnimationFrame(step);
    };

    sync();
    const observer = new ResizeObserver(sync);
    observer.observe(canvas);
    reduced.addEventListener("change", sync);
    document.addEventListener("visibilitychange", sync);
    window.addEventListener("focus", sync);
    window.addEventListener("blur", sync);
    return () => {
      stop();
      observer.disconnect();
      reduced.removeEventListener("change", sync);
      document.removeEventListener("visibilitychange", sync);
      window.removeEventListener("focus", sync);
      window.removeEventListener("blur", sync);
    };
    // `active` only. The label deliberately stays out: it changes on every
    // tool call, and re-running the effect would restart the sweep each time
    // — the field would visibly stutter against the work it is reporting.
    // Its width is remeasured every frame, so nothing goes stale.
  }, [active]);

  return (
    <div className="board-composer__strip" data-active={active ? "true" : "false"}>
      <div className="board-composer__strip-inner arden-peek-rule-below">
        <canvas ref={canvasRef} className="board-composer__strip-field" aria-hidden="true" />
        {/* The app's one text-swap treatment: exit upward, commit, enter
            from below. Continuity comes from the travel, so the two tool
            names are never on screen together. */}
        <span ref={labelRef} className="board-composer__strip-label">
          <BlurSwap swapKey={`${label.verb} ${label.target}`}>
            <span className="board-composer__strip-step">
              {label.verb}
              {label.target && <b>{label.target}</b>}
            </span>
          </BlurSwap>
        </span>
      </div>
    </div>
  );
}
