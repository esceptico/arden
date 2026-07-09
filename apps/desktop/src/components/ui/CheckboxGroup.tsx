import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type KeyboardEvent,
  type ReactNode,
  type Ref,
} from "react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import clsx from "clsx";
import { useItemRects, type ItemRect } from "@/hooks/useItemRects";
import { MOTION, SPRING_TAP } from "@/lib/tokens/motion";

// Corner radius the selected-background blocks round to. Matches the item's
// rounded-lg so a single selected row reads as one rounded pill.
const RADIUS = 8;

// ── Context ──────────────────────────────────────────────────
// The parent owns selection (value[]) and roving focus; items self-register
// their index + ref so the selection geometry can be measured from the live
// DOM.

interface CheckboxGroupContextValue {
  checked: Set<string>;
  toggle: (value: string) => void;
  register: (value: string, index: number, el: HTMLElement | null) => void;
  focusIndex: number;
}

const CheckboxGroupContext = createContext<CheckboxGroupContextValue | null>(null);

function useCheckboxGroupContext() {
  const ctx = useContext(CheckboxGroupContext);
  if (!ctx) throw new Error("CheckboxGroupItem must be used within a CheckboxGroup");
  return ctx;
}

// ── Merge / split signature ──────────────────────────────────
// Contiguous CHECKED rows merge into one rounded selection block; unchecking a
// bridging row splits it. The block geometry is recomputed from the live runs
// each render, so rapid toggles redirect instead of freezing. Ported from Fluid
// Functionalism (use-merge-split) and kept self-contained here.
//
// Simplification vs FF: we animate the OUTER corner radii of each contiguous run
// (a run's top/bottom corners round; mid-run corners stay sharp via the abutting
// halves) and spring the abutting halves together/apart. We drop FF's per-corner
// straightening *delay* (cornerDelay) and the zero-shift commit "ghost" frame —
// the survivor simply springs to the union rect. The merge/split feel is intact;
// the corner choreography is one tier simpler. See `radii` per block below.

const useIsoLayoutEffect = typeof window !== "undefined" ? useLayoutEffect : useEffect;

// Duration-based spring (not a stiffness/damping token) ON PURPOSE: the
// merge/split choreography arms a SPLIT_MS timer that must match the morph's
// settle time, and a stiffness/damping spring has no fixed duration to key it
// off. The focus-ring layer (which needs no timer) uses the shared SPRING_TAP
// token instead, matching RadioGroup's equivalent layer.
const EDGE_SPRING = { type: "spring" as const, duration: 0.16, bounce: 0 };
const SPLIT_MS = EDGE_SPRING.duration * 1000 + 80;

type Rect = { top: number; left: number; width: number; height: number };

interface SelBlock extends Rect {
  key: string;
  radii: [number, number, number, number]; // tl, tr, br, bl
  instant: boolean; // skip the spring (split snap-in / merge swap)
  enterFrom?: { top: number; height: number; radii: [number, number, number, number] };
}

type Run = { start: number; end: number; id: number };

interface Boundary {
  tid: number;
  kind: "split";
  survivorId: number; // upper run that persists
  otherId: number; // new lower run
  gapIndex: number; // deselected row where the halves part
}

// Two runs separated by exactly one row — the only shape a single click merges
// or splits.
function bridgePair(outer: Run, runs: Run[]) {
  const inside = runs
    .filter((r) => r.start >= outer.start && r.end <= outer.end)
    .sort((a, b) => a.start - b.start);
  if (inside.length !== 2) return null;
  const [up, lo] = inside;
  return lo.start === up.end + 2 ? { up, lo, gap: up.end + 1 } : null;
}

// Group sorted checked indices into contiguous runs, reusing a run's id when it
// overlaps a previous run so framer-motion morphs it across renders (merge) and
// a split's upper half keeps the survivor's identity.
function computeRuns(checkedIndices: number[], prev: Map<number, number>, nextId: () => number) {
  const sorted = [...checkedIndices].sort((a, b) => a - b);
  const raw: { start: number; end: number }[] = [];
  for (const idx of sorted) {
    const last = raw[raw.length - 1];
    if (last && idx === last.end + 1) last.end = idx;
    else raw.push({ start: idx, end: idx });
  }
  const used = new Set<number>();
  const next = new Map<number, number>();
  const runs: Run[] = raw.map((run) => {
    let id: number | null = null;
    for (let i = run.start; i <= run.end; i++) {
      const prevId = prev.get(i);
      if (prevId !== undefined && !used.has(prevId)) {
        id = prevId;
        break;
      }
    }
    const resolved = id ?? nextId();
    used.add(resolved);
    for (let i = run.start; i <= run.end; i++) next.set(i, resolved);
    return { ...run, id: resolved };
  });
  return { runs, next };
}

