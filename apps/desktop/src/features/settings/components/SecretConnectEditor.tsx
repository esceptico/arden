import { type ReactNode } from "react";
import { motion } from "motion/react";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { DISSOLVE_OUT, EASE_OUT, MOTION, RISE_IN, RISE_SETTLED } from "@/lib/tokens/motion";

/**
 * Animated inline secret editor: a password field + Connect/Cancel buttons that
 * rises into a card's body when the row enters its editing state. Shared by the
 * model-provider API-key row and the Slack token row — same markup, motion, and
 * a11y; only the field label, the Connect spinner, and the inset padding differ.
 */
export function SecretConnectEditor({
  value,
  label,
  pending,
  motionKey,
  active = true,
  spinner,
  paddingX = "px-3.5",
  onChange,
  onConnect,
  onCancel,
}: {
  value: string;
  label: string;
  pending: boolean;
  motionKey: string;
  active?: boolean;
  spinner: ReactNode;
  paddingX?: string;
  onChange: (value: string) => void;
  onConnect: () => void;
  onCancel: () => void;
}) {
  return (
    <motion.div
      key={motionKey}
      initial={{ ...RISE_IN, y: -4 }}
      animate={RISE_SETTLED}
      exit={{ ...DISSOLVE_OUT, transition: { duration: MOTION.fast, ease: EASE_OUT } }}
      transition={{ duration: MOTION.row, ease: EASE_OUT }}
      className={`grid grid-cols-[minmax(0,1fr)_auto_auto] gap-2 ${paddingX} py-3 bg-surface-soft/35`}
    >
      <Input
        type="password"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={label}
        aria-label={label}
        autoFocus={active}
        spellCheck={false}
        autoComplete="off"
      />
      <Button variant="primary" onClick={onConnect} disabled={!value.trim() || pending}>
        {pending && spinner}
        Connect
      </Button>
      <Button onClick={onCancel} disabled={pending}>
        Cancel
      </Button>
    </motion.div>
  );
}
