import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type RefObject,
} from "react";

const FOCUSABLE_SELECTOR = [
  "a[href]",
  "button:not([disabled])",
  "textarea:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  "[tabindex]:not([tabindex='-1'])",
].join(",");

/** Trap Tab focus inside `ref` while `active`: move focus into the panel on
 *  open (unless a child already grabbed it via autoFocus), wrap Tab/Shift+Tab
 *  at the edges, and restore focus to the previously-focused element on
 *  close/unmount. Pair with role="dialog" aria-modal and tabIndex={-1} on the
 *  trapped container (so it can receive focus when it has no focusable child).
 *  WAI-ARIA APG dialog pattern. */
export function useFocusTrap(ref: RefObject<HTMLElement | null>, active: boolean): void {
  useEffect(() => {
    if (!active) return;
    const node = ref.current;
    if (!node) return;
    const restoreTo = document.activeElement as HTMLElement | null;

    if (!node.contains(document.activeElement)) {
      const first = node.querySelector<HTMLElement>(FOCUSABLE_SELECTOR);
      (first ?? node).focus();
    }

    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Tab") return;
      const focusable = Array.from(
        node.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR),
      ).filter((el) => el.offsetParent !== null);
      if (focusable.length === 0) {
        e.preventDefault();
        node.focus();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const current = document.activeElement;
      if (e.shiftKey) {
        if (current === first || !node.contains(current)) {
          e.preventDefault();
          last.focus();
        }
      } else if (current === last || !node.contains(current)) {
        e.preventDefault();
        first.focus();
      }
    };

    node.addEventListener("keydown", onKey);
    return () => {
      node.removeEventListener("keydown", onKey);
      if (restoreTo && document.contains(restoreTo)) restoreTo.focus();
    };
  }, [ref, active]);
}

/** Returns `[flag, fire]`. `fire()` flips the flag true and schedules
 *  it back to false after `durationMs`. The pending timeout is cleared
 *  if the component unmounts (or `fire()` is called again) so the flag
 *  never updates after unmount — fixing the classic "setCopied(false)
 *  after the button is gone" warning. */
export function useTimeoutFlag(durationMs: number): readonly [boolean, () => void] {
  const [flag, setFlag] = useState(false);
  const timerRef = useRef<number | null>(null);
  useEffect(
    () => () => {
      if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    },
    [],
  );
  const fire = useCallback(() => {
    if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    setFlag(true);
    timerRef.current = window.setTimeout(() => {
      setFlag(false);
      timerRef.current = null;
    }, durationMs);
  }, [durationMs]);
  return [flag, fire] as const;
}

/** Fire `onEscape` while `active` is true. Replaces the boilerplate
 *  useEffect + addEventListener("keydown") + Escape branch that every
 *  modal/popover/picker would otherwise hand-roll. The callback is held
 *  in a ref so callers don't need to memoize it. */
