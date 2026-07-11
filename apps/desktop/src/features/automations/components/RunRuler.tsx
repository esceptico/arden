import { useEffect, useMemo, useRef, useState } from "react";
import type { AutomationRun } from "@/api/types";
import { formatDuration } from "@/lib/agentRun";

/** Time as an instrument, not a chart (port of the InstrumentRuler lab study,
 *  laid horizontal): runs are ink ticks crossing a dotted rule, fading with
 *  age, with graduation ticks only where labels are, an ink now-mark, and
 *  the next fire as a faint tick ahead of now.
 *
 *  The tape AUTO-SCALES to the data — an hourly automation gets an hours-wide
 *  window, a daily one gets weeks — and the wheel PANS it (inertial ease,
 *  lab-style) back through history. Hover brightens marks in a gaussian
 *  around the pointer and floats ONE readout chip that magnet-snaps to the
 *  nearest mark. */

const H = 64;
const BASE = 36;
/** Magnet radius in px — touching the mark, not tiling the tape. */
const SNAP_PX = 14;
const HOUR = 3_600_000;
const DAY = 24 * HOUR;
const MIN_SPAN = 6 * HOUR;
const MAX_SPAN = 14 * DAY;
/** Tick steps to choose from — smallest that yields ≤6 labels wins. */
const TICK_STEPS = [HOUR, 3 * HOUR, 6 * HOUR, 12 * HOUR, DAY, 2 * DAY, 4 * DAY, 7 * DAY];

export interface RulerRun {
  run: AutomationRun;
  at: number;
  ok: boolean;
  label: string;
  duration: string | null;
}

export function rulerRunsFrom(runs: AutomationRun[]): RulerRun[] {
  return runs
    .filter((r) => r.status !== "running")
    .map((r) => {
      const at = Date.parse(r.started_at);
      const ms = r.ended_at != null ? Date.parse(r.ended_at) - at : NaN;
      return {
        run: r,
        at,
        ok: r.status !== "failed",
        label: shortStamp(at),
        // Sub-second runs are recorder artifacts, not measurements.
        duration: Number.isFinite(ms) && ms >= 1000 ? formatDuration(ms) : null,
      };
    })
    .filter((r) => Number.isFinite(r.at))
    .sort((a, b) => a.at - b.at);
}

/** Window width fitted to the data: wide enough to hold the fetched history,
 *  clamped so hourly automations get hours and monthly ones get the cap. */
export function rulerSpanMs(runs: RulerRun[], now = Date.now()): number {
  if (runs.length < 2) return MAX_SPAN;
  return Math.min(MAX_SPAN, Math.max(MIN_SPAN, (now - runs[0].at) * 1.08));
}

export function spanLabel(spanMs: number): string {
  if (spanMs < 2 * DAY) return `last ${Math.round(spanMs / HOUR)}h`;
  return `last ${Math.round(spanMs / DAY)} days`;
}

function shortStamp(t: number): string {
  const d = new Date(t);
  const day = d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  const hm = d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", hour12: false });
  return `${day} ${hm}`;
}

