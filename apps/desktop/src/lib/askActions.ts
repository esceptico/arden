import type { AreaAsk } from "@/api/areas";
import type { Automation } from "@/api/types";

/** Primary action derived from an ask's first action verb. `run` is a
 *  closure the caller invokes (no ask/verb leaks into the button). Unknown
 *  verbs, and `retry` against an automation name that no longer resolves to
 *  a task_id, return null — no primary button (forward-compatible,
 *  no crash on stale data). */
export interface AskPrimaryAction {
  label: string;
  run: () => void;
}

interface AskActionHandlers {
  switchSession: (sessionId: string) => void;
  runAutomation: (taskId: string) => void;
  openArea: (areaKey: string) => void;
}

/** Maps an ask's declared verb (server contract: arden/areas/service.py +
 *  agent.py) to a primary action. `retry`'s ref is an automation NAME, not a
 *  task_id — the client run API takes task_id, so we resolve it against the
 *  live automations list. */
export function primaryActionFor(
  ask: AreaAsk,
  automations: Automation[] | null,
  handlers: AskActionHandlers,
): AskPrimaryAction | null {
  const action = ask.actions[0];
  if (!action) return null;

  switch (action.verb) {
    case "open_session":
      return { label: "Open", run: () => handlers.switchSession(action.ref) };
    case "retry": {
      const taskId = (automations ?? []).find((a) => a.name === action.ref)?.task_id;
      if (!taskId) return null;
      return { label: "Retry", run: () => handlers.runAutomation(taskId) };
    }
    case "open_page":
      return { label: "Review", run: () => handlers.openArea(ask.area_key) };
    default:
      return null;
  }
}
