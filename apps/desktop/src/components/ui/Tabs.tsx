import {
  createContext,
  useCallback,
  useContext,
  useId,
  type ReactNode,
} from "react";
import clsx from "clsx";
import { motion } from "motion/react";
import { SPRING_LAYOUT } from "@/lib/tokens/motion";

// The one tabs primitive:
//   "underline" — sliding 2px bar under the active tab (modal header tabs).
//   "segmented" — tinted track + shared-layout pill spanning the active item.
//   "expanding" — equal hit targets; active icon + label get a natural-width pill.
//   "plain"     — no animated indicator; the active state is the item's own
//                 static tint (the app's tint-only vertical menus).
type Variant = "underline" | "plain" | "segmented" | "expanding";
type Orientation = "horizontal" | "vertical";
type Size = "sm" | "md" | "lg";

// Literal class strings so Tailwind sees them.
const ITEM_SIZE: Record<Size, string> = {
  sm: "h-7 px-2.5 text-xs",
  md: "h-[33px] px-3 text-[13px]",
  lg: "h-10 px-3.5 text-sm",
};

interface TabsContextValue {
  value: string;
  select: (value: string) => void;
  orientation: Orientation;
  variant: Variant;
  size: Size;
  indicatorLayoutId: string;
  indicatorClassName?: string;
}

const TabsContext = createContext<TabsContextValue | null>(null);

function useTabsContext(): TabsContextValue {
  const ctx = useContext(TabsContext);
  if (!ctx) throw new Error("<Tab> must be used inside <Tabs>");
  return ctx;
}

/** Motion's shared-layout tabs pattern: each active tab mounts the same
 * layoutId. Motion transfers one pill between targets without manual width or
 * glyph measurements. */
export function Tabs({
  value,
  onChange,
  variant = "underline",
  orientation = "horizontal",
  size = "md",
  label,
  indicatorClassName,
  className,
  children,
}: {
  value: string;
  onChange: (value: string) => void;
  variant?: Variant;
  orientation?: Orientation;
  /** Segment sizing for variant="segmented" items. */
  size?: Size;
  /** Accessible name for the group — maps to aria-label on the tablist. */
  label?: string;
  indicatorClassName?: string;
  className?: string;
  children: ReactNode;
}) {
  const indicatorLayoutId = `tab-indicator-${useId().replace(/:/g, "")}`;
  const segmented = variant === "segmented" || variant === "expanding";
  const select = useCallback(
    (nextValue: string) => {
      if (nextValue === value) return;
      onChange(nextValue);
    },
    [onChange, value],
  );
  return (
    <TabsContext.Provider
      value={{
        value,
        select,
        orientation,
        variant,
        size,
        indicatorLayoutId,
        indicatorClassName,
      }}
    >
      <div
        role="tablist"
        aria-label={label}
        aria-orientation={orientation}
        className={clsx(
          "relative flex",
          orientation === "vertical" && "flex-col",
          segmented &&
            "segmented-control inline-flex items-center gap-[3px] rounded-full p-[3px] bg-[color-mix(in_oklab,var(--color-ink)_6%,transparent)]",
          className,
        )}
      >
        {children}
      </div>
    </TabsContext.Provider>
  );
}

export function Tab({
  value,
  id,
  "aria-label": ariaLabel,
  className,
  children,
}: {
  value: string;
  id?: string;
  /** Accessible name for icon-only tabs. */
  "aria-label"?: string;
  className?: string;
  children?: ReactNode;
}) {
  const ctx = useTabsContext();
  const active = ctx.value === value;

  // APG tabs pattern: arrow keys (orientation-aware) + Home/End move between
  // tabs with automatic activation; roving tabindex keeps only the selected
  // tab in the Tab sequence.
  const onKeyDown = (e: React.KeyboardEvent<HTMLButtonElement>) => {
    const horizontal = ctx.orientation === "horizontal";
    const nextKey = horizontal ? "ArrowRight" : "ArrowDown";
    const prevKey = horizontal ? "ArrowLeft" : "ArrowUp";
    if (![nextKey, prevKey, "Home", "End"].includes(e.key)) return;
    const tablist = e.currentTarget.closest('[role="tablist"]');
    if (!tablist) return;
    const tabs = Array.from(
      tablist.querySelectorAll<HTMLButtonElement>('[role="tab"]:not([disabled])'),
    );
    const idx = tabs.indexOf(e.currentTarget);
    if (idx === -1) return;
    e.preventDefault();
    const next =
      e.key === "Home" ? 0
      : e.key === "End" ? tabs.length - 1
      : e.key === nextKey ? (idx + 1) % tabs.length
      : (idx - 1 + tabs.length) % tabs.length;
    const target = tabs[next];
    target.focus();
    const nextValue = target.getAttribute("data-tab-value");
    if (nextValue !== null) ctx.select(nextValue);
  };

  const indicator = active && ctx.variant !== "plain" ? (
    <motion.span
      layoutId={ctx.indicatorLayoutId}
      aria-hidden="true"
      data-tab-indicator={ctx.variant}
      transition={SPRING_LAYOUT}
      className={clsx(
        "absolute pointer-events-none",
        ctx.variant === "underline" && "inset-x-0 -bottom-px h-0.5 rounded-full bg-ink",
        ctx.variant === "segmented" &&
          (ctx.indicatorClassName ??
            "inset-0 rounded-full bg-surface-3 shadow-[var(--shadow-2)]"),
        ctx.variant === "expanding" &&
          (ctx.indicatorClassName ??
            "inset-0 rounded-full bg-surface-3 shadow-[var(--shadow-2)]"),
      )}
    />
  ) : null;

  return (
    <motion.button
      layout={ctx.variant === "expanding"}
      transition={SPRING_LAYOUT}
      type="button"
      role="tab"
      id={id}
      aria-label={ariaLabel}
      aria-selected={active}
      tabIndex={active ? 0 : -1}
      data-active={active ? "true" : undefined}
      data-tab-value={value}
      onClick={() => ctx.select(value)}
      onKeyDown={onKeyDown}
      className={clsx(
        "group relative",
        (ctx.variant === "segmented" || ctx.variant === "expanding") &&
          clsx(
            "inline-flex items-center justify-center gap-1.5 whitespace-nowrap rounded-full font-medium",
            "text-muted hover:text-ink data-[active]:text-ink",
            "[transition:color_var(--tabs-dur)_var(--tabs-ease)]",
            "outline-none focus-visible:ring-2 focus-visible:ring-accent",
            ctx.variant === "expanding"
              ? "h-7 min-w-7 flex-1 px-0 data-[active]:flex-none"
              : ITEM_SIZE[ctx.size],
          ),
        className,
      )}
    >
      {ctx.variant === "expanding" ? (
        <motion.span
          layout
          transition={SPRING_LAYOUT}
          className="relative inline-flex h-7 items-center justify-center gap-1.5 px-2.5"
        >
          {indicator}
          <span className="relative z-10 inline-flex items-center gap-1.5">{children}</span>
        </motion.span>
      ) : (
        <>
          {indicator}
          <span className="relative z-10">{children}</span>
        </>
      )}
    </motion.button>
  );
}