export function RunRuler({
  runs,
  nextRunAt,
  paused,
  waitingLabel,
  onHoverRun,
  onOpenRun,
}: {
  runs: RulerRun[];
  nextRunAt: string | null;
  paused: boolean;
  /** Future-zone text when there is no scheduled next fire (event-driven). */
  waitingLabel: string;
  onHoverRun?: (id: number | null) => void;
  onOpenRun?: (run: AutomationRun) => void;
}) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const chipRef = useRef<HTMLDivElement>(null);
  const markRefs = useRef(new Map<number, SVGRectElement>());
  const hoverId = useRef<number | null>(null);
  const [width, setWidth] = useState(0);
  const [pan, setPan] = useState(0);

  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setWidth(el.clientWidth));
    ro.observe(el);
    setWidth(el.clientWidth);
    return () => ro.disconnect();
  }, []);

  const now = Date.now();
  const span = rulerSpanMs(runs, now);
  const ahead = span * 0.14;
  // Pan slides the window into the past; 0 = the present.
  const maxPan = runs.length > 0 ? Math.max(0, now - runs[0].at - span / 2) : 0;
  const t0 = now - span - pan;
  const t1 = now + ahead - pan;
  const X = (t: number) => ((t - t0) / (t1 - t0)) * width;

  // Inertial wheel pan (lab pattern): wheel moves a target, the tape eases
  // after it; reduced motion snaps.
  const panCur = useRef(0);
  const panTarget = useRef(0);
  const rafRef = useRef<number | null>(null);
  const maxPanRef = useRef(maxPan);
  maxPanRef.current = maxPan;
  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
    const kick = () => {
      if (rafRef.current != null) return;
      const step = () => {
        const d = panTarget.current - panCur.current;
        panCur.current = Math.abs(d) < 1 ? panTarget.current : panCur.current + d * (reduced ? 1 : 0.16);
        setPan(panCur.current);
        rafRef.current = panCur.current === panTarget.current ? null : requestAnimationFrame(step);
      };
      rafRef.current = requestAnimationFrame(step);
    };
    const onWheel = (e: WheelEvent) => {
      if (maxPanRef.current <= 0) return;
      e.preventDefault();
      const w = el.clientWidth || 1;
      const spanNow = rulerSpanMs(runs);
      const msPerPx = (spanNow * 1.14) / w;
      const delta = (Math.abs(e.deltaX) > Math.abs(e.deltaY) ? e.deltaX : e.deltaY) * msPerPx;
      panTarget.current = Math.min(maxPanRef.current, Math.max(0, panTarget.current + delta));
      kick();
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => {
      el.removeEventListener("wheel", onWheel);
      if (rafRef.current != null) cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    };
  }, [runs]);

  const visible = useMemo(() => runs.filter((r) => r.at >= t0 && r.at <= t1), [runs, t0, t1]);
  const nextAt = nextRunAt ? Date.parse(nextRunAt) : null;

  // Graduation ticks on nice boundaries at a step fitted to the span —
  // texture lives in the dotted baseline, real ticks only where labels are.
  const ticks = useMemo(() => {
    const step = TICK_STEPS.find((s) => span / s <= 6) ?? 7 * DAY;
    const offset = new Date(t0).getTimezoneOffset() * 60_000;
    const out: { t: number; label: string }[] = [];
    let t = Math.ceil((t0 - offset) / step) * step + offset;
    for (; t <= t1; t += step) {
      const d = new Date(t);
      out.push({
        t,
        label:
          step < DAY
            ? d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", hour12: false })
            : d.toLocaleDateString(undefined, { month: "short", day: "numeric" }),
      });
    }
    return out;
  }, [t0, t1, span]);

  // Recency ladder over the FULL history, not the visible slice, so panning
  // doesn't re-normalize brightness.
  const rank = useMemo(() => new Map(runs.map((r, i) => [r.run.id, i])), [runs]);
  const baseOpacity = (id: number) =>
    runs.length <= 1 ? 0.9 : 0.3 + 0.62 * ((rank.get(id) ?? 0) / (runs.length - 1));

  const clearHover = () => {
    const chip = chipRef.current;
    if (chip) {
      chip.style.opacity = "0";
      chip.style.filter = "blur(2px)";
    }
    markRefs.current.forEach((el) => {
      el.style.transition = "opacity 300ms";
      el.style.opacity = el.dataset.op ?? "1";
    });
    if (hoverId.current !== null) {
      hoverId.current = null;
      onHoverRun?.(null);
    }
  };

  const onPointerMove = (e: React.PointerEvent) => {
    const wrap = wrapRef.current;
    const chip = chipRef.current;
    if (!wrap || !chip || width === 0) return;
    const px = e.clientX - wrap.getBoundingClientRect().left;
    // Attention: marks brighten in a gaussian around the pointer. Raw writes
    // while live — a transition here would restart every frame and trail.
    markRefs.current.forEach((el) => {
      const d = Math.abs(Number(el.dataset.x) - px);
      const boost = Math.exp(-(d * d) / (2 * 60 * 60));
      el.style.transition = "none";
      el.style.opacity = String(Math.min(1, Number(el.dataset.op ?? "1") + 0.7 * boost));
    });
    let nearest: RulerRun | null = null;
    let best = SNAP_PX;
    for (const r of visible) {
      const d = Math.abs(X(r.at) - px);
      if (d < best) {
        best = d;
        nearest = r;
      }
    }
    if (nearest) {
      // Terse chip — status word only; error detail lives in the ledger row
      // and the opened run.
      chip.textContent = `${nearest.label}${nearest.duration ? ` · ${nearest.duration}` : ""} · ${
        nearest.ok ? "ok" : "failed"
      }`;
      chip.style.color = nearest.ok ? "" : "var(--color-bad)";
      const half = chip.offsetWidth / 2;
      chip.style.left = `${Math.min(Math.max(X(nearest.at), half), width - half)}px`;
      chip.style.opacity = "1";
      chip.style.filter = "blur(0px)";
      if (hoverId.current !== nearest.run.id) {
        hoverId.current = nearest.run.id;
        onHoverRun?.(nearest.run.id);
      }
    } else {
      chip.style.opacity = "0";
      chip.style.filter = "blur(2px)";
      if (hoverId.current !== null) {
        hoverId.current = null;
        onHoverRun?.(null);
      }
    }
  };

  const onClick = () => {
    if (hoverId.current === null) return;
    const hit = visible.find((r) => r.run.id === hoverId.current);
    if (hit) onOpenRun?.(hit.run);
  };

  const nowVisible = now >= t0 && now <= t1;

  // Every bottom-line auxiliary text (next-run label, midpoint waiting/
  // paused/next text) yields entirely when it can't clear the now mark —
  // the stats strip below carries the same fact, so nothing is lost.
  const clearsNow = (x: number) => !nowVisible || Math.abs(x - X(now)) >= 48;
  // Where the auxiliary bottom-line label actually renders — tick labels on
  // the same baseline must clear it.
  const auxLabelX =
    paused || nextAt == null || nextAt > t1 || nextAt < t0
      ? nowVisible
        ? width - 30 // future-zone text right-anchors at the tape edge
        : null
      : X(nextAt);

  return (
    <div
      ref={wrapRef}
      className="relative h-16 cursor-crosshair touch-none"
      onPointerMove={onPointerMove}
      onPointerLeave={clearHover}
      onClick={onClick}
    >
      {width > 0 && (
        <svg
          className="absolute inset-0 w-full h-full overflow-visible"
          viewBox={`0 0 ${width} ${H}`}
          aria-hidden="true"
        >
          <line
            x1={0}
            y1={BASE}
            x2={width}
            y2={BASE}
            stroke="var(--color-whisper)"
            strokeWidth={1}
            strokeDasharray="1 4"
          />
          {ticks
            .filter(({ t }) => {
              // Drop tick labels that would collide with the now-mark or
              // whatever auxiliary label shares their baseline.
              if (nowVisible && Math.abs(X(t) - X(now)) < 44) return false;
              return auxLabelX == null || Math.abs(X(t) - auxLabelX) >= 60;
            })
            .map(({ t, label }) => (
              <g key={t}>
                <line
                  x1={X(t)}
                  y1={BASE - 6}
                  x2={X(t)}
                  y2={BASE + 6}
                  stroke="var(--color-line-strong)"
                  strokeWidth={1}
                />
                <text
                  x={X(t)}
                  y={BASE + 24}
                  textAnchor="middle"
                  fontSize={10}
                  className="font-mono"
                  fill="var(--color-faint)"
                >
                  {label}
                </text>
              </g>
            ))}
          {/* Runs are ink ticks crossing the rule (lifeline-style marks, not
              dots), fading with age. */}
          {visible.map((r) => (
            <rect
              key={r.run.id}
              ref={(el) => {
                if (el) markRefs.current.set(r.run.id, el);
                else markRefs.current.delete(r.run.id);
              }}
              x={X(r.at) - 1}
              y={BASE - 5}
              width={2}
              height={10}
              rx={1}
              fill={r.ok ? "var(--color-ink)" : "var(--color-bad)"}
              opacity={r.ok ? baseOpacity(r.run.id) : 1}
              data-x={X(r.at).toFixed(1)}
              data-op={r.ok ? baseOpacity(r.run.id).toFixed(2) : "1"}
            />
          ))}
          {nowVisible && (
            <>
              <line
                x1={X(now)}
                y1={BASE - 12}
                x2={X(now)}
                y2={BASE + 12}
                stroke="var(--color-ink)"
                strokeWidth={1.2}
              />
              <text
                x={X(now)}
                y={BASE - 20}
                textAnchor="middle"
                fontSize={10}
                className="font-mono"
                fill="var(--color-ink)"
              >
                now
              </text>
            </>
          )}
          {paused ? (
            nowVisible && clearsNow(width - 30) && (
              <text
                x={width}
                y={BASE + 24}
                textAnchor="end"
                fontSize={10}
                className="font-mono"
                fill="var(--color-whisper)"
              >
                paused
              </text>
            )
          ) : nextAt != null && nextAt <= t1 && nextAt >= t0 ? (
            <g>
              {/* The next fire is a faint tick ahead of now — planned, not
                  yet ink. */}
              <rect
                x={X(nextAt) - 1}
                y={BASE - 5}
                width={2}
                height={10}
                rx={1}
                fill="var(--color-faint)"
                opacity={0.55}
              />
              {/* The label yields when the next fire hugs the now-mark —
                  the stats strip already reads "next in …"; the faint tick
                  alone carries the position. */}
              {clearsNow(X(nextAt)) && (
                <text
                  x={X(nextAt)}
                  y={BASE + 24}
                  textAnchor="middle"
                  fontSize={10}
                  className="font-mono"
                  fill="var(--color-faint)"
                >
                  {nextLabel(nextAt)}
                </text>
              )}
            </g>
          ) : (
            nowVisible && clearsNow(width - 30) && (
              <text
                x={width}
                y={BASE + 24}
                textAnchor="end"
                fontSize={10}
                className="font-mono"
                fill="var(--color-whisper)"
              >
                {nextAt != null ? nextLabel(nextAt) : waitingLabel}
              </text>
            )
          )}
        </svg>
      )}
      {/* One readout chip riding the popover surface — resolves in/out through
          a small blur (continuous, never snapped), eased slide between marks. */}
      <div
        ref={chipRef}
        className="pointer-events-none absolute -top-1 -translate-x-1/2 whitespace-nowrap surface-panel surface-popover rounded-[5px] px-1.5 py-0.5 font-mono text-2xs text-ink tabular-nums motion-reduce:transition-none"
        style={{
          opacity: 0,
          filter: "blur(2px)",
          transition:
            "left 200ms cubic-bezier(0.2,0.8,0.2,1), opacity 200ms cubic-bezier(0.2,0.8,0.2,1), filter 200ms cubic-bezier(0.2,0.8,0.2,1)",
        }}
      />
    </div>
  );
}

function nextLabel(t: number): string {
  const d = new Date(t);
  const hm = d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", hour12: false });
  const today = new Date();
  if (d.toDateString() === today.toDateString()) return hm;
  const tomorrow = new Date(today.getTime() + DAY);
  if (d.toDateString() === tomorrow.toDateString()) return `tmrw ${hm}`;
  return `${d.toLocaleDateString(undefined, { month: "short", day: "numeric" })} ${hm}`;
}
