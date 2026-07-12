import { useCallback, useEffect, useId, useMemo, useRef, useState, type RefObject } from "react";
import { AnchoredPopover } from "@/components/ui/AnchoredPopover";
import { ArtifactCache } from "@/features/memory/lib/artifactCache";
import { resolveWikiTarget } from "@/features/memory/lib/wikiResolution";
import type { MemoryArtifactDetail, MemoryArtifactSummary, PageLinks } from "@/features/memory/lib/notebookTypes";

const INTENT_DELAY_MS = 300;
const HIDE_DELAY_MS = 120;

function previewParagraph(content: string) {
  const withoutFrontmatter = content.replace(/^---\r?\n[\s\S]*?\r?\n---\r?\n/, "");
  const blocks = withoutFrontmatter.split(/\r?\n\s*\r?\n/).map((block) => block.trim());
  return blocks.find((block) => block && !/^#{1,6}\s/.test(block))?.replace(/\s+/g, " ") ?? "No preview available.";
}

interface PreviewState {
  element: HTMLElement;
  path: string;
  anchor: string | null;
  detail: MemoryArtifactDetail | null;
  loading: boolean;
  error: string | null;
}

export function WikiLinkPreview({
  containerRef,
  links,
  summaries,
  cache,
  loadDetail,
}: {
  containerRef: RefObject<HTMLElement | null>;
  links: PageLinks | null;
  summaries: MemoryArtifactSummary[];
  cache: ArtifactCache;
  loadDetail: (path: string, signal: AbortSignal) => Promise<MemoryArtifactDetail>;
}) {
  const [preview, setPreview] = useState<PreviewState | null>(null);
  const intentTimer = useRef<number | null>(null);
  const hideTimer = useRef<number | null>(null);
  const request = useRef<AbortController | null>(null);
  const tooltipId = useId();
  const summariesByPath = useMemo(() => new Map(summaries.map((summary) => [summary.path, summary])), [summaries]);

  const cancelTimers = useCallback(() => {
    if (intentTimer.current != null) window.clearTimeout(intentTimer.current);
    if (hideTimer.current != null) window.clearTimeout(hideTimer.current);
    intentTimer.current = null;
    hideTimer.current = null;
  }, []);
  const close = useCallback(() => {
    cancelTimers();
    request.current?.abort();
    request.current = null;
    setPreview(null);
  }, [cancelTimers]);
  const cancelHide = useCallback(() => {
    if (hideTimer.current != null) window.clearTimeout(hideTimer.current);
    hideTimer.current = null;
  }, []);
  const scheduleHide = useCallback(() => {
    cancelHide();
    hideTimer.current = window.setTimeout(close, HIDE_DELAY_MS);
  }, [cancelHide, close]);

  useEffect(() => close(), [close, links]);

  useEffect(() => {
    const element = preview?.element;
    if (!element) return;
    element.setAttribute("aria-describedby", tooltipId);
    return () => {
      if (element.getAttribute("aria-describedby") === tooltipId) element.removeAttribute("aria-describedby");
    };
  }, [preview?.element, tooltipId]);

  useEffect(() => {
    const root = containerRef.current;
    if (!root) return;

    const cancelRequest = () => {
      cancelTimers();
      request.current?.abort();
      request.current = null;
    };
    const anchorFor = (target: EventTarget | null) =>
      target instanceof Element ? target.closest<HTMLElement>("[data-wikilink]") : null;
    const open = (element: HTMLElement) => {
      const target = element.dataset.wikilink;
      if (!target) return close();
      const resolved = resolveWikiTarget(links, target);
      if (!resolved) return close();
      cancelRequest();
      const revision = summariesByPath.get(resolved.path)?.revision;
      const cached = cache.get(resolved.path, revision);
      if (cached) {
        setPreview({ element, ...resolved, detail: cached, loading: false, error: null });
        return;
      }
      const controller = new AbortController();
      request.current = controller;
      setPreview({ element, ...resolved, detail: null, loading: true, error: null });
      void loadDetail(resolved.path, controller.signal).then((detail) => {
        if (controller.signal.aborted || request.current !== controller) return;
        cache.set(detail, revision ?? detail.revision);
        setPreview({ element, ...resolved, detail, loading: false, error: null });
      }).catch((reason) => {
        if (controller.signal.aborted || request.current !== controller) return;
        setPreview({ element, ...resolved, detail: null, loading: false, error: reason instanceof Error ? reason.message : String(reason) });
      });
    };
    const schedule = (element: HTMLElement) => {
      cancelRequest();
      setPreview(null);
      intentTimer.current = window.setTimeout(() => open(element), INTENT_DELAY_MS);
    };
    const onMouseOver = (event: MouseEvent) => {
      const element = anchorFor(event.target);
      if (element && !element.contains(event.relatedTarget as Node | null)) schedule(element);
    };
    const onMouseOut = (event: MouseEvent) => {
      const element = anchorFor(event.target);
      if (element && !element.contains(event.relatedTarget as Node | null)) scheduleHide();
    };
    const onFocusIn = (event: FocusEvent) => {
      const element = anchorFor(event.target);
      if (element) schedule(element);
    };
    const onFocusOut = (event: FocusEvent) => {
      const element = anchorFor(event.target);
      if (element && !element.contains(event.relatedTarget as Node | null)) scheduleHide();
    };
    root.addEventListener("mouseover", onMouseOver);
    root.addEventListener("mouseout", onMouseOut);
    root.addEventListener("focusin", onFocusIn);
    root.addEventListener("focusout", onFocusOut);
    return () => {
      root.removeEventListener("mouseover", onMouseOver);
      root.removeEventListener("mouseout", onMouseOut);
      root.removeEventListener("focusin", onFocusIn);
      root.removeEventListener("focusout", onFocusOut);
      cancelRequest();
    };
  }, [cache, cancelTimers, containerRef, links, loadDetail, scheduleHide, summariesByPath]);

  const anchor = useMemo<RefObject<HTMLElement | null>>(() => ({ current: preview?.element ?? null }), [preview?.element]);
  return (
    <AnchoredPopover
      open={preview != null}
      onClose={close}
      anchor={anchor}
      id={tooltipId}
      role="tooltip"
      className="w-[320px] p-3"
      closeOnScroll
    >
      {preview && (
        <div className="grid gap-1.5" onMouseEnter={cancelHide} onMouseLeave={scheduleHide}>
          <strong className="text-sm font-semibold text-ink">{preview.detail?.title ?? summariesByPath.get(preview.path)?.title ?? preview.path}</strong>
          {preview.loading ? <span className="text-xs text-muted">Loading preview…</span>
            : preview.error ? <span role="alert" className="text-xs text-danger">{preview.error}</span>
              : <p className="line-clamp-4 text-xs leading-relaxed text-muted">{previewParagraph(preview.detail?.content ?? "")}</p>}
        </div>
      )}
    </AnchoredPopover>
  );
}
