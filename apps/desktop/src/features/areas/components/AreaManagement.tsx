import type { AreaAgentStatus, AreaOutcome, AreaWorkItem } from "@/api/areas";
import { formatRelativeFuture } from "@/lib/format";

const WORK_STATE: Record<AreaWorkItem["status"], string> = {
  active: "Next",
  in_progress: "In progress",
  completed: "Done",
  cancelled: "Cancelled",
};

const WORK_OWNER: Record<AreaWorkItem["owner"], string> = {
  custodian: "Arden",
  user: "You",
  external: "External",
};

function displayLine(value: string | null, maxLength: number): string | null {
  const line = value?.split(/\r?\n/, 1)[0]?.trim();
  if (!line) return null;
  return line.length > maxLength ? `${line.slice(0, maxLength - 1)}…` : line;
}

function futureLabel(value: string | null): string | null {
  if (!value || !Number.isFinite(Date.parse(value))) return null;
  return formatRelativeFuture(value);
}

function nextCheck(
  agent: AreaAgentStatus | null,
  paused: boolean,
): { status: string; detail: string | null } {
  if (paused) return { status: "Paused", detail: "Resume to schedule the next check." };
  if (!agent) return { status: "Not scheduled", detail: "This Area has no custodian." };
  if (agent.running_since) return { status: "Checking now", detail: "A fresh report is in progress." };

  const scheduled = futureLabel(agent.next_check);
  if (agent.availability !== "ready") {
    return {
      status: scheduled ? `Retry ${scheduled}` : "Unavailable",
      detail: scheduled
        ? displayLine(agent.next_check_reason, 180)
        : "No retry is scheduled.",
    };
  }
  if (!scheduled) return { status: "Not scheduled", detail: null };
  return { status: scheduled, detail: displayLine(agent.next_check_reason, 180) };
}

function workSchedule(item: AreaWorkItem): { dateTime: string; label: string } | null {
  const due = futureLabel(item.due_at);
  if (due && item.due_at) {
    return { dateTime: item.due_at, label: due === "soon" ? "Due now" : `Due ${due}` };
  }

  const attempt = futureLabel(item.next_attempt_at);
  if (attempt && item.next_attempt_at) {
    return {
      dateTime: item.next_attempt_at,
      label: attempt === "soon" ? "Checking now" : `Check ${attempt}`,
    };
  }
  return null;
}

export function AreaManagement({
  agent,
  paused,
  outcomes,
  workItems,
}: {
  agent: AreaAgentStatus | null;
  paused: boolean;
  outcomes: readonly AreaOutcome[];
  workItems: readonly AreaWorkItem[];
}) {
  const check = nextCheck(agent, paused);
  const report = displayLine(agent?.last_report ?? null, 240);
  const error = agent?.availability === "error"
    ? displayLine(agent.last_error, 180) || "The custodian did not finish."
    : null;
  const liveWork = workItems.filter((item) => item.status === "active" || item.status === "in_progress");
  const outcomeById = new Map(outcomes.map((outcome) => [outcome.outcome_id, outcome]));

  return (
    <section className="board-area-management" aria-label="Area management">
      <div className="board-area-management__checks">
        <section aria-labelledby="area-latest-check-title">
          <h2 id="area-latest-check-title">Latest check</h2>
          <p>
            {error ? (
              <>
                <strong>Failed</strong>
                <span>{error}</span>
              </>
            ) : (
              report || "No report yet."
            )}
          </p>
        </section>
        <section aria-labelledby="area-next-check-title">
          <h2 id="area-next-check-title">Next check</h2>
          <p>
            <strong>{check.status}</strong>
            {check.detail ? <span>{check.detail}</span> : null}
          </p>
        </section>
      </div>

      <section className="board-area-work" aria-labelledby="area-work-title">
        <header>
          <h2 id="area-work-title">Current work</h2>
          {liveWork.length ? <span>{liveWork.length} open</span> : null}
        </header>
        {liveWork.length ? (
          <ol>
            {liveWork.map((item) => {
              const schedule = workSchedule(item);
              const state = item.kind === "blocker" ? "Blocked" : WORK_STATE[item.status];
              const outcome = item.outcome_id ? outcomeById.get(item.outcome_id) : null;
              return (
                <li key={item.item_id} data-state={state.toLowerCase().replace(" ", "-")}>
                  <div>
                    <h3>{item.text}</h3>
                    <p>
                      <span>{state}</span>
                      <span>{WORK_OWNER[item.owner]}</span>
                      <span>{displayLine(outcome?.title ?? null, 160) ?? "Unfiled"}</span>
                    </p>
                  </div>
                  {schedule ? <time dateTime={schedule.dateTime}>{schedule.label}</time> : null}
                </li>
              );
            })}
          </ol>
        ) : (
          <p className="board-area-work__zero">No open work.</p>
        )}
      </section>
    </section>
  );
}
