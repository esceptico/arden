import type { CSSProperties, ReactNode, Ref } from "react";
import clsx from "clsx";

export type BadgeTone = "neutral" | "accent" | "ok" | "warn" | "bad";
export type BadgeVariant = "filled" | "outline" | "ghost";

interface BadgeProps {
  children: ReactNode;
  tone?: BadgeTone;
  size?: "sm" | "md";
  shape?: "pill" | "rounded";
  /** Surface treatment. `filled` (default) = tone fill. `outline` =
   *  transparent bg + tone border only. `ghost` = tone text only, no chrome. */
  variant?: BadgeVariant;
  leading?: ReactNode;
  className?: string;
  style?: CSSProperties;
  title?: string;
  ref?: Ref<HTMLSpanElement>;
}

const toneFill: Record<BadgeTone, string> = {
  neutral: "bg-surface-sunken text-muted",
  accent: "bg-accent-soft text-accent-strong",
  ok: "bg-ok-soft text-ok",
  warn: "bg-warn-soft text-warn",
  bad: "bg-bad-soft text-bad",
};

// Outline variant: tone-tinted text on a tone border, no fill.
const toneText: Record<BadgeTone, string> = {
  neutral: "text-muted",
  accent: "text-accent-strong",
  ok: "text-ok",
  warn: "text-warn",
  bad: "text-bad",
};

const toneBorder: Record<BadgeTone, string> = {
  neutral: "border-line-soft",
  accent: "border-accent/15",
  ok: "border-ok/20",
  warn: "border-warn/20",
  bad: "border-bad/20",
};

const sizeClass = {
  sm: "px-1.5 h-[18px] text-2xs",
  md: "px-2 py-[3px] text-xs",
};

export function Badge({
  children,
  tone = "neutral",
  size = "sm",
  shape = "pill",
  variant,
  leading,
  className,
  style,
  title,
  ref,
}: BadgeProps) {
  const surface =
    variant === "outline"
      ? ["border", toneBorder[tone], toneText[tone]]
      : variant === "ghost"
        ? toneText[tone]
        : toneFill[tone];
  return (
    <span
      ref={ref}
      style={style}
      title={title}
      className={clsx(
        "inline-flex max-w-full shrink-0 justify-self-start items-center gap-1 font-medium tracking-[var(--tracking-badge)] whitespace-nowrap",
        shape === "pill" ? "rounded-[var(--r-control)]" : "rounded-md",
        sizeClass[size],
        surface,
        className,
      )}
    >
      {leading != null && <span className="inline-flex shrink-0">{leading}</span>}
      {children}
    </span>
  );
}
