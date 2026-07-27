import type { ToolOverrideDecision } from "@/api/types";
import { Tab, Tabs } from "@/components/ui/Tabs";

export const TOOL_POLICY_DECISIONS = [
  { value: "approve", label: "Approve" },
  { value: "ask", label: "Ask" },
  { value: "deny", label: "Deny" },
] as const;

/**
 * Approve / Ask / Deny, in words. This was three icon-only glyphs — a tick, a
 * question mark and a slash in a capsule — carrying no visible label at all,
 * so a permission decision had to be decoded from shapes. That is the thing
 * the app refuses to do everywhere else: state is words in status ink. The
 * labels cost the row ~70px, out of a description column with 317 to give.
 */
export function ToolPolicySelect({
  value,
  onChange,
}: {
  value: ToolOverrideDecision;
  onChange: (decision: ToolOverrideDecision) => void;
}) {
  return (
    <Tabs
      variant="segmented"
      size="sm"
      value={value}
      onChange={(v) => onChange(v as ToolOverrideDecision)}
      label="Tool approval policy"
      className="settings-tool-policy"
    >
      {TOOL_POLICY_DECISIONS.map((decision) => (
        <Tab key={decision.value} value={decision.value}>
          {decision.label}
        </Tab>
      ))}
    </Tabs>
  );
}
