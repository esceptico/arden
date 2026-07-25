import { CheckCircle, Prohibit, Question } from "@/components/icons";
import type { ToolOverrideDecision } from "@/api/types";
import { Tab, Tabs } from "@/components/ui/Tabs";

export const TOOL_POLICY_DECISIONS = [
  { value: "approve", label: "Approve", Icon: CheckCircle },
  { value: "ask", label: "Ask", Icon: Question },
  { value: "deny", label: "Deny", Icon: Prohibit },
] as const;

/**
 * Approve / Ask / Deny keeps three fixed icon targets. The surrounding rows
 * can adapt at narrow widths without the policy control changing its density.
 */
export function ToolPolicySelect({
  value,
  onChange,
}: {
  value: ToolOverrideDecision;
  onChange: (decision: ToolOverrideDecision) => void;
}) {
  return (
    <Tabs variant="segmented"
      size="sm"
      value={value}
      onChange={(v) => onChange(v as ToolOverrideDecision)}
      label="Tool approval policy"
      className="settings-tool-policy"
    >
      {TOOL_POLICY_DECISIONS.map((d) => {
        return (
          <Tab
            key={d.value}
            value={d.value}
            aria-label={d.label}
            className="!size-7 !min-w-7 !p-0"
          >
            <d.Icon size={16} className="shrink-0" />
          </Tab>
        );
      })}
    </Tabs>
  );
}
