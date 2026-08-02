import type { ActivityItem, UiMessage } from "@/stores";
import { activityItemStatus } from "@/lib/agent";

/** What the composer's working strip says it is doing right now. */
export interface WorkingLabel {
  verb: string;
  /** The thing being acted on. Empty when only the verb is known. */
  target: string;
}

const GENERIC: WorkingLabel = { verb: "Working", target: "" };

/** Title for a single call: the model's own `_display_title` when it wrote
 *  one, else the server's grouping noun over the tool's target, else the
 *  bare tool name. `displayTitle` is absent on history reload and for
 *  uncategorized tools, so neither fallback is dead code. */
function labelForItem(item: ActivityItem): WorkingLabel {
  const title = item.displayTitle?.trim();
  if (title) {
    const [verb, ...rest] = title.split(" ");
    return rest.length > 0 ? { verb: verb!, target: rest.join(" ") } : { verb: title, target: "" };
  }
  const target = item.target?.trim() ?? "";
  const noun = item.noun?.trim();
  if (noun) return { verb: noun, target };
  const verb = item.displayName?.trim() || item.kind;
  // For uncategorized tools the projected target is the display name again
  // (bare, or with the call's args appended) — rendering both stutters:
  // "ReadSession ReadSession". The verb already names the tool; only a
  // target that says something new earns the second slot.
  if (target === verb || target.startsWith(`${verb}(`)) return { verb, target: "" };
  return { verb, target };
}

/** The strip reports the call that is *running*, never the last one that
 *  finished: between two tools the agent is thinking, and a strip still
 *  naming the completed read would be stating something untrue. With nothing
 *  ongoing — before the first tool, or in the gap between two — it falls back
 *  to the generic label, which is exactly the no-signal window the strip
 *  exists to cover.
 *
 *  Only top-level calls are eligible. A workflow or subagent runs its own
 *  tools at depth ≥ 1, and surfacing those puts a nested agent's private step
 *  on the composer — the user sees "Tracing …" from somewhere inside a
 *  workflow instead of the workflow itself. The trace panel is where that
 *  detail belongs; the strip answers what the agent you are talking to is
 *  doing. */
export function workingLabel(activityMessage: UiMessage | null | undefined): WorkingLabel {
  const items = activityMessage?.activity?.items;
  if (!items?.length) return GENERIC;
  for (let index = items.length - 1; index >= 0; index -= 1) {
    const item = items[index]!;
    if ((item.depth ?? 0) > 0) continue;
    if (activityItemStatus(item) === "ongoing") return labelForItem(item);
  }
  return GENERIC;
}
