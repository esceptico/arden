import { useEffect, useState } from "react";
import type { AreaDetail, AreaOutcome } from "@/api/areas";
import { useStore } from "@/stores";
import { fetchAreaDetail, replyToAsk } from "@/actions/areas";
import { AreaRequestDeck } from "@/features/areas/components/AreaRequestDeck";
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
  const checkedAt = detail.agent?.last_checked ?? detail.updated;
  return {
    running: `${count} ${noun} running`,
    checked: `checked ${formatRelativePast(checkedAt)} ago`,
  };
}

function currentOutcome(outcomes: readonly AreaOutcome[]): AreaOutcome | null {
  return outcomes.find((outcome) => outcome.status === "active" || outcome.status === "paused")
    ?? outcomes[0]
    ?? null;
}

/**
 * An Area is a room for the current decision and its one active outcome.
 * Broader activity belongs in the Area inspector, rather than competing with
 * the request deck in the room itself.
 */
export function AreaRoom({ areaKey }: { areaKey: string }) {
  const detail = useStore((s) => s.areas.detailByKey[areaKey]);
  const phase = useStore((s) => s.areas.detailPhaseByKey[areaKey] ?? "idle");
  const pushToast = useStore((s) => s.pushToast);
  const [replyingTo, setReplyingTo] = useState<string | null>(null);
  const [reply, setReply] = useState("");
  const [sending, setSending] = useState(false);

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
  const outcome = currentOutcome(detail.work.outcomes);
  const completedOutcomes = detail.work.outcomes.filter((item) => item.status === "completed").length;
  const presence = agentPresence(detail);

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
            action={<Button variant="secondary" size="sm" onClick={() => void fetchAreaDetail(areaKey)}>Retry</Button>}
            className="board-area-stale"
          >
            Showing the last successful Area snapshot.
          </Callout>
        )}

        <section className="board-area-needs" data-page-enter-item>
          <AreaRequestDeck asks={detail.asks} onDiscuss={discussAsk} />
        </section>

        <div className="board-area-room-scroll scroll-fade" tabIndex={0} aria-label="Area work and history" data-page-enter-item>
          <section className="board-area-current-outcome" aria-labelledby="area-current-outcome-title">
            <header>
              <h2>Current outcome</h2>
              <span>{completedOutcomes} of {detail.work.outcomes.length} complete</span>
            </header>
            {outcome ? (
              <>
                <h3 id="area-current-outcome-title">{outcome.title}</h3>
                <p>{outcome.success_criteria}</p>
              </>
            ) : (
              <p id="area-current-outcome-title">No outcome has been recorded yet.</p>
            )}
          </section>
        </div>

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
