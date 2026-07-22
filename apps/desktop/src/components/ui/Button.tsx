import {
  forwardRef,
  type ButtonHTMLAttributes,
  type ReactNode,
} from "react";
import clsx from "clsx";
import { type NtrpIcon } from "@/components/icons";

/**
 * Text button primitive — sibling to {@link IconButton}. Collapses the
 * recurring inlined `<button>` patterns (primary / secondary / ghost) onto
 * one component so every action button shares the same height, radius,
 * motion, and disabled treatment. For icon-only controls use IconButton.
 *
 * Variants reproduce the existing hand-rolled classes 1:1, so swapping an
 * inlined button for `<Button>` is visually a no-op.
 *   primary   — solid ink slab (the main CTA: "New", "Save & reconnect")
 *   secondary — bordered, quiet fill on hover (neutral secondary action)
 *   ghost     — text-only, tints on hover (low-emphasis / inline action)
 *   danger    — destructive text, bad-tinted hover
 */
type ButtonVariant = "primary" | "secondary" | "ghost" | "quiet" | "danger";
type ButtonSize = "sm" | "md";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  /** sm = h-7 (dense toolbars/rows), md = h-8 (default, forms/modals). */
  size?: ButtonSize;
  leadingIcon?: NtrpIcon;
  trailingIcon?: NtrpIcon;
  /** Force the pressed/engaged look — e.g. while this button's menu is open. */
  active?: boolean;
  children?: ReactNode;
}

const BASE =
  "inline-flex items-center justify-center gap-1.5 rounded-md font-medium tracking-[-0.005em] " +
  "transition-[background-color,border-color,color,opacity,scale] duration-check ease-out " +
  "active:scale-[0.97] disabled:opacity-[0.45] disabled:cursor-not-allowed disabled:active:scale-100";

const SIZE: Record<ButtonSize, string> = {
  sm: "h-7 text-sm",
  md: "h-8 text-sm",
};

// Optical alignment (make-interfaces-feel-better): a button with text on one
// side and an icon on the other gets ~2px LESS padding on the icon side, so the
// glyph doesn't read as pushed toward the edge. Symmetric when there's no icon
// (or an icon on both sides). Static class names so Tailwind generates them.
const PAD: Record<ButtonSize, { sym: string; lead: string; trail: string }> = {
  sm: { sym: "px-2.5", lead: "pl-2 pr-2.5", trail: "pl-2.5 pr-2" },
  md: { sym: "px-3", lead: "pl-2.5 pr-3", trail: "pl-3 pr-2.5" },
};

const ICON_PX: Record<ButtonSize, number> = { sm: 14, md: 16 };

const VARIANT: Record<ButtonVariant, string> = {
  primary: "bg-ink text-on-ink hover:opacity-90",
  secondary:
    "border border-line-soft text-ink-soft hover:bg-surface-soft hover:border-line-strong",
  // ghost: text-only with a subtle hover fill (toolbar/menu actions).
  ghost: "text-muted hover:text-ink hover:bg-surface-soft",
  // quiet: text-only, NO hover fill (inline text actions — colour shift only).
  quiet: "text-muted hover:text-ink",
  danger: "text-bad hover:bg-bad-soft",
};

const ACTIVE: Record<ButtonVariant, string> = {
  primary: "opacity-90",
  secondary: "bg-surface-soft border-line-strong",
  ghost: "text-ink bg-surface-soft",
  quiet: "text-ink",
  danger: "bg-bad-soft",
};

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  {
    variant = "primary",
    size = "md",
    leadingIcon: Leading,
    trailingIcon: Trailing,
    active = false,
    type,
    className,
    children,
    ...rest
  },
  ref,
) {
  const px = ICON_PX[size];
  const pad =
    Leading && !Trailing
      ? PAD[size].lead
      : Trailing && !Leading
        ? PAD[size].trail
        : PAD[size].sym;
  return (
    <button
      ref={ref}
      type={type ?? "button"}
      data-active={active || undefined}
      className={clsx(BASE, SIZE[size], pad, VARIANT[variant], active && ACTIVE[variant], className)}
      {...rest}
    >
      {Leading && <Leading size={px} strokeWidth={2} className="shrink-0" />}
      {children}
      {Trailing && <Trailing size={px} strokeWidth={2} className="shrink-0" />}
    </button>
  );
});
