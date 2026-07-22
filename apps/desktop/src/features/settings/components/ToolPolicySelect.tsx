import { CheckCircle, Prohibit, Question } from "@/components/icons";
import { AnimatePresence, motion } from "motion/react";
import type { ToolOverrideDecision } from "@/api/types";
import { Tab, Tabs } from "@/components/ui/Tabs";
import { EASE_OUT, MOTION } from "@/lib/tokens/motion";

export const TOOL_POLICY_DECISIONS = [
  { value: "approve", label: "Approve", Icon: CheckCircle },
  { value: "ask", label: "Ask", Icon: Question },
  { value: "deny", label: "Deny", Icon: Prohibit },
] as const;

/**
 * Approve / Ask / Deny segmented control for a tool's approval policy. The
 * builtin-tools tab and the MCP server tools section render different rows
 * around it (one has an enable switch, the other a policy badge), but the
 * policy selector itself is identical — so it lives here as one source.
 */
export function ToolPolicySelect({
  value,
  onChange,
}: {
  value: ToolOverrideDecision;
  onChange: (decision: ToolOverrideDecision) => void;
}) {
  return (
    <Tabs variant="expanding"
      size="sm"
      value={value}
      onChange={(v) => onChange(v as ToolOverrideDecision)}
      label="Tool approval policy"
      className="w-[180px]"
    >
      {TOOL_POLICY_DECISIONS.map((d) => {
        const active = d.value === value;
        return (
          <Tab
            key={d.value}
            value={d.value}
            aria-label={d.label}
            className="overflow-visible !text-[11px]"
          >
            <motion.span
              aria-hidden="true"
              animate={{ opacity: active ? 1 : 0.6 }}
              transition={{ duration: MOTION.fast, ease: EASE_OUT }}
              className="inline-flex shrink-0"
            >
              <d.Icon
                size={14}
                className="shrink-0"
              />
            </motion.span>
            <AnimatePresence initial={false}>
              {active && (
                <motion.span
                  key={d.label}
                  initial={{ opacity: 0, x: 4, filter: "blur(2px)" }}
                  animate={{ opacity: 1, x: 0, filter: "blur(0px)" }}
                  exit={{ opacity: 0, x: -3, filter: "blur(2px)" }}
                  transition={{ duration: MOTION.row, ease: EASE_OUT }}
                  className="whitespace-nowrap"
                >
                  {d.label}
                </motion.span>
              )}
            </AnimatePresence>
          </Tab>
        );
      })}
    </Tabs>
  );
}
