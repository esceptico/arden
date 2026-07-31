import { useEffect, useState } from "react";
import type { AreaDetail, AreaOutcome } from "@/api/areas";
import { useStore } from "@/stores";
import { fetchAreaDetail, replyToAsk, updateAreaSettings } from "@/actions/areas";
import { AreaRequestDeck } from "@/features/areas/components/AreaRequestDeck";
import { AreaPagePeek } from "@/features/areas/components/AreaPagePeek";
import { ATTENTION_CADENCE, AreaSettings } from "@/features/areas/components/AreaSettings";
import { BlurSwap } from "@/components/ui/BlurSwap";
import { Button } from "@/components/ui/Button";
import { Callout } from "@/components/ui/Callout";
import { formatRelativePast } from "@/lib/format";
import "@/design/area.css";

function runningAgentCount(detail: AreaDetail): number {
  const automationCount = detail.automations.filter((automation) => automation.running_since !== null).length;
  return Math.max(automationCount, detail.agent?.running_since ? 1 : 0);
}

function agentPresence(detail: AreaDetail): { running: string; checked: string } {
  const count = runningAgentCount(detail);
  const noun = count === 1 ? "agent" : "agents";
  // A custodian that has never run has no `last_checked`, and a page-less area
  // has no `updated` stamp — neither is a date to subtract from.
  const checkedAt = detail.agent?.last_checked || detail.updated;
  return {
    running: `${count} ${noun} running`,
    checked: checkedAt ? `checked ${formatRelativePast(checkedAt)} ago` : "never checked",
  };
}

/** Cancelled outcomes are not a tally the user needs; they are withdrawn. */
function outcomeTally(outcomes: readonly AreaOutcome[]): string | null {
  const open = outcomes.filter((o) => o.status === "active" || o.status === "paused").length;
  const done = outcomes.filter((o) => o.status === "completed").length;
  if (!open && !done) return null;
  return [open ? `${open} open` : null, done ? `${done} done` : null].filter(Boolean).join(" · ");
}

const OUTCOME_STATE: Record<AreaOutcome["status"], string> = {
  active: "In progress",
  paused: "Paused",
  completed: "Done",
  cancelled: "Cancelled",
};

/**
 * An Area is a room for the current decision, what the area is working toward,
 * and the contract its custodian runs under. The page preview is mounted here
 * rather than beside its trigger: the trigger lives inside the scrolling body,
 * whose scroll-fade mask clips every descendant it paints — fixed ones too.
 */
