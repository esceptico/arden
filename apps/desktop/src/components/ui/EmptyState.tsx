import type { ComponentType, ReactNode } from "react";
import { Inbox } from "@/components/icons";
import clsx from "clsx";

type EmptyStateProps = {
  icon?: ComponentType<{ size?: number | string; strokeWidth?: number; className?: string }>;
  children: ReactNode;
  hint?: ReactNode;
  action?: ReactNode;
  /** sm = 36px chip (compact sidebars), md = 48px chip (default modals/panes). */
  size?: "sm" | "md";
  className?: string;
};

/** Designed empty/placeholder state: icon chip + conversational copy + an
 *  optional next action. Shared by both list and detail panes. */
export function EmptyState({ icon: Icon = Inbox, children, hint, action, size = "md", className }: EmptyStateProps) {
  const sm = size === "sm";
  return (
    <div className={clsx("grid place-items-center px-6 text-center", className)}>
      <div className={clsx("flex max-w-[260px] flex-col items-center", sm ? "gap-2.5" : "gap-3")}>
        <div className={clsx("grid place-items-center rounded-[var(--r-house)] bg-surface-soft text-faint", sm ? "size-9" : "size-12")}>
          <Icon size={sm ? 16 : 22} strokeWidth={1.75} />
        </div>
        <div className="text-sm text-muted">{children}</div>
        {hint && <div className="text-xs text-muted">{hint}</div>}
        {action && <div className="mt-1">{action}</div>}
      </div>
    </div>
  );
}

export function DetailPlaceholder({
  children,
  icon,
  hint,
  action,
}: {
  children: ReactNode;
  icon?: EmptyStateProps["icon"];
  hint?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <EmptyState icon={icon} hint={hint} action={action} className="h-full">
      {children}
    </EmptyState>
  );
}

export function Empty({
  children,
  icon,
  hint,
  action,
}: {
  children: ReactNode;
  icon?: EmptyStateProps["icon"];
  hint?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <EmptyState icon={icon} hint={hint} action={action} className="min-h-[200px]">
      {children}
    </EmptyState>
  );
}