function useMergeSplitBlocks(runs: Run[], itemRects: ItemRect[], reduced: boolean): SelBlock[] {
  const [boundaries, setBoundaries] = useState<Boundary[]>([]);
  const prevRunsRef = useRef<Run[]>([]);
  const tidRef = useRef(0);
  const timersRef = useRef(new Map<number, ReturnType<typeof setTimeout>>());
  const runsSig = runs.map((r) => `${r.id}:${r.start}-${r.end}`).join("|");

  // Detect splits before paint so the first frame shows two abutting halves, and
  // drop any boundary the latest selection invalidated (bridge toggled again
  // mid-flight). Merges need no boundary state — a reused run id lets the
  // survivor spring straight to the union rect.
  useIsoLayoutEffect(() => {
    const prev = prevRunsRef.current;
    const found: Boundary[] = [];
    for (const p of prev) {
      const c = bridgePair(p, runs);
      if (c)
        found.push({
          tid: ++tidRef.current,
          kind: "split",
          survivorId: c.up.id,
          otherId: c.lo.id,
          gapIndex: c.gap,
        });
    }
    prevRunsRef.current = runs.map((r) => ({ ...r }));
    for (const b of found) {
      timersRef.current.set(
        b.tid,
        setTimeout(() => {
          timersRef.current.delete(b.tid);
          setBoundaries((bs) => bs.filter((x) => x.tid !== b.tid));
        }, SPLIT_MS),
      );
    }
    setBoundaries((active) => [
      ...active.filter((b) =>
        runs.some((c) => c.id === b.survivorId && c.end === b.gapIndex - 1) &&
        runs.some((c) => c.id === b.otherId && c.start === b.gapIndex + 1),
      ),
      ...found,
    ]);
  }, [runsSig]);

  useEffect(() => {
    const timers = timersRef.current;
    return () => timers.forEach(clearTimeout);
  }, []);

  const rectOf = (start: number, end: number): Rect | null => {
    const s = itemRects[start];
    const e = itemRects[end];
    if (!s || !e) return null;
    return {
      top: s.top,
      left: Math.min(s.left, e.left),
      width: Math.max(s.width, e.width),
      height: e.top + e.height - s.top,
    };
  };

  const blocks: SelBlock[] = [];
  for (const run of runs) {
    const r = rectOf(run.start, run.end);
    if (r)
      blocks.push({
        key: `sel-${run.id}`,
        ...r,
        radii: [RADIUS, RADIUS, RADIUS, RADIUS],
        instant: reduced,
      });
  }

  const byId = new Map(blocks.map((b) => [b.key, b]));
  // Pin a fresh split's halves at the deselected row's midpoint so the lower
  // half mounts on the seam and springs apart (instead of snapping into place).
  for (const b of boundaries) {
    const gap = itemRects[b.gapIndex];
    const sv = byId.get(`sel-${b.survivorId}`);
    const lo = byId.get(`sel-${b.otherId}`);
    if (!gap || !sv || !lo) continue;
    const midY = gap.top + gap.height / 2;
    const bottom = lo.top + lo.height;
    sv.height = midY - sv.top;
    sv.radii = [RADIUS, RADIUS, 0, 0];
    lo.top = midY;
    lo.height = bottom - midY;
    lo.radii = [0, 0, RADIUS, RADIUS];
    lo.enterFrom = { top: midY, height: bottom - midY, radii: [0, 0, RADIUS, RADIUS] };
  }

  return blocks;
}

function SelectionBackgrounds({ blocks, reduced }: { blocks: SelBlock[]; reduced: boolean }) {
  return (
    <AnimatePresence>
      {blocks.map((b) => {
        const opacity = 1;
        return (
          <motion.div
            key={b.key}
            aria-hidden
            className="absolute pointer-events-none bg-accent-soft"
            initial={
              b.enterFrom
                ? {
                    opacity,
                    top: b.enterFrom.top,
                    left: b.left,
                    width: b.width,
                    height: b.enterFrom.height,
                    borderTopLeftRadius: b.enterFrom.radii[0],
                    borderTopRightRadius: b.enterFrom.radii[1],
                    borderBottomRightRadius: b.enterFrom.radii[2],
                    borderBottomLeftRadius: b.enterFrom.radii[3],
                  }
                : false
            }
            animate={{
              top: b.top,
              left: b.left,
              width: b.width,
              height: b.height,
              borderTopLeftRadius: b.radii[0],
              borderTopRightRadius: b.radii[1],
              borderBottomRightRadius: b.radii[2],
              borderBottomLeftRadius: b.radii[3],
              opacity,
            }}
            exit={{ opacity: 0, transition: { duration: reduced ? 0 : MOTION.check } }}
            transition={
              b.instant
                ? { duration: 0 }
                : { ...EDGE_SPRING, opacity: { duration: MOTION.fast } }
            }
          />
        );
      })}
    </AnimatePresence>
  );
}

