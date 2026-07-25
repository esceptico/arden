import {
  forwardRef,
  type ButtonHTMLAttributes,
  type ReactNode,
} from "react";
import clsx from "clsx";
import { type ArdenIcon } from "@/components/icons";

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
  leadingIcon?: ArdenIcon;
  trailingIcon?: ArdenIcon;
  /** Force the pressed/engaged look — e.g. while this button's menu is open. */
  active?: boolean;
  children?: ReactNode;
}

const BUTTON_ICON_PX = 16;

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
  return (
    <button
      ref={ref}
      type={type ?? "button"}
      data-variant={variant}
      data-size={size}
      data-active={active || undefined}
      data-leading={Leading ? "true" : undefined}
      data-trailing={Trailing ? "true" : undefined}
      className={clsx(
        "btn arden-button",
        variant === "primary" && "primary",
        variant === "quiet" && "quiet",
        variant === "danger" && "danger",
        className,
      )}
      {...rest}
    >
      {Leading && <Leading size={BUTTON_ICON_PX} className="shrink-0" />}
      {children}
      {Trailing && <Trailing size={BUTTON_ICON_PX} className="shrink-0" />}
    </button>
  );
});
