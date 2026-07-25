import { useEffect, useMemo, useState } from "react";
import type { AreaAsk } from "@/api/areas";
import type { Automation } from "@/api/types";
import { fetchAreaDetail, resolveAsk } from "@/actions/areas";
import { runAutomation } from "@/actions/automations";
import { switchSession } from "@/actions/sessions";
import { BlurSwap } from "@/components/ui/BlurSwap";
import { Button } from "@/components/ui/Button";
import { ASK_KIND, splitAreaAsk } from "@/lib/areaKind";
import { primaryActionFor } from "@/lib/askActions";
import { formatRelativePastAgo } from "@/lib/format";
import { useStore } from "@/stores";

type AreaRequestDeckProps = {
  asks: readonly AreaAsk[];
  onDiscuss: (ask: AreaAsk) => void;
};

/**
 * One real ask is in focus; the rest stay as compact, directly promotable
 * rows. This is deliberately not a stack of approval cards: the room asks
 * for one decision at a time while preserving the complete Area ask queue.
 */
export function AreaRequestDeck({ asks, onDiscuss }: AreaRequestDeckProps) {
  const askIds = useMemo(() => asks.map((ask) => ask.id), [asks]);
  const [activeId, setActiveId] = useState<string | null>(() => askIds[0] ?? null);

  // Resolution, refresh, and live events can remove or reorder asks. Keep a
  // surviving focused ask; otherwise promote the first current ask.
  useEffect(() => {
    setActiveId((current) => current && askIds.includes(current) ? current : askIds[0] ?? null);
  }, [askIds]);

  const activeIndex = Math.max(0, asks.findIndex((ask) => ask.id === activeId));
  const active = asks[activeIndex] ?? null;
  const peers = active ? asks.filter((ask) => ask.id !== active.id) : [];

  if (!active) {
    return (
      <section className="board-area-deck" aria-label="Needs you" data-area-request-deck>
        <p className="board-area-deck__zero">All requests handled</p>
      </section>
    );
  }

  return (
    <section className="board-area-deck" aria-label="Needs you" data-area-request-deck>
      <div className="board-area-deck__slot">
        <div className="board-area-deck__active">
          <AreaRequestCard ask={active} onDiscuss={onDiscuss} />
        </div>
        <div className="board-area-deck__chrome" aria-live="polite">
          <BlurSwap swapKey={`${active.id}:${activeIndex}`}>
            <span>{activeIndex + 1} of {asks.length}</span>
          </BlurSwap>
        </div>
      </div>

      {peers.length > 0 && (
        <div className="board-area-deck__rest" aria-label="Other requests">
          {peers.map((ask) => (
            <button
              key={ask.id}
              type="button"
              data-area-request-id={ask.id}
              className="board-area-deck__rest-row arden-row"
              onClick={() => setActiveId(ask.id)}
            >
              <strong>{splitAreaAsk(ask.text).title}</strong>
              <small className="arden-attention-meta" data-columns="2">
                <span>{ASK_KIND[ask.kind].label}</span>
                <span className="arden-attention-meta__separator" aria-hidden>·</span>
                <time dateTime={ask.created_at}>{formatRelativePastAgo(ask.created_at)}</time>
              </small>
            </button>
          ))}
        </div>
      )}
    </section>
  );
}

function AreaRequestCard({
  ask,
  onDiscuss,
}: {
  ask: AreaAsk;
  onDiscuss: (ask: AreaAsk) => void;
}) {
  const automations = useStore((state) => state.automations);
  const pushToast = useStore((state) => state.pushToast);
  const [busy, setBusy] = useState(false);
  const { title, detail } = splitAreaAsk(ask.text);
  const primaryAction = ask.actions[0]?.verb === "open_page"
    ? null
    : primaryActionFor(ask, automations as Automation[] | null, {
        switchSession: (id) => void switchSession(id),
        runAutomation: (taskId) => void runAutomation(taskId),
        openArea: (key) => useStore.getState().openArea(key),
      });
  const reason = ask.why_now ?? detail ?? ask.what_next;
  const eyebrowKind = `${ASK_KIND[ask.kind].label.charAt(0).toUpperCase()}${ASK_KIND[ask.kind].label.slice(1)}`;

  const settle = async (state: "done" | "dismissed", resolution: string, failureTitle: string) => {
    if (busy) return;
    setBusy(true);
    try {
      await resolveAsk(ask.id, state, undefined, resolution);
    } catch {
      pushToast({
        id: `area-request-fail:${ask.id}`,
        title: failureTitle,
        status: "failed",
        target: { kind: "automation" },
      });
      // An area room only ever renders its own asks, so the key is present here.
      if (ask.area_key) void fetchAreaDetail(ask.area_key);
    } finally {
      setBusy(false);
    }
  };

  const primary = ask.kind === "review"
    ? {
        label: "Approve",
        run: () => void settle("done", "approved", "Couldn’t approve"),
      }
    : primaryAction
      ? primaryAction
      : ask.kind === "question"
        ? { label: "Review", run: () => onDiscuss(ask) }
        : { label: "Got it", run: () => void settle("done", "acknowledged", "Couldn’t clear this") };

  return (
    <article className="board-area-deck__card" aria-label={`${ASK_KIND[ask.kind].label}: ${title}`}>
      <p className="board-area-deck__eyebrow">
        <BlurSwap swapKey={`${ask.id}:kind`}>
          <span>{eyebrowKind}</span>
        </BlurSwap>
        <BlurSwap swapKey={`${ask.id}:age`} className="board-area-deck__age">
          <time dateTime={ask.created_at}>{formatRelativePastAgo(ask.created_at)}</time>
        </BlurSwap>
      </p>
      <h2>
        <BlurSwap swapKey={`${ask.id}:title`}>{title}</BlurSwap>
      </h2>
      {reason && (
        <p className="board-area-deck__reason">
          <BlurSwap swapKey={`${ask.id}:reason`}>{reason}</BlurSwap>
        </p>
      )}
      <div className="board-area-deck__actions">
        <Button variant="primary" size="md" disabled={busy} onClick={primary.run}>
          <span className="board-area-deck__verb-key" aria-hidden>1</span>
          <BlurSwap swapKey={`${ask.id}:primary`}>{primary.label}</BlurSwap>
        </Button>
        <Button
          variant="quiet"
          size="md"
          disabled={busy}
          onClick={() => void settle("dismissed", "set aside", "Couldn’t set this aside")}
        >
          <span className="board-area-deck__verb-key" aria-hidden>2</span>
          <BlurSwap swapKey={`${ask.id}:secondary`}>Later</BlurSwap>
        </Button>
      </div>
    </article>
  );
}