// ── CheckboxGroup ────────────────────────────────────────────

interface CheckboxGroupProps {
  value: string[];
  onChange: (next: string[]) => void;
  children: ReactNode;
  /** Accessible name for the group — maps to aria-label on the role="group". */
  "aria-label"?: string;
  className?: string;
  ref?: Ref<HTMLDivElement>;
}

export function CheckboxGroup({
  value,
  onChange,
  children,
  "aria-label": ariaLabel,
  className,
  ref,
}: CheckboxGroupProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const reduced = !!useReducedMotion();

  // value <-> index. Items register their value at their DOM index; selection is
  // a Set of values, runs are computed in index space for the merge/split.
  const indexToValue = useRef(new Map<number, string>());
  const checked = new Set(value);

  const { itemRects, registerItem, measureItems } = useItemRects();

  const [focusIndex, setFocusIndex] = useState(0);
  const [hasFocusVisible, setHasFocusVisible] = useState(false);

  // Stable so an item's mount-time register effect doesn't refire every render.
  const register = useCallback(
    (val: string, index: number, el: HTMLElement | null) => {
      if (el) indexToValue.current.set(index, val);
      else indexToValue.current.delete(index);
      registerItem(index, el);
    },
    [registerItem],
  );

  // Re-measure when the row COUNT changes (add/remove). registerItem already
  // schedules a remeasure rAF on mount; depending on unstable identities here
  // (children / the value array) would loop, since measureItems sets a fresh
  // itemRects array each call.
  const itemCount = indexToValue.current.size;
  useEffect(() => {
    measureItems();
  }, [measureItems, itemCount]);

  const toggle = (val: string) => {
    onChange(checked.has(val) ? value.filter((v) => v !== val) : [...value, val]);
  };

  // Contiguous runs of checked rows, in index space, with stable ids.
  const prevGroupMapRef = useRef(new Map<number, number>());
  const groupIdRef = useRef(0);
  const checkedIndices: number[] = [];
  indexToValue.current.forEach((val, idx) => {
    if (checked.has(val)) checkedIndices.push(idx);
  });
  const { runs, next } = computeRuns(checkedIndices, prevGroupMapRef.current, () => ++groupIdRef.current);
  prevGroupMapRef.current = next;

  const blocks = useMergeSplitBlocks(runs, itemRects, reduced);

  const focusRect = hasFocusVisible ? itemRects[focusIndex] : null;

  const moveFocus = (next: number) => {
    const items = Array.from(
      containerRef.current?.querySelectorAll<HTMLElement>("[data-checkbox-index]") ?? [],
    );
    items[next]?.focus();
    setFocusIndex(next);
  };

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(e.key)) return;
    e.preventDefault();
    const count = indexToValue.current.size;
    if (count === 0) return;
    let next = focusIndex;
    if (e.key === "ArrowDown") next = (focusIndex + 1) % count;
    else if (e.key === "ArrowUp") next = (focusIndex - 1 + count) % count;
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = count - 1;
    moveFocus(next);
  };

  const ctx: CheckboxGroupContextValue = {
    checked,
    toggle,
    register,
    focusIndex,
  };

  return (
    <CheckboxGroupContext.Provider value={ctx}>
      <div
        ref={(node) => {
          containerRef.current = node;
          if (typeof ref === "function") ref(node);
          else if (ref) (ref as { current: HTMLDivElement | null }).current = node;
        }}
        role="group"
        aria-label={ariaLabel}
        className={clsx("relative flex flex-col select-none", className)}
        onKeyDown={onKeyDown}
        onFocus={(e) => {
          const attr = (e.target as HTMLElement)
            .closest("[data-checkbox-index]")
            ?.getAttribute("data-checkbox-index");
          if (attr == null) return;
          const idx = Number(attr);
          setFocusIndex(idx);
          setHasFocusVisible((e.target as HTMLElement).matches(":focus-visible"));
        }}
        onBlur={(e) => {
          if (containerRef.current?.contains(e.relatedTarget as Node)) return;
          setHasFocusVisible(false);
        }}
      >
        {/* Selected backgrounds — merged for contiguous checked rows. */}
        <SelectionBackgrounds blocks={blocks} reduced={reduced} />

        {/* Focus ring — concentric, 2px outside the row. */}
        <AnimatePresence>
          {focusRect && (
            <motion.div
              aria-hidden
              className="absolute rounded-[10px] pointer-events-none z-20 border border-accent"
              initial={false}
              animate={{ left: focusRect.left - 2, top: focusRect.top - 2, width: focusRect.width + 4, height: focusRect.height + 4 }}
              exit={{ opacity: 0, transition: { duration: reduced ? 0 : MOTION.fast } }}
              transition={reduced ? { duration: 0 } : SPRING_TAP}
            />
          )}
        </AnimatePresence>

        {children}
      </div>
    </CheckboxGroupContext.Provider>
  );
}

