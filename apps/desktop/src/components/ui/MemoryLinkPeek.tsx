import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import clsx from "clsx";
import { AnchoredPopover } from "@/components/ui/AnchoredPopover";
import { readMemoryArtifactDetail } from "@/api/memoryArtifacts";
import { getState } from "@/stores";
import { formatRelativePast } from "@/lib/format";
import { MOTION } from "@/lib/tokens/motion";

/** Hover intent — long enough that scanning prose never spawns cards. */
const SHOW_DELAY_MS = 300;
/** Same gap-crossing grace as the composer-toolbar hover popovers. */
const HIDE_DELAY_MS = MOTION.press * 1_000;
const CACHE_TTL_MS = 60_000;

type PeekData = { title: string; excerpt: string; updated: string | null };
type Phase = { kind: "loading" } | ({ kind: "ready" } & PeekData) | { kind: "error" };

const cache = new Map<string, { at: number; promise: Promise<PeekData> }>();

function loadPeek(path: string): Promise<PeekData> {
  const hit = cache.get(path);
  if (hit && Date.now() - hit.at < CACHE_TTL_MS) return hit.promise;
  const promise = readMemoryArtifactDetail(getState().config, path).then(({ artifact }) => ({
    title: artifact.title,
    excerpt: excerptOf(artifact.content),
    updated: artifact.updatedAt ?? null,
  }));
  // A failed read must not linger — the page may exist on the next hover.
  promise.catch(() => cache.delete(path));
  cache.set(path, { at: Date.now(), promise });
  return promise;
}

/** Plain-text first lines of a page. Deliberately not rendered Markdown: the
 *  card is a glance, and rendered links inside it would recurse into peeks. */
function excerptOf(markdown: string): string {
  const text = markdown
    .replace(/```[\s\S]*?(?:```|$)/g, " ")
    .replace(/<!--[\s\S]*?-->/g, " ")
    .replace(/!\[[^\]]*\]\([^)]*\)/g, " ")
    .replace(/\[\[([^\]|]+)\|([^\]]+)\]\]/g, "$2")
    .replace(/\[\[([^\]]+)\]\]/g, "$1")
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
    .replace(/^#{1,6}\s+/gm, "")
    .replace(/[*_`>|]/g, "")
    .replace(/\s+/g, " ")
    .trim();
  return text.length > 360 ? `${text.slice(0, 360).trimEnd()}…` : text;
}

/**
 * A memory-page link with a hover preview card — the chat-side affordance for
 * agent-cited wiki paths. Hovering fetches the page (cached, prefetched on
 * enter so the card usually opens ready); clicking hands over to Memory via
 * `onOpen`. A dangling path degrades visibly: the card says the page is
 * missing instead of a click silently landing on the vault's default page.
 *
 * The memory view's own hover preview (WikiLinkPreview) resolves aliases and
 * revisions against loaded vault state; this one is deliberately stateless —
 * exact paths only, fetched fresh — so chat needs no vault lifecycle.
 */
export function MemoryPeekLink({
  path,
  anchor,
  href,
  className,
  onOpen,
  children,
}: {
  path: string;
  anchor?: string | null;
  href?: string;
  className?: string;
  onOpen: () => void;
  children: ReactNode;
}) {
  const linkRef = useRef<HTMLAnchorElement>(null);
  const showTimer = useRef<number | null>(null);
  const hideTimer = useRef<number | null>(null);
  const generation = useRef(0);
  const tooltipId = useId();
  const [open, setOpen] = useState(false);
  const [phase, setPhase] = useState<Phase>({ kind: "loading" });

  const cancelHide = () => {
    if (hideTimer.current !== null) {
      window.clearTimeout(hideTimer.current);
      hideTimer.current = null;
    }
  };
  const cancelShow = () => {
    if (showTimer.current !== null) {
      window.clearTimeout(showTimer.current);
      showTimer.current = null;
    }
  };

  const show = () => {
    cancelHide();
    if (open || showTimer.current !== null) return;
    const gen = ++generation.current;
    setPhase({ kind: "loading" });
    loadPeek(path).then(
      (data) => generation.current === gen && setPhase({ kind: "ready", ...data }),
      () => generation.current === gen && setPhase({ kind: "error" }),
    );
    showTimer.current = window.setTimeout(() => {
      showTimer.current = null;
      setOpen(true);
    }, SHOW_DELAY_MS);
  };
  const scheduleHide = () => {
    cancelShow();
    cancelHide();
    hideTimer.current = window.setTimeout(() => setOpen(false), HIDE_DELAY_MS);
  };
  const close = () => {
    cancelShow();
    cancelHide();
    setOpen(false);
  };

  useEffect(() => () => {
    cancelShow();
    cancelHide();
  }, []);

  return (
    <>
      <a
        ref={linkRef}
        href={href ?? path}
        className={clsx("wikilink", className)}
        data-memory-path={path}
        data-memory-anchor={anchor ?? undefined}
        aria-describedby={open ? tooltipId : undefined}
        onClick={(event) => {
          event.preventDefault();
          close();
          onOpen();
        }}
        onMouseEnter={show}
        onMouseLeave={scheduleHide}
        onFocus={show}
        onBlur={scheduleHide}
      >
        {children}
      </a>
      <AnchoredPopover
        open={open}
        onClose={close}
        anchor={linkRef}
        id={tooltipId}
        role="tooltip"
        className="w-[320px]"
        closeOnScroll
      >
        <div className="memory-peek" onMouseEnter={cancelHide} onMouseLeave={scheduleHide}>
          <header className="memory-peek__head">
            <b>{phase.kind === "ready" ? phase.title : path}</b>
            <small>
              {path}
              {phase.kind === "ready" && phase.updated
                ? ` · edited ${formatRelativePast(phase.updated)} ago`
                : ""}
            </small>
          </header>
          {phase.kind === "loading" ? (
            <p className="memory-peek__state" role="status">Reading the page…</p>
          ) : phase.kind === "error" ? (
            <p className="memory-peek__state">No page at this path yet.</p>
          ) : phase.excerpt ? (
            <p className="memory-peek__excerpt">{phase.excerpt}</p>
          ) : (
            <p className="memory-peek__state">The page exists but is still empty.</p>
          )}
        </div>
      </AnchoredPopover>
    </>
  );
}
