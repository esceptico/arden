import { useEffect, useRef } from "react";
import { useStore } from "@/stores";
import { maybeTriageChat } from "@/actions/triage";

/** Fires chat triage on the busy→idle edge of the current session: the moment
 *  a run finishes is when the first assistant reply has landed. All gating
 *  (unfiled, top-level, once-per-session) lives in maybeTriageChat. */
export function useChatTriage(): void {
  const sessionId = useStore((s) => s.currentSessionId);
  const isActive = useStore((s) =>
    s.currentSessionId ? s.activeRunSessionIds.has(s.currentSessionId) : false,
  );
  const prev = useRef<{ sessionId: string | null; active: boolean }>({
    sessionId: null,
    active: false,
  });

  useEffect(() => {
    const was = prev.current;
    prev.current = { sessionId, active: isActive };
    if (!sessionId) return;
    // Only the same-session busy → idle transition means "a run just finished."
    if (was.sessionId === sessionId && was.active && !isActive) {
      void maybeTriageChat(sessionId);
    }
  }, [sessionId, isActive]);
}
