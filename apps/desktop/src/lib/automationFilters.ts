import type { Automation } from "@/api/types";

const INTERNAL_HANDLERS = new Set(["knowledge_reflection", "knowledge_retention", "knowledge_health"]);

export function isInternalAutomation(automation: Automation): boolean {
  return automation.builtin || (automation.handler != null && INTERNAL_HANDLERS.has(automation.handler));
}

/** Post-mode loops are still automations, but their activity lands in a
 *  channel session. They stay in Active with a channel badge/link. */
export function isChannelAutomation(automation: Automation): boolean {
  return automation.kind === "loop" && automation.read_history === false;
}

export function isIterationLoop(automation: Automation): boolean {
  return automation.kind === "loop" && automation.read_history !== false;
}
