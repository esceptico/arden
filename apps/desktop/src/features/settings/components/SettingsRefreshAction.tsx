import { useEffect, useRef, useState } from "react";
import { useReducedMotion } from "motion/react";
import { Check, RefreshCw } from "@/components/icons";
import { BlurSwap } from "@/components/ui/BlurSwap";
import { Button } from "@/components/ui/Button";
import { DynamicIconSwap } from "@/components/ui/IconSwap";
import { ICON } from "@/lib/icons";
import { MOTION } from "@/lib/tokens/motion";

type RefreshPhase = "idle" | "refreshing" | "done";

const wait = (milliseconds: number) =>
  new Promise<void>((resolve) => window.setTimeout(resolve, milliseconds));

/** Shared Settings refresh choreography from the redesign: label collapses,
 * the retained refresh glyph centers and spins, then check + Done hold before
 * both bridge back. */
export function SettingsRefreshAction({
  label,
  loading,
  onRefresh,
}: {
  label: string;
  loading: boolean;
  onRefresh: () => Promise<void>;
}) {
  const [phase, setPhase] = useState<RefreshPhase>("idle");
  const generation = useRef(0);
  const reducedMotion = useReducedMotion() ?? false;

  useEffect(() => () => {
    generation.current += 1;
  }, []);

  const run = async () => {
    if (loading || phase !== "idle") return;
    const request = ++generation.current;
    setPhase("refreshing");
    await onRefresh();
    if (generation.current !== request) return;
    setPhase("done");
    if (!reducedMotion) {
      await wait((MOTION.textSwap * 2 + MOTION.refreshDoneHold) * 1000);
    }
    if (generation.current !== request) return;
    setPhase("idle");
  };

  const done = phase === "done";
  const text = phase === "refreshing" ? "" : done ? "Done" : "Refresh";

  return (
    <Button
      variant="secondary"
      className="settings-refresh-action"
      data-phase={phase}
      disabled={loading || phase !== "idle"}
      aria-busy={phase === "refreshing" || undefined}
      aria-label={
        phase === "refreshing"
          ? `Refreshing ${label.toLowerCase()}`
          : done
            ? `${label} refreshed`
            : `Refresh ${label.toLowerCase()}`
      }
      onClick={() => void run()}
    >
      <span className="settings-refresh-content" aria-hidden>
        <DynamicIconSwap swapKey={done ? "done" : "refresh"} className="settings-refresh-glyph">
          {done ? <Check size={ICON.SM} /> : <RefreshCw size={ICON.SM} />}
        </DynamicIconSwap>
        <span className="settings-refresh-label-slot">
          <BlurSwap swapKey={text} className="settings-refresh-label">{text}</BlurSwap>
        </span>
      </span>
    </Button>
  );
}
