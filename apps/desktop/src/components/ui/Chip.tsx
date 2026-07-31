import type { ButtonHTMLAttributes, ReactNode, Ref } from "react";
import clsx from "clsx";

export type ChipVariant = "ghost" | "filled";
export type ChipTone = "neutral" | "accent" | "ink";
export type ChipSize = "sm" | "md";

interface ChipProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, "children" | "type"> {
  children: ReactNode;
  active?: boolean;
  variant?: ChipVariant;
  tone?: ChipTone;
  size?: ChipSize;
  leading?: ReactNode;
  trailing?: ReactNode;
  ref?: Ref<HTMLButtonElement>;
}

const sizeClass: Record<ChipSize, string> = {
  sm: "h-7 px-2.5 text-[length:var(--text-label)] gap-1.5 rounded-[var(--r-control)]",
  md: "h-8 px-2.5 text-[length:var(--text-label)] gap-1.5 rounded-[var(--r-control)]",
};

function styleFor(variant: ChipVariant, tone: ChipTone, active: boolean): string {
  if (variant === "filled") {
    if (tone === "accent") return "bg-accent text-on-ink";
    if (tone === "ink") return "bg-ink text-on-ink";
    return "bg-surface text-ink border border-line-soft";
  }
  // ghost — keep a constant 1px border across states so toggling `active`
  // never reflows the chip (transparent until the neutral-active border).
  if (!active) {
    return "border border-transparent text-muted hover:bg-surface-soft hover:text-ink";
  }
  if (tone === "accent") return "border border-transparent bg-accent-soft text-accent-strong hover:bg-accent-soft/80";
  if (tone === "ink") return "border border-transparent bg-ink text-on-ink";
  return "bg-surface text-ink shadow-[var(--shadow-sm)] border border-line-soft";
}

export function Chip({
  children,
  active = false,
  variant = "ghost",
  tone = "neutral",
  size = "sm",
  leading,
  trailing,
  disabled,
  className,
  ref,
  ...rest
}: ChipProps) {
  const ariaPressed = rest["aria-pressed"] ?? active;
  return (
    <button
      ref={ref}
      type="button"
      disabled={disabled}
      aria-pressed={ariaPressed}
      {...rest}
      className={clsx(
        "inline-flex items-center tracking-[-0.005em] transition-[background-color,border-color,box-shadow,color,transform,scale] duration-check ease-out select-none active:scale-[0.97]",
        sizeClass[size],
        styleFor(variant, tone, active),
        disabled && "opacity-[0.45] cursor-not-allowed pointer-events-none active:scale-100",
        className,
      )}
    >
      {leading != null && <span className="inline-flex shrink-0">{leading}</span>}
      {children}
      {trailing != null && <span className="inline-flex shrink-0">{trailing}</span>}
    </button>
  );
}
