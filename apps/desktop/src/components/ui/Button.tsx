import {
  forwardRef,
  type ButtonHTMLAttributes,
  type ReactNode,
} from "react";
import clsx from "clsx";
import { type ArdenIcon } from "@/components/icons";

/**
 * Text button primitive — sibling to {@link IconButton}. Collapses the
 * recurring inlined `<button>` patterns onto one component so every action
 * button shares the same height, radius, motion, and disabled treatment.
 * For icon-only controls use IconButton.
 *
 * Fill policy (see docs/design-language.md → Buttons): the ink slab is
 * RATIONED — at most one per visible surface, and only on that surface's
 * commit action (Send, Save, Approve). Everything else takes the unmarked
 * default. That is why `secondary` is the default variant: a bare <Button>
 * can never accidentally out-shout the commit.
 *   primary   — solid ink slab; the surface's single commit action
 *   secondary — raised paper (default); the workhorse for everything else
 *   quiet     — tonal, no shadow; dismissals and third-rank actions
 *   ghost     — text-only, tints on hover (inline/low-emphasis)
 *   danger    — destructive text, bad-tinted hover; never the default action
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
    variant = "secondary",
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
