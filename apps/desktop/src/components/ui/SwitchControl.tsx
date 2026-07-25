import { type Ref } from "react";
import clsx from "clsx";

interface SwitchControlProps {
  checked: boolean;
  onChange: (next: boolean) => void;
  size?: "sm" | "md";
  disabled?: boolean;
  className?: string;
  "aria-label"?: string;
  ref?: Ref<HTMLButtonElement>;
}

export function SwitchControl({
  checked,
  onChange,
  size = "md",
  disabled = false,
  className,
  "aria-label": ariaLabel,
  ref,
}: SwitchControlProps) {
  return (
    <button
      ref={ref}
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={ariaLabel}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={clsx("switch-control", checked && "switch-control-on", className)}
      data-size={size}
    >
      <span aria-hidden className="switch-control-knob" />
    </button>
  );
}

export default SwitchControl;
