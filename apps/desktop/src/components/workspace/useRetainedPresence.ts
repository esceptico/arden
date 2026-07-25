import { useLayoutEffect, useRef, useState } from "react";

/**
 * Keeps an overlay mounted for its visual exit, while allowing a close → open
 * reversal to reuse the same element and cancel the stale cleanup timer.
 */
export function useRetainedPresence(open: boolean, exitMs: number): boolean {
  const [present, setPresent] = useState(open);
  const openRef = useRef(open);
  const closeVersion = useRef(0);
  openRef.current = open;

  useLayoutEffect(() => {
    const version = ++closeVersion.current;
    if (open) {
      setPresent(true);
      return;
    }
    if (!present) return;

    const timer = window.setTimeout(() => {
      if (closeVersion.current === version && !openRef.current) {
        setPresent(false);
      }
    }, exitMs);
    return () => window.clearTimeout(timer);
  }, [exitMs, open, present]);

  return present;
}
