import {
  createContext,
  useCallback,
  useContext,
  useLayoutEffect,
  useRef,
  useState,
  type ReactNode,
  type RefObject,
} from "react";
import { useReducedMotion } from "motion/react";
import clsx from "clsx";
import { Tooltip } from "@/components/ui/Tooltip";
import { TAB_INDICATOR_LINE_SIZE } from "@/lib/tokens/motion";

type Variant = "underline" | "plain" | "segmented" | "expanding" | "sidebar" | "surface";
type Orientation = "horizontal" | "vertical";
type Size = "sm" | "md" | "lg";

const ITEM_SIZE: Record<Size, string> = {
  sm: "",
  md: "",
  lg: "h-10 px-3.5 text-sm",
};

interface TabsContextValue {
  value: string;
  select: (value: string) => void;
  orientation: Orientation;
  variant: Variant;
  size: Size;
}

interface TabIndicatorGeometry {
  x: number;
  y: number;
  width: number;
  height: number;
}

const TabsContext = createContext<TabsContextValue | null>(null);

function useTabsContext(): TabsContextValue {
  const ctx = useContext(TabsContext);
  if (!ctx) throw new Error("<Tab> must be used inside <Tabs>");
  return ctx;
}

function sameGeometry(left: TabIndicatorGeometry | null, right: TabIndicatorGeometry): boolean {
  return !!left
    && left.x === right.x
    && left.y === right.y
    && left.width === right.width
    && left.height === right.height;
}

function tabLineSize(container: HTMLElement): number {
  const value = Number.parseFloat(getComputedStyle(container).getPropertyValue("--tab-line-size"));
  return Number.isFinite(value) ? value : TAB_INDICATOR_LINE_SIZE;
}

function useTabIndicator(
  listRef: RefObject<HTMLDivElement | null>,
  value: string,
  variant: Variant,
): { geometry: TabIndicatorGeometry | null; ready: boolean } {
  const [geometry, setGeometry] = useState<TabIndicatorGeometry | null>(null);
  const [ready, setReady] = useState(false);
  const hasMeasured = useRef(false);

  useLayoutEffect(() => {
    if (variant === "plain") {
      hasMeasured.current = false;
      setGeometry(null);
      setReady(false);
      return;
    }

    const container = listRef.current;
    if (!container) return;
    let frame: number | undefined;
    const measure = () => {
      const tabs = Array.from(container.querySelectorAll<HTMLButtonElement>('[role="tab"]'));
      const target = tabs.find((tab) => tab.dataset.tabValue === value);
      if (!target || !container.getClientRects().length || !target.getClientRects().length) {
        hasMeasured.current = false;
        setGeometry(null);
        setReady(false);
        return;
      }

      const containerRect = container.getBoundingClientRect();
      const targetRect = target.getBoundingClientRect();
      const line = variant === "underline";
      const height = line ? tabLineSize(container) : targetRect.height;
      const next = {
        x: targetRect.left - containerRect.left + container.scrollLeft,
        y: line
          ? container.scrollTop + containerRect.height - height
          : targetRect.top - containerRect.top + container.scrollTop,
        width: targetRect.width,
        height,
      };
      setGeometry((current) => (sameGeometry(current, next) ? current : next));

      if (hasMeasured.current) return;
      hasMeasured.current = true;
      frame = window.requestAnimationFrame(() => {
        frame = undefined;
        setReady(true);
      });
    };

    measure();
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(measure);
    observer?.observe(container);
    for (const tab of container.querySelectorAll<HTMLButtonElement>('[role="tab"]')) observer?.observe(tab);
    window.addEventListener("resize", measure);

    return () => {
      if (frame !== undefined) {
        window.cancelAnimationFrame(frame);
        frame = undefined;
        hasMeasured.current = false;
      }
      observer?.disconnect();
      window.removeEventListener("resize", measure);
    };
  }, [listRef, value, variant]);

  return { geometry, ready };
}

function TabIndicator({
  variant,
  className,
  geometry,
  ready,
  reducedMotion,
}: {
  variant: Exclude<Variant, "plain">;
  className?: string;
  geometry: TabIndicatorGeometry | null;
  ready: boolean;
  reducedMotion: boolean;
}) {
  return (
    <span
      aria-hidden="true"
      data-tab-indicator={variant}
      data-ready={ready ? "true" : undefined}
      hidden={!geometry}
      className={clsx(
        "arden-tab-indicator",
        variant === "underline" && "arden-tab-indicator--underline",
        className,
      )}
      style={
        geometry
          ? {
              width: `${geometry.width}px`,
              height: `${geometry.height}px`,
              transform: `translate(${geometry.x}px, ${geometry.y}px)`,
              transition: variant === "sidebar" || !ready || reducedMotion ? "none" : undefined,
            }
          : { transition: "none" }
      }
    />
  );
}

/** One canonical tab primitive. Geometry commits immediately; only the
 * indicator's compositor transform travels between tabs. */
