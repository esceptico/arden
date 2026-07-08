import { switchSession } from "@/actions/sessions";
import { formatRelativePast } from "@/lib/format";

interface SliceSessionRow {
  session_id: string;
  name: string;
  last_activity?: string;
}

/** ACTIVITY: the slice's sessions, click-through. Automation bookkeeping
 *  rows were dropped — the room header already carries the agent's last
 *  run, so repeating `slice:{key}` here was noise. Section renders only
 *  when there is actually something to show. */
export function SliceActivity({ sessions }: { sessions: SliceSessionRow[] }) {
  if (sessions.length === 0) return null;

  return (
    <div className="grid min-w-0 gap-2">
      <span className="text-2xs font-semibold tracking-wide text-faint uppercase">Activity</span>
      <div className="grid min-w-0 gap-px">
        {sessions.map((session) => (
          <button
            key={session.session_id}
            type="button"
            onClick={() => void switchSession(session.session_id)}
            className="app-row -mx-2 flex min-w-0 items-center gap-2 rounded-lg px-2 py-2 text-left text-sm text-ink-soft focus-visible:shadow-[0_0_0_2px_var(--color-accent-soft)] focus-visible:outline-none"
          >
            <span className="min-w-0 flex-1 truncate">{session.name || "Untitled session"}</span>
            {session.last_activity && (
              <span className="shrink-0 text-2xs text-whisper tabular-nums">
                {formatRelativePast(session.last_activity)} ago
              </span>
            )}
          </button>
        ))}
      </div>
    </div>
  );
}
