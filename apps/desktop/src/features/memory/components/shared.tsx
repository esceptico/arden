import type { ButtonHTMLAttributes, ReactNode } from "react";
import { Button } from "@/components/ui/Button";

// ─── Display helpers ──────────────────────────────────────────────────

// ─── Buttons ──────────────────────────────────────────────────────────

export function GhostBtn({
  children,
  onClick,
  disabled,
  ...buttonProps
}: {
  children: ReactNode;
  onClick: () => void;
  disabled?: boolean;
} & Omit<ButtonHTMLAttributes<HTMLButtonElement>, "type" | "onClick" | "disabled" | "className">) {
  return (
    <Button {...buttonProps} variant="ghost" size="sm" onClick={onClick} disabled={disabled}>
      {children}
    </Button>
  );
}
