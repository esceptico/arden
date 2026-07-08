import { motion } from "motion/react";
import type { SliceAsk } from "@/api/slices";
import type { Automation } from "@/api/types";
import { useStore } from "@/stores";
import { runAutomation } from "@/actions/automations";
import { switchSession } from "@/actions/sessions";
import { fetchSliceDetail, resolveAsk } from "@/actions/slices";
import { primaryActionFor } from "@/lib/askActions";
import { IconButton } from "@/components/ui/IconButton";
import { Button } from "@/components/ui/Button";
import { ASK_KIND } from "@/lib/sliceKind";
import { RISE_IN, RISE_SETTLED, ROW_EXIT, SPRING_ROW_ENTRY, MOTION, EASE_OUT } from "@/lib/tokens/motion";
import { X } from "lucide-react";

/** Agent asks read as "headline — elaboration"; split on the first em-dash
 *  so the card can render the mock's title/description hierarchy. Asks
 *  without one are all title. The detail is capitalized: split on the dash
 *  leaves a lowercase continuation ("…Friday" / "decide which…") that reads
 *  as a broken fragment, so promote it to its own sentence. */
function splitAsk(text: string): { title: string; detail: string | null } {
  const i = text.indexOf(" — ");
  if (i === -1) return { title: text, detail: null };
  const detail = text.slice(i + 3);
  return { title: text.slice(0, i), detail: detail.charAt(0).toUpperCase() + detail.slice(1) };
}

/** Attention card for a slice's top ask: severity dot, title/detail, a
 *  buttons row (primary action + Discuss), dismiss ✕ in the corner.
 *  Discuss hands the ask to the room's scoped composer via `onDiscuss`.
 *  Retires with ROW_EXIT; list membership is the caller's AnimatePresence.
 *
 *  `open_page` actions route to `openSlice(ask.slice_key)` — a no-op
 *  inside the ask's own room, so the primary button is suppressed there
 *  and Discuss carries the card. */
export function AskCard({ ask, onDiscuss }: { ask: SliceAsk; onDiscuss?: (ask: SliceAsk) => void }) {
  const automations = useStore((s) => s.automations);
  const { title, detail } = splitAsk(ask.text);

  const isNoOpOpenPage = ask.actions[0]?.verb === "open_page";
  const primaryAction = isNoOpOpenPage
    ? null
    : primaryActionFor(ask, automations as Automation[] | null, {
        switchSession: (id) => void switchSession(id),
        runAutomation: (taskId) => void runAutomation(taskId),
        openSlice: (key) => useStore.getState().openSlice(key),
      });

  const dismiss = async () => {
    try {
      await resolveAsk(ask.slice_key, ask.id, "dismissed");
    } catch {
      useStore.getState().pushToast({
        id: `ask-dismiss-fail:${ask.slice_key}:${ask.id}`,
        title: "Couldn’t dismiss",
        status: "failed",
        target: { kind: "automation" },
      });
      await fetchSliceDetail(ask.slice_key);
    }
  };

  return (
    <motion.div
      layout
      initial={RISE_IN}
      animate={RISE_SETTLED}
      exit={{ ...ROW_EXIT, transition: { duration: MOTION.row, ease: EASE_OUT } }}
      transition={SPRING_ROW_ENTRY}
      className="grid min-w-0 gap-1.5 rounded-xl bg-surface-soft px-4 py-3.5"
    >
      {/* Eyebrow: the kind is a text LABEL, not an action; dismiss at the
          far end. Keeps the kind out of the button position where it read
          as a control. */}
      <div className="flex min-w-0 items-center gap-2">
        <span className="text-2xs font-medium uppercase tracking-wide text-faint">
          {ASK_KIND[ask.kind].label}
        </span>
        <IconButton size="sm" title="Dismiss" onClick={() => void dismiss()} className="-my-1 ml-auto">
          <X className="size-3.5" />
        </IconButton>
      </div>
      <div className="min-w-0">
        <p className="m-0 text-base font-medium leading-snug text-ink [overflow-wrap:anywhere]">{title}</p>
        {detail && <p className="m-0 mt-1 text-sm leading-snug text-ink-soft [overflow-wrap:anywhere]">{detail}</p>}
      </div>
      <div className="mt-2 flex items-center gap-2">
        {primaryAction && (
          <Button variant="primary" size="sm" onClick={primaryAction.run}>
            {primaryAction.label}
          </Button>
        )}
        {onDiscuss && (
          <Button variant={primaryAction ? "ghost" : "primary"} size="sm" onClick={() => onDiscuss(ask)}>
            Discuss
          </Button>
        )}
      </div>
    </motion.div>
  );
}
