import { motion } from "motion/react";
import type { AreaAsk } from "@/api/areas";
import type { Automation } from "@/api/types";
import { useStore } from "@/stores";
import { runAutomation } from "@/actions/automations";
import { switchSession } from "@/actions/sessions";
import { fetchAreaDetail, fewerLikeThis, resolveAsk } from "@/actions/areas";
import { primaryActionFor } from "@/lib/askActions";
import { IconButton } from "@/components/ui/IconButton";
import { Button } from "@/components/ui/Button";
import { Tooltip } from "@/components/ui/Tooltip";
import { ASK_KIND } from "@/lib/areaKind";
import { RISE_IN, RISE_SETTLED, ROW_EXIT, SPRING_ROW_ENTRY, MOTION, EASE_OUT } from "@/lib/tokens/motion";
import { X } from "@/components/icons";

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

/** Attention card for an area's ask. The buttons follow the ask's kind
 *  (three-verb taxonomy) so every ask presents exactly the affordances it
 *  can answer with:
 *    notify   — Got it (done). An FYI is cleared, not decided.
 *    question — Reply (hands the ask to the room's composer) / Dismiss.
 *    review   — Approve (done) / Reject (dismissed) / Discuss.
 *  The why-now and what-happens-next lines make the ask answerable instead
 *  of rubber-stampable; "fewer like this" steers the agent's standing
 *  instructions from the card itself.
 *
 *  `open_page` actions route to `openArea(ask.area_key)` — a no-op
 *  inside the ask's own room, so the primary button is suppressed there. */
export function AskCard({ ask, onDiscuss }: { ask: AreaAsk; onDiscuss?: (ask: AreaAsk) => void }) {
  const automations = useStore((s) => s.automations);
  const { title, detail } = splitAsk(ask.text);

  const isNoOpOpenPage = ask.actions[0]?.verb === "open_page";
  const primaryAction = isNoOpOpenPage
    ? null
    : primaryActionFor(ask, automations as Automation[] | null, {
        switchSession: (id) => void switchSession(id),
        runAutomation: (taskId) => void runAutomation(taskId),
        openArea: (key) => useStore.getState().openArea(key),
      });

  const settle = async (state: "done" | "dismissed", resolution: string, failTitle: string) => {
    try {
      await resolveAsk(ask.area_key, ask.id, state, undefined, resolution);
    } catch {
      useStore.getState().pushToast({
        id: `ask-resolve-fail:${ask.area_key}:${ask.id}`,
        title: failTitle,
        status: "failed",
        target: { kind: "automation" },
      });
      await fetchAreaDetail(ask.area_key);
    }
  };

  const steer = async () => {
    try {
      await fewerLikeThis(ask);
    } catch {
      useStore.getState().pushToast({
        id: `ask-steer-fail:${ask.area_key}:${ask.id}`,
        title: "Couldn’t update instructions",
        status: "failed",
        target: { kind: "automation" },
      });
    }
  };

  return (
    <motion.div
      initial={RISE_IN}
      animate={RISE_SETTLED}
      exit={{ ...ROW_EXIT, transition: { duration: MOTION.row, ease: EASE_OUT } }}
      transition={SPRING_ROW_ENTRY}
      className="grid min-w-0 gap-1.5 rounded-xl bg-surface-soft px-4 py-3.5"
    >
      {/* Eyebrow: the kind is a text LABEL, not an action; dismiss at the
          far end. */}
      <div className="flex min-w-0 items-center gap-2">
        <span className="text-2xs font-medium uppercase tracking-wide text-faint">
          {ASK_KIND[ask.kind].label}
        </span>
        <IconButton
          size="sm"
          title="Dismiss"
          onClick={() => void settle("dismissed", "dismissed", "Couldn’t dismiss")}
          className="-my-1 ml-auto"
        >
          <X className="size-3.5" />
        </IconButton>
      </div>
      <div className="min-w-0">
        <p className="m-0 text-base font-medium leading-snug text-ink [overflow-wrap:anywhere]">{title}</p>
        {detail && <p className="m-0 mt-1 text-sm leading-snug text-ink-soft [overflow-wrap:anywhere]">{detail}</p>}
        {(ask.why_now || ask.what_next) && (
          <div className="mt-1.5 grid gap-0.5">
            {ask.why_now && (
              <p className="m-0 text-xs leading-snug text-muted [overflow-wrap:anywhere]">
                <span className="text-faint">Why now:</span> {ask.why_now}
              </p>
            )}
            {ask.what_next && (
              <p className="m-0 text-xs leading-snug text-muted [overflow-wrap:anywhere]">
                <span className="text-faint">Next:</span> {ask.what_next}
              </p>
            )}
          </div>
        )}
      </div>
      <div className="mt-2 flex items-center gap-2">
        {ask.kind === "review" && (
          <>
            <Button variant="primary" size="sm" onClick={() => void settle("done", "approved", "Couldn’t approve")}>
              Approve
            </Button>
            <Button variant="ghost" size="sm" onClick={() => void settle("dismissed", "rejected", "Couldn’t reject")}>
              Reject
            </Button>
          </>
        )}
        {ask.kind === "question" && onDiscuss && (
          <Button variant="primary" size="sm" onClick={() => onDiscuss(ask)}>
            Reply
          </Button>
        )}
        {ask.kind === "notify" && (
          <Button variant="primary" size="sm" onClick={() => void settle("done", "acknowledged", "Couldn’t clear")}>
            Got it
          </Button>
        )}
        {primaryAction && (
          <Button variant="ghost" size="sm" onClick={primaryAction.run}>
            {primaryAction.label}
          </Button>
        )}
        {ask.kind !== "question" && onDiscuss && (
          <Button variant="ghost" size="sm" onClick={() => onDiscuss(ask)}>
            Discuss
          </Button>
        )}
        {ask.source === "agent" && (
          <Tooltip label="Dismisses this and adds a standing instruction so the agent raises fewer asks like it">
            <button
              type="button"
              onClick={() => void steer()}
              className="ml-auto text-2xs text-whisper transition-colors duration-check ease-out hover:text-muted"
            >
              Fewer like this
            </button>
          </Tooltip>
        )}
      </div>
    </motion.div>
  );
}
