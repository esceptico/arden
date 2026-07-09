import { BlurSwap } from "@/components/ui/BlurSwap";
import { StatusDot } from "@/components/ui/StatusDot";

type SessionIconState = "streaming" | "unread" | "idle";

/** Leading state glyph on each session row. Only rendered for
 *  states with something to indicate — streaming (animated dots in
 *  accent) and unread done (solid dot in accent-strong). Idle rows
 *  render an empty span that preserves the grid column width so the
 *  text alignment stays consistent across all rows. State changes
 *  crossfade through BlurSwap — the streaming→unread swap fires the
 *  moment a run completes, right when the user is watching the row.
 *
 *  No per-kind glyphs (channel/agent): the sidebar shows chats OR
 *  channels, never mixed, so a kind icon carries no information — and
 *  it landed on the group headers' glyph rail, making rows read as
 *  siblings of their own group. */
export function SessionStateIcon({
  streaming,
  unread,
}: {
  streaming: boolean;
  unread: boolean;
}) {
  const state: SessionIconState = streaming ? "streaming" : unread ? "unread" : "idle";

  return (
    <BlurSwap swapKey={state} blur={2}>
      {state === "streaming" ? (
        <span className="grid place-items-center w-4 h-4" aria-label="Running">
          <StatusDot status="running" pulse />
        </span>
      ) : state === "unread" ? (
        <span className="grid place-items-center w-4 h-4" aria-label="Unread">
          <span className="block w-[5px] h-[5px] rounded-full bg-accent-strong" />
        </span>
      ) : (
        <span className="block w-4 h-4" aria-hidden />
      )}
    </BlurSwap>
  );
}