export function Tabs({
  value,
  onChange,
  variant = "underline",
  orientation = "horizontal",
  size = "md",
  label,
  indicatorClassName,
  className,
  listRef: externalListRef,
  children,
}: {
  value: string;
  onChange: (value: string) => void;
  variant?: Variant;
  orientation?: Orientation;
  size?: Size;
  label?: string;
  indicatorClassName?: string;
  className?: string;
  /** Exposes the tablist for overflow/ensure-visible controllers. */
  listRef?: RefObject<HTMLDivElement | null>;
  children: ReactNode;
}) {
  const internalListRef = useRef<HTMLDivElement>(null);
  const listRef = externalListRef ?? internalListRef;
  const reducedMotion = useReducedMotion() ?? false;
  const { geometry, ready } = useTabIndicator(listRef, value, variant);
  const select = useCallback(
    (nextValue: string) => {
      if (nextValue === value) return;
      onChange(nextValue);
    },
    [onChange, value],
  );

  return (
    <TabsContext.Provider value={{ value, select, orientation, variant, size }}>
      <div
        ref={listRef}
        role="tablist"
        aria-label={label}
        aria-orientation={orientation}
        data-tabs-variant={variant}
        data-tabs-expanding={variant === "expanding" ? "true" : undefined}
        className={clsx(
          "relative flex",
          orientation === "vertical" && "flex-col",
          variant === "segmented" && "arden-segmented segmented-control",
          variant === "expanding" &&
            "segmented-control inline-flex items-center gap-[3px] rounded-[var(--r-control)] p-[3px] bg-[color-mix(in_oklab,var(--color-ink)_6%,transparent)]",
          className,
        )}
      >
        {variant !== "plain" && (
          <TabIndicator
            variant={variant}
            className={indicatorClassName}
            geometry={geometry}
            ready={ready}
            reducedMotion={reducedMotion}
          />
        )}
        {children}
      </div>
    </TabsContext.Provider>
  );
}

export function Tab({
  value,
  id,
  "aria-label": ariaLabel,
  controls,
  disabled,
  title,
  className,
  children,
}: {
  value: string;
  id?: string;
  "aria-label"?: string;
  controls?: string;
  disabled?: boolean;
  title?: string;
  className?: string;
  children?: ReactNode;
}) {
  const ctx = useTabsContext();
  const active = ctx.value === value;

  const onKeyDown = (event: React.KeyboardEvent<HTMLButtonElement>) => {
    const horizontal = ctx.orientation === "horizontal";
    const nextKey = horizontal ? "ArrowRight" : "ArrowDown";
    const prevKey = horizontal ? "ArrowLeft" : "ArrowUp";
    if (![nextKey, prevKey, "Home", "End"].includes(event.key)) return;
    const tablist = event.currentTarget.closest('[role="tablist"]');
    if (!tablist) return;
    const tabs = Array.from(
      tablist.querySelectorAll<HTMLButtonElement>('[role="tab"]:not([disabled])'),
    );
    const index = tabs.indexOf(event.currentTarget);
    if (index === -1) return;
    event.preventDefault();
    const next =
      event.key === "Home" ? 0
      : event.key === "End" ? tabs.length - 1
      : event.key === nextKey ? (index + 1) % tabs.length
      : (index - 1 + tabs.length) % tabs.length;
    const target = tabs[next];
    target.focus();
    const nextValue = target.getAttribute("data-tab-value");
    if (nextValue !== null) ctx.select(nextValue);
  };

  const button = (
    <button
      type="button"
      role="tab"
      id={id}
      aria-label={ariaLabel}
      aria-controls={controls}
      aria-selected={active}
      tabIndex={active ? 0 : -1}
      data-active={active ? "true" : undefined}
      data-tab-value={value}
      disabled={disabled}
      onClick={() => ctx.select(value)}
      onKeyDown={onKeyDown}
      className={clsx(
        "group relative z-[var(--z-raised)]",
        (ctx.variant === "segmented" || ctx.variant === "expanding") &&
          clsx(
            "inline-flex items-center justify-center gap-1.5 whitespace-nowrap rounded-[calc(var(--r-control)-3px)] font-medium",
            "text-muted hover:text-ink data-[active]:text-ink",
            "[transition:color_var(--tabs-dur)_var(--tabs-ease)]",
            "outline-none focus-visible:ring-2 focus-visible:ring-accent",
            ctx.variant === "expanding"
              ? "h-7 min-w-7 flex-1 px-0 data-[active]:flex-none"
              : clsx("arden-segmented-tab", ITEM_SIZE[ctx.size]),
          ),
        className,
      )}
    >
      {ctx.variant === "expanding" ? (
        <span className="relative z-[var(--z-control-content)] inline-flex h-7 items-center justify-center gap-1.5 px-2.5">
          {children}
        </span>
      ) : (
        <span className="arden-tab-content relative z-[var(--z-control-content)]">{children}</span>
      )}
    </button>
  );

  // `title` raises the house tooltip, not the OS bubble — matching IconButton.
  // Pass it only when the tab has no visible label of its own; a tooltip that
  // repeats text already on screen is noise.
  return title ? <Tooltip label={title}>{button}</Tooltip> : button;
}