export function useEscapeKey(onEscape: () => void, active = true): void {
  const ref = useRef(onEscape);
  useEffect(() => {
    ref.current = onEscape;
  }, [onEscape]);
  useEffect(() => {
    if (!active) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") ref.current();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [active]);
}

/**
 * Re-anchors a portaled overlay (popover, tooltip, picker) to its trigger.
 * Runs `onReposition` immediately (via useLayoutEffect, so coords commit before
 * paint — no wrong-corner flash) and again on window resize and scroll (capture
 * phase, so nested scroll containers also fire) while `active`. Pass `observe`
 * to also re-anchor when that element's own size changes (e.g. a tooltip whose
 * label grew). Call sites keep only their placement math. The callback is read
 * through a ref, so changing placement props doesn't re-bind the listeners.
 */
export function useReanchor(
  active: boolean,
  onReposition: () => void,
  observe?: RefObject<Element | null>,
): void {
  const cb = useRef(onReposition);
  cb.current = onReposition;
  useLayoutEffect(() => {
    if (!active) return;
    const run = () => cb.current();
    run();
    window.addEventListener("resize", run);
    window.addEventListener("scroll", run, true);
    const ro = observe?.current ? new ResizeObserver(run) : null;
    if (observe?.current) ro!.observe(observe.current);
    return () => {
      window.removeEventListener("resize", run);
      window.removeEventListener("scroll", run, true);
      ro?.disconnect();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active]);
}

export interface MutationState {
  busy: boolean;
  /** Transient flag — true for ~1.5s after a `run` resolves without error.
   *  Drives the shared "Saved" confirmation (see SaveStatus). */
  saved: boolean;
  error: string | null;
  run: (fn: () => Promise<void>) => Promise<void>;
}

const SAVED_HOLD_MS = 1500;

/** Shared busy/saved/error state for save+delete handlers. */
export function useMutationState(): MutationState {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, fireSaved] = useTimeoutFlag(SAVED_HOLD_MS);

  async function run(fn: () => Promise<void>) {
    setBusy(true);
    setError(null);
    try {
      await fn();
      fireSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return { busy, saved, error, run };
}

/** Forces a re-render every `intervalMs` ms. Use to refresh relative-time
 *  labels ("2m ago") without each consumer wiring its own timer. */
export function useTimeTicker(intervalMs = 30_000): void {
  const [, setTick] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setTick((n) => n + 1), intervalMs);
    return () => clearInterval(id);
  }, [intervalMs]);
}

/** Roving-index keyboard nav for a list of options. Pass `items.length`
 *  and either controlled `{index, setIndex}` or omit both to use internal
 *  state. Returns `{index, setIndex, onKeyDown}` — wire `onKeyDown` to the
 *  input/container that owns focus.
 *
 *  Handles ArrowUp/ArrowDown (clamped to [0, length-1]) and Enter
 *  (invokes `onEnter(index)`). Escape/Backspace are NOT handled — owners
 *  with drill-down or close semantics keep that logic. */
export function useListNav(
  length: number,
  onEnter: (index: number) => void,
  controlled?: { index: number; setIndex: (i: number) => void },
): {
  index: number;
  setIndex: (i: number) => void;
  onKeyDown: (e: ReactKeyboardEvent<HTMLElement>) => void;
} {
  const [internal, setInternal] = useState(0);
  const rawIndex = controlled ? controlled.index : internal;
  const setIndex = controlled ? controlled.setIndex : setInternal;
  const last = Math.max(0, length - 1);
  const index = Math.min(Math.max(rawIndex, 0), last);

  // Converge controlled state when it falls out of bounds (shorter list after
  // a filter change). Uncontrolled state can't drift past `last` because
  // setIndex calls below all clamp.
  useEffect(() => {
    if (rawIndex !== index) setIndex(index);
  }, [rawIndex, index, setIndex]);

  const enterRef = useRef(onEnter);
  enterRef.current = onEnter;

  const onKeyDown = useCallback(
    (e: ReactKeyboardEvent<HTMLElement>) => {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setIndex(Math.min(index + 1, last));
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setIndex(Math.max(index - 1, 0));
        return;
      }
      if (e.key === "Enter") {
        e.preventDefault();
        enterRef.current(index);
      }
    },
    [index, last, setIndex],
  );

  return { index, setIndex, onKeyDown };
}


/** Invokes `callback` once on mount, then on each `intervalMs` tick
 *  (skipped when the document is hidden), and again on every visibility
 *  transition back to "visible". The latest `callback` is captured in a
 *  ref so consumers don't need to memoize it.
 *
 *  Use for background data refresh that should pause when the user
 *  switches away from the window (saves API calls, respects user focus). */
export function useVisibilityPoll(
  callback: () => void | Promise<void>,
  intervalMs: number,
): void {
  const cbRef = useRef(callback);
  cbRef.current = callback;
  useEffect(() => {
    let cancelled = false;
    const tick = () => {
      if (!cancelled) void cbRef.current();
    };
    tick();
    const id = window.setInterval(() => {
      if (document.visibilityState === "visible") tick();
    }, intervalMs);
    const onVis = () => {
      if (document.visibilityState === "visible") tick();
    };
    document.addEventListener("visibilitychange", onVis);
    return () => {
      cancelled = true;
      window.clearInterval(id);
      document.removeEventListener("visibilitychange", onVis);
    };
  }, [intervalMs]);
}
