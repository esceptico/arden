import { useEffect, useRef, useState } from "react";

/** Smooths a bursty server stream into a steady cadence the eye reads as
 *  "thinking out loud" instead of "broken." The server emits text deltas
 *  whenever the model flushes — often 5 tokens in one frame, then nothing
 *  for 400ms. Rendering deltas directly inherits that rhythm.
 *
 *  This hook decouples DOM-rendered content from incoming content. While
 *  `isStreaming` is true, we tick toward the latest `target` at a fixed
 *  cadence (~36ms), advancing a few characters per tick and snapping to
 *  the nearest word boundary so cuts don't area through mid-word. When
 *  `isStreaming` flips false (turn completed), we flush instantly — no
 *  trailing partial state.
 *
 *  Adaptive chunk size: the further `rendered` falls behind `target`, the
 *  bigger each tick advances. Keeps the visible lag bounded — a long
 *  reply doesn't stretch into a noticeably-delayed render. */
// 50ms ≈ 20Hz. Faster ticks look smoother but pay the Markdown re-parse
// cost per render — and ReactMarkdown is not cheap for long content.
// 20Hz is well above the perceptual jitter threshold (~12Hz) and matches
// the cadence smoothStream-style libraries (Vercel AI SDK) converge on.
const TICK_INTERVAL_MS = 50;
const MIN_CHUNK_CHARS = 2;
const MAX_CHUNK_CHARS = 48;
const WORD_BOUNDARY_SEARCH_WINDOW = 24;

export function useSmoothStreamedContent(target: string, isStreaming: boolean): string {
  // Initial render: show whatever's already there. We only smooth deltas
  // that arrive after mount — replaying an already-loaded message would
  // be jarring on session switch / scrollback.
  const [rendered, setRendered] = useState(target);
  const renderedRef = useRef(rendered);
  const targetRef = useRef(target);
  targetRef.current = target;

  // Keep ref in sync with state so the rAF loop reads latest without
  // re-subscribing.
  useEffect(() => {
    renderedRef.current = rendered;
  }, [rendered]);

  useEffect(() => {
    // Not streaming: snap to target. Catches the "turn just ended" frame
    // where target may still be ahead of rendered by a few chars.
    if (!isStreaming) {
      if (renderedRef.current !== targetRef.current) {
        renderedRef.current = targetRef.current;
        setRendered(targetRef.current);
      }
      return;
    }

    let rafId: number;
    let lastEmitAt = performance.now();

    const tick = (now: number) => {
      const tgt = targetRef.current;
      const cur = renderedRef.current;

      // Defensive: target shrank (history reload, re-sync). Re-anchor.
      if (cur.length > tgt.length) {
        renderedRef.current = tgt;
        setRendered(tgt);
        rafId = requestAnimationFrame(tick);
        return;
      }

      // Already caught up — idle until target grows.
      if (cur.length === tgt.length) {
        rafId = requestAnimationFrame(tick);
        return;
      }

      // Throttle to TICK_INTERVAL_MS regardless of frame rate.
      if (now - lastEmitAt < TICK_INTERVAL_MS) {
        rafId = requestAnimationFrame(tick);
        return;
      }
      lastEmitAt = now;

      // Adaptive chunk: catch up faster when far behind. Divide remaining
      // by 4 keeps the eye-perceived lag short even on a 500-char burst.
      const remaining = tgt.length - cur.length;
      const baseChunk = Math.max(MIN_CHUNK_CHARS, Math.min(MAX_CHUNK_CHARS, Math.ceil(remaining / 4)));
      let nextEnd = cur.length + baseChunk;

      // Word-boundary snap: if the proposed cut lands mid-word, extend
      // forward to the next whitespace. Cap the extension so a very long
      // word can't blow the chunk size.
      if (nextEnd < tgt.length && !/\s/.test(tgt[nextEnd - 1])) {
        const window = tgt.slice(nextEnd, nextEnd + WORD_BOUNDARY_SEARCH_WINDOW);
        const wsOffset = window.search(/\s/);
        if (wsOffset >= 0) nextEnd = nextEnd + wsOffset + 1;
      }
      nextEnd = Math.min(tgt.length, nextEnd);

      const next = tgt.slice(0, nextEnd);
      renderedRef.current = next;
      setRendered(next);
      rafId = requestAnimationFrame(tick);
    };

    rafId = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(rafId);
  }, [isStreaming]);

  return rendered;
}