export function AreaRoom({ areaKey }: { areaKey: string }) {
  const detail = useStore((s) => s.areas.detailByKey[areaKey]);
  const phase = useStore((s) => s.areas.detailPhaseByKey[areaKey] ?? "idle");
  const pushToast = useStore((s) => s.pushToast);
  const [replyingTo, setReplyingTo] = useState<string | null>(null);
  const [reply, setReply] = useState("");
  const [sending, setSending] = useState(false);
  const [pageOpen, setPageOpen] = useState(false);
  const [pausing, setPausing] = useState(false);

  useEffect(() => {
    void fetchAreaDetail(areaKey);
  }, [areaKey]);

  if (!detail) {
    return (
      <div className="board-area board-area--loading">
        <div className="board-area-loading">
          {phase === "error" ? (
            <Callout
              tone="bad"
              title="Couldn’t load this Area"
              action={<Button size="sm" onClick={() => void fetchAreaDetail(areaKey)}>Retry</Button>}
            >
              Arden couldn’t read the current Area.
            </Callout>
          ) : (
            <p role="status">Loading Area…</p>
          )}
        </div>
      </div>
    );
  }

  const stale = phase === "error";
  const outcomes = detail.work.outcomes.filter((o) => o.status !== "cancelled");
  const tally = outcomeTally(detail.work.outcomes);
  const presence = agentPresence(detail);

  const pauseLabel = pausing ? "Updating" : detail.paused ? "Resume" : "Pause";

  const togglePause = async () => {
    if (pausing) return;
    setPausing(true);
    try {
      await updateAreaSettings(areaKey, { paused: !detail.paused });
    } catch {
      pushToast({
        id: `area-pause-fail:${areaKey}:${Date.now()}`,
        title: detail.paused ? "Couldn’t resume the custodian" : "Couldn’t pause the custodian",
        status: "failed",
        target: { kind: "automation", taskId: `area:${areaKey}` },
      });
    } finally {
      setPausing(false);
    }
  };

  const discussAsk = (ask: { id: string }) => {
    setReplyingTo(ask.id);
    setReply("");
    requestAnimationFrame(() => document.getElementById("area-reply-input")?.focus());
  };

  const sendReply = async () => {
    const text = reply.trim();
    const askId = replyingTo;
    if (!text || !askId || sending) return;
    setSending(true);
    try {
      await replyToAsk(askId, text);
      setReply("");
      setReplyingTo(null);
    } catch {
      pushToast({
        id: `area-reply-fail:${areaKey}:${askId}`,
        title: "Couldn’t send the reply",
        status: "failed",
        target: { kind: "automation" },
      });
    } finally {
      setSending(false);
    }
  };

  return (
    <section className="board-area" aria-label={`${detail.title} Area`}>
      <div className="board-area-page">
        <header className="board-area-header" data-page-enter-item>
          <h1>{detail.title}</h1>
          <p><b>{presence.running}</b> · {presence.checked}</p>
        </header>

        {stale && (
          <Callout
            tone="warn"
            title="Area data is stale"
            action={<Button size="sm" onClick={() => void fetchAreaDetail(areaKey)}>Retry</Button>}
            className="board-area-stale"
          >
            Showing the last successful Area snapshot.
          </Callout>
        )}

        <section className="board-area-needs" data-page-enter-item>
          <AreaRequestDeck asks={detail.asks} onDiscuss={discussAsk} />
        </section>

        <div className="board-area-room-scroll scroll-fade" tabIndex={0} aria-label="Area work and history" data-page-enter-item>
          <section className="board-area-outcomes" aria-labelledby="area-outcomes-title">
            <header>
              <h2 id="area-outcomes-title">What this area is working toward</h2>
              {tally ? <span>{tally}</span> : null}
            </header>
            {outcomes.length ? (
              <ol>
                {outcomes.map((item) => (
                  <li key={item.outcome_id} data-status={item.status}>
                    <h3>{item.title}</h3>
                    <p>{item.success_criteria}</p>
                    <span>{OUTCOME_STATE[item.status]}</span>
                  </li>
                ))}
              </ol>
            ) : (
              <p className="board-area-outcomes__zero">
                Nothing yet. The custodian proposes one once it has read enough to know what
                finished looks like.
              </p>
            )}
          </section>

          <AreaSettings
            area={detail}
            pageOpen={pageOpen}
            onTogglePage={() => setPageOpen((value) => !value)}
          />
        </div>

        {detail.autonomy ? (
          <footer className="board-area-footer">
            <span className="board-area-footer__state" aria-live="polite">
              <BlurSwap swapKey={detail.paused ? "paused" : "running"}>
                {detail.paused
                  ? "Paused. Nothing runs until you resume — the area and its page are kept."
                  : `Running. ${ATTENTION_CADENCE[detail.attention ?? "ambient"]}`}
              </BlurSwap>
            </span>
            <Button
              size="md"
              className="board-area-footer__pause"
              disabled={pausing}
              onClick={() => void togglePause()}
            >
              <BlurSwap swapKey={pauseLabel}>{pauseLabel}</BlurSwap>
            </Button>
          </footer>
        ) : null}

        {detail.page_path ? (
          <AreaPagePeek
            path={detail.page_path}
            open={pageOpen}
            onClose={() => setPageOpen(false)}
          />
        ) : null}

        {replyingTo && (
          <form
            className="board-area-reply"
            onSubmit={(event) => {
              event.preventDefault();
              void sendReply();
            }}
          >
            <div className="board-area-reply__context">
              Replying to this request
              <button type="button" onClick={() => setReplyingTo(null)}>Cancel</button>
            </div>
            <div className="board-area-reply__field">
              <input
                id="area-reply-input"
                className="arden-field"
                value={reply}
                onChange={(event) => setReply(event.target.value)}
                placeholder="Your answer"
                aria-label="Reply to this request"
              />
              <Button type="submit" variant="primary" size="md" disabled={sending || !reply.trim()}>
                Send
              </Button>
            </div>
          </form>
        )}
      </div>
    </section>
  );
}