// ── CheckboxGroupItem ────────────────────────────────────────

interface CheckboxGroupItemProps {
  value: string;
  label: string;
  description?: string;
}

export function CheckboxGroupItem({ value, label, description }: CheckboxGroupItemProps) {
  const ctx = useCheckboxGroupContext();
  const ref = useRef<HTMLDivElement | null>(null);
  const reduced = !!useReducedMotion();

  // Resolve the item's index from its DOM position among siblings (before paint
  // so geometry is correct on the first measured frame) and self-register. Deps
  // are stable (register is memoized) so this fires on mount / value change, not
  // every render.
  const { register } = ctx;
  const [i, setI] = useState(-1);
  useIsoLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const siblings = Array.from(
      el.parentElement?.querySelectorAll<HTMLElement>("[data-checkbox-item]") ?? [],
    );
    const idx = siblings.indexOf(el);
    setI(idx);
    register(value, idx, el);
    return () => register(value, idx, null);
  }, [value, register]);

  const checked = ctx.checked.has(value);
  const descId = useId();

  return (
    <div
      ref={ref}
      data-checkbox-item
      data-checkbox-index={i}
      role="checkbox"
      aria-checked={checked}
      aria-label={label}
      aria-describedby={description ? descId : undefined}
      tabIndex={ctx.focusIndex === i ? 0 : -1}
      onClick={() => ctx.toggle(value)}
      onKeyDown={(e) => {
        if (e.key === " " || e.key === "Enter") {
          e.preventDefault();
          ctx.toggle(value);
        }
      }}
      className="group relative z-10 flex items-start gap-2.5 rounded-lg px-3 py-2 outline-none hover:bg-fill-hover transition-colors duration-check ease-out"
    >
      {/* Checkbox glyph. */}
      <span
        aria-hidden
        className={clsx(
          "relative mt-px h-[15px] w-[15px] shrink-0 rounded-[5px] border-[1.5px] transition-colors",
          checked ? "border-accent bg-accent" : "border-line group-hover:border-line-strong",
        )}
        style={{ transitionDuration: `${MOTION.fast * 1000}ms` }}
      >
        <AnimatePresence>
          {checked && (
            <motion.svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth={3}
              strokeLinecap="round"
              strokeLinejoin="round"
              className="absolute left-1/2 top-1/2 h-[18px] w-[18px] -translate-x-1/2 -translate-y-1/2 text-on-ink"
            >
              <motion.path
                d="M6 12L10 16L18 8"
                initial={{ pathLength: reduced ? 1 : 0 }}
                animate={{ pathLength: 1, transition: { duration: reduced ? 0 : MOTION.check, ease: "easeOut" } }}
                exit={{ pathLength: 0, transition: { duration: reduced ? 0 : MOTION.fast, ease: "easeIn" } }}
              />
            </motion.svg>
          )}
        </AnimatePresence>
      </span>

      {/* Label + optional description. */}
      <span className="flex flex-col gap-0.5 leading-tight">
        <span
          className={clsx(
            "text-[13px] transition-colors",
            checked ? "text-ink" : "text-ink-soft group-hover:text-ink",
          )}
          style={{ fontWeight: checked ? 600 : 400, transitionDuration: `${MOTION.fast * 1000}ms` }}
        >
          {label}
        </span>
        {description && (
          <span id={descId} className="text-[12px] text-muted">
            {description}
          </span>
        )}
      </span>
    </div>
  );
}

export default CheckboxGroup;
