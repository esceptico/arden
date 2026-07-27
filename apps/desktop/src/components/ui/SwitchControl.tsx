import { type Ref } from "react";
import clsx from "clsx";

/** One geometry, 32 × 18. There was a `size` prop stamping `data-size`, but no
 *  stylesheet ever matched it — four call sites asked for "sm" and rendered
 *  identically to the rest. Removed rather than implemented: giving it real
 *  dimensions would have shrunk four shipped surfaces at once. */
interface SwitchControlProps {
  checked: boolean;
  onChange: (next: boolean) => void;
  disabled?: boolean;
  className?: string;
  "aria-label"?: string;
  ref?: Ref<HTMLButtonElement>;
}

export function SwitchControl({
  checked,
  onChange,
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
    >
      <span aria-hidden className="switch-control-knob" />
    </button>
  );
}

export default SwitchControl;
