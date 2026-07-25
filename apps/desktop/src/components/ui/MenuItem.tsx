import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from "react";
import clsx from "clsx";

/**
 * One row in a portaled menu / popover (SessionContextMenu, SidebarFilters).
 * Owns the shared visual chassis — full-width row, a fixed leading slot for an
 * icon or selection check, a truncating label, and the menu-row interaction
 * (background highlight on hover/focus + press scale). It deliberately does NOT
 * set role/tabIndex: a roving-tabindex menu and a plain focusable popover have
 * different keyboard models, so callers pass those through `...rest`.
 */
interface MenuItemProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  /** Leading 3.5×3.5 slot content — an icon or a selection check. The caller
   *  owns the glyph and its colour (faint icon vs accent check). */
  leading?: ReactNode;
  /** Tighter vertical padding (py-1) for long option lists; default py-1.5. */
  dense?: boolean;
  /** Board context-menu row: the compact 28px no-icon menu contract. */
  context?: boolean;
  /** A two-line / custom-content row. It removes the empty icon gutter and
   * the single-line truncating wrapper while preserving the shared chassis. */
  rich?: boolean;
  /** Optional trailing affordance (for example a keyboard shortcut). */
  trailing?: ReactNode;
  children: ReactNode;
}

export const MenuItem = forwardRef<HTMLButtonElement, MenuItemProps>(function MenuItem(
  { leading, dense, context = false, rich = false, trailing, type, className, children, ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type ?? "button"}
      className={clsx(
        context
          ? "arden-context-menu__item"
          : [
            "w-full flex items-center gap-2 px-2.5 text-left text-sm text-ink-soft",
            "hover:bg-fill-hover hover:text-ink focus-visible:bg-fill-hover focus-visible:text-ink focus-visible:outline-none",
            "transition-[background-color,color,scale] duration-check ease-out active:scale-[0.98]",
            dense ? "py-1" : "py-1.5",
          ],
        className,
      )}
      {...rest}
    >
      {leading != null || (!context && !rich) ? <span className="grid place-items-center w-3.5 h-3.5 shrink-0">{leading}</span> : null}
      {rich ? children : <span className={clsx("truncate", context && "min-w-0 flex-1")}>{children}</span>}
      {trailing}
    </button>
  );
});
