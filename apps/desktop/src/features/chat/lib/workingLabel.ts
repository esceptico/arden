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
  return { verb: item.displayName?.trim() || item.kind, target };
}

/** The strip reports the call that is *running*, never the last one that
 *  finished: between two tools the agent is thinking, and a strip still
 *  naming the completed read would be stating something untrue. With nothing
 *  ongoing — before the first tool, or in the gap between two — it falls back
 *  to the generic label, which is exactly the no-signal window the strip
 *  exists to cover. */
export function workingLabel(activityMessage: UiMessage | null | undefined): WorkingLabel {
  const items = activityMessage?.activity?.items;
  if (!items?.length) return GENERIC;
  for (let index = items.length - 1; index >= 0; index -= 1) {
    const item = items[index]!;
    if (activityItemStatus(item) === "ongoing") return labelForItem(item);
  }
  return GENERIC;
}
