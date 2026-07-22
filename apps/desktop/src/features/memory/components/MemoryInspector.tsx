import { useEffect, useMemo, useState, type ReactNode, type RefObject } from "react";
import { Clock, Link2, List } from "@/components/icons";
import { MemoryDiffOverlay, patchStat } from "@/features/memory/components/MemoryDiffOverlay";
import type {
  MemoryArtifactDetail,
  MemoryLink,
  PageEditEvent,
  PageEditHistory,
  PageLinks,
} from "@/features/memory/lib/notebookTypes";

type CtxPane = "links" | "outline" | "activity";

const PANE_STORAGE_KEY = "ntrp.desktop.memory.ctxPane";
const PANES: Array<{ key: CtxPane; label: string; icon: typeof Link2 }> = [
  { key: "links", label: "Links", icon: Link2 },
  { key: "outline", label: "Outline", icon: List },
  { key: "activity", label: "Activity", icon: Clock },
];

function storedPane(): CtxPane {
  const value = localStorage.getItem(PANE_STORAGE_KEY);
  return value === "links" || value === "outline" || value === "activity" ? value : "links";
}

function stem(path: string): string {
  return (path.split("/").pop() ?? path).replace(/\.md$/, "");
}

function parentDir(path: string): string {
  return path.includes("/") ? path.split("/").slice(0, -1).join("/") : "";
}

/** Wikilinks resolved to their display text, markdown markup stripped,
 *  collapsed to a single line — the excerpt form the draft renders. */
function cleanContext(context: string): string {
  return context
    .replace(/\[\[([^\]|]+)(?:\|([^\]]+))?\]\]/g, (_match, target: string, display?: string) => display ?? target)
    .replace(/[*_`]/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

/** First case-insensitive occurrence of `needle` wrapped in <mark>. */
function markFirst(text: string, needle: string): ReactNode {
  const trimmed = needle.trim();
  if (!trimmed) return text;
  const index = text.toLowerCase().indexOf(trimmed.toLowerCase());
  if (index < 0) return text;
  return (
    <>
      {text.slice(0, index)}
      <mark>{text.slice(index, index + trimmed.length)}</mark>
      {text.slice(index + trimmed.length)}
    </>
  );
}

type OutlineHeading = { text: string; level: number };

/** Headings h1–h4 from raw markdown, skipping fenced code blocks. */
function parseOutline(content: string): OutlineHeading[] {
  const headings: OutlineHeading[] = [];
  let fenced = false;
  for (const line of content.split("\n")) {
    if (/^\s*(```|~~~)/.test(line)) { fenced = !fenced; continue; }
    if (fenced) continue;
    const match = /^(#{1,4})\s+(.+?)\s*$/.exec(line);
    if (match) headings.push({ level: match[1].length, text: match[2].replace(/[*_`[\]]/g, "") });
  }
  return headings;
}

function normalizeHeading(text: string): string {
  return text.replace(/\s+/g, " ").trim().toLowerCase();
}

function noteScroller(container: HTMLElement | null): HTMLElement | null {
  return container?.querySelector<HTMLElement>("[data-memory-note-scroll]") ?? null;
}

function headingNodes(scroller: HTMLElement, headings: OutlineHeading[]): Array<HTMLElement | null> {
  const nodes = [...scroller.querySelectorAll<HTMLElement>("h1, h2, h3, h4")];
  return headings.map((heading) =>
    nodes.find((node) => normalizeHeading(node.textContent ?? "") === normalizeHeading(heading.text)) ?? null);
}

// Optional server field; typed locally until the /memory/links API lands it.
type UnlinkedMention = { sourcePath: string; context: string };
type PageLinksWithUnlinked = PageLinks & { unlinked?: UnlinkedMention[] };

const OUTLINE_LEVEL_CLASS: Record<number, string> = { 1: "", 2: " l2", 3: " l3", 4: " l4" };
const ACTIVITY_PREVIEW = 8;

export function MemoryInspector({
  page,
  links,
  history,
  linksLoading,
  historyLoading,
  linkError,
  historyError,
  navigationDisabled,
  titleForPath,
  onNavigate,
  onRetryLinks,
  scrollTargetRef,
}: {
  page: MemoryArtifactDetail;
  links: PageLinks | null;
  history: PageEditHistory | null;
  linksLoading: boolean;
  historyLoading: boolean;
  linkError: string | null;
  historyError: string | null;
  navigationDisabled: boolean;
  titleForPath: (path: string) => string | undefined;
  onNavigate: (path: string, anchor: string | null) => void;
  onRetryLinks: () => void;
  scrollTargetRef: RefObject<HTMLElement | null>;
}) {
  const [pane, setPane] = useState<CtxPane>(storedPane);
  const [diffEvent, setDiffEvent] = useState<PageEditEvent | null>(null);
  const [activityExpanded, setActivityExpanded] = useState(false);
  const [activeHeading, setActiveHeading] = useState(0);

  useEffect(() => {
    setDiffEvent(null);
    setActivityExpanded(false);
    setActiveHeading(0);
  }, [page.path]);

  const selectPane = (next: CtxPane) => {
    setPane(next);
    localStorage.setItem(PANE_STORAGE_KEY, next);
  };

  const outgoing = useMemo(() => {
    const seen = new Set<string>();
    const rows: MemoryLink[] = [];
    for (const link of links?.outgoing ?? []) {
      const key = link.resolvedPath ?? link.target;
      if (seen.has(key)) continue;
      seen.add(key);
      rows.push(link);
    }
    return rows;
  }, [links]);

  const backlinkGroups = useMemo(() => {
    const groups = new Map<string, MemoryLink[]>();
    for (const link of links?.backlinks ?? []) {
      const group = groups.get(link.sourcePath);
      if (group) group.push(link);
      else groups.set(link.sourcePath, [link]);
    }
    return [...groups];
  }, [links]);

  const unlinked = (links as PageLinksWithUnlinked | null)?.unlinked ?? [];

  const headings = useMemo(() => parseOutline(page.content), [page.content]);

  useEffect(() => {
    if (pane !== "outline") return;
    const scroller = noteScroller(scrollTargetRef.current);
    if (!scroller || headings.length === 0) return;
    const onScroll = () => {
      const nodes = headingNodes(scroller, headings);
      const top = scroller.getBoundingClientRect().top + 96;
      let current = 0;
      nodes.forEach((node, index) => {
        if (node && node.getBoundingClientRect().top <= top) current = index;
      });
      setActiveHeading(current);
    };
    onScroll();
    scroller.addEventListener("scroll", onScroll, { passive: true });
    return () => scroller.removeEventListener("scroll", onScroll);
  }, [pane, headings, scrollTargetRef, page.path]);

  const jumpToHeading = (index: number) => {
    const scroller = noteScroller(scrollTargetRef.current);
    if (!scroller) return;
    headingNodes(scroller, headings)[index]?.scrollIntoView({ block: "start" });
  };

  const events = useMemo(() => {
    const list = [...(history?.events ?? [])];
    list.sort((a, b) => b.sequence - a.sequence);
    return list;
  }, [history]);
  const visibleEvents = activityExpanded ? events : events.slice(0, ACTIVITY_PREVIEW);

  const pageStem = stem(page.path);

  return (
    <>
      <div className="mw-ctx-toolbar">
        {PANES.map(({ key, label, icon: Icon }) => (
          <button
            key={key}
            type="button"
            className={pane === key ? "mw-icon-btn on" : "mw-icon-btn"}
            aria-pressed={pane === key}
            aria-label={label}
            title={label}
            onClick={() => selectPane(key)}
          >
            <Icon size={15} />
          </button>
        ))}
      </div>

      {pane === "links" && (
        <div>
          <div className="mw-ctx-pane-head">
            <h2>Links</h2>
            <span className="mw-ctx-count">{outgoing.length}</span>
          </div>
          {linkError ? (
            <>
              <p role="alert" className="mw-ctx-empty">{linkError}</p>
              <button type="button" className="mw-recs-more" onClick={onRetryLinks}>Retry</button>
            </>
          ) : linksLoading && !links ? (
            <p className="mw-ctx-empty">Loading…</p>
          ) : (
            <>
              {outgoing.length === 0 && <p className="mw-ctx-empty">No links on this page.</p>}
              {outgoing.map((link) => {
                const path = link.resolvedPath;
                const title = link.display || (path ? titleForPath(path) ?? stem(path) : link.target);
                return (
                  <button
                    key={path ?? link.target}
                    type="button"
                    className="mw-lk-row"
                    disabled={navigationDisabled || !path}
                    onClick={() => path && onNavigate(path, link.heading)}
                  >
                    <span className="mw-lk-icon"><Link2 size={13} /></span>
                    <span className="mw-lk-body">
                      <span className="mw-lk-title">{title}</span>
                      <span className="mw-lk-sub">{path ? parentDir(path) : "unresolved"}</span>
                    </span>
                  </button>
                );
              })}

              <div className="mw-ctx-pane-head sub">
                <h2>Linked mentions</h2>
                <span className="mw-ctx-count">{links?.totalBacklinks ?? 0}</span>
              </div>
              {backlinkGroups.length === 0 && <p className="mw-ctx-empty">No backlinks yet.</p>}
              {backlinkGroups.map(([sourcePath, group]) => (
                <div key={sourcePath} className="mw-bl-group">
                  <button
                    type="button"
                    className="mw-bl-title"
                    disabled={navigationDisabled}
                    title={sourcePath}
                    onClick={() => onNavigate(sourcePath, null)}
                  >
                    {titleForPath(sourcePath) ?? stem(sourcePath)}
                  </button>
                  {group.slice(0, 2).map((link, index) => (
                    <p key={`${link.line}:${link.column}:${index}`} className="mw-bl-excerpt">
                      {markFirst(cleanContext(link.context), link.display)}
                    </p>
                  ))}
                </div>
              ))}

              <div className="mw-ctx-pane-head sub">
                <h2>Unlinked mentions</h2>
                <span className="mw-ctx-count">{unlinked.length}</span>
              </div>
              {unlinked.length === 0 && <p className="mw-ctx-empty">None found.</p>}
              {unlinked.map((mention, index) => (
                <div key={`${mention.sourcePath}:${index}`} className="mw-bl-group">
                  <button
                    type="button"
                    className="mw-bl-title"
                    disabled={navigationDisabled}
                    title={mention.sourcePath}
                    onClick={() => onNavigate(mention.sourcePath, null)}
                  >
                    {titleForPath(mention.sourcePath) ?? stem(mention.sourcePath)}
                  </button>
                  <p className="mw-bl-excerpt">{markFirst(cleanContext(mention.context), pageStem)}</p>
                </div>
              ))}
            </>
          )}
        </div>
      )}

      {pane === "outline" && (
        <div>
          <div className="mw-ctx-pane-head">
            <h2>Outline</h2>
            <span className="mw-ctx-count">{headings.length}</span>
          </div>
          {headings.length === 0 && <p className="mw-ctx-empty">No headings.</p>}
          {headings.map((heading, index) => (
            <button
              key={`${heading.text}:${index}`}
              type="button"
              className={`mw-outline-row${OUTLINE_LEVEL_CLASS[heading.level]}${index === activeHeading ? " active" : ""}`}
              onClick={() => jumpToHeading(index)}
            >
              {heading.text}
            </button>
          ))}
        </div>
      )}

      {pane === "activity" && (
        <div>
          <div className="mw-ctx-pane-head">
            <h2>Activity</h2>
            <span className="mw-ctx-count">{history?.total ?? 0}</span>
          </div>
          {historyError ? (
            <p role="alert" className="mw-ctx-empty">{historyError}</p>
          ) : historyLoading && !history ? (
            <p className="mw-ctx-empty">Loading…</p>
          ) : events.length === 0 ? (
            <p className="mw-ctx-empty">No edits recorded.</p>
          ) : (
            <>
              <div className="mw-recs">
                {visibleEvents.map((event, index) => {
                  const at = event.occurredAt.slice(0, 16).replace("T", " ");
                  return (
                    <div key={event.id} className={index === 0 ? "mw-rec accent" : "mw-rec"}>
                      <span className="dot" />
                      <button
                        type="button"
                        title={`${at} — show diff`}
                        onClick={() => setDiffEvent(event)}
                      >
                        <div className="when">
                          <span>{at.slice(5)}</span>
                          <span className="actor">{event.actor}</span>
                          <span className="stat">{patchStat(event.patch)}</span>
                        </div>
                      </button>
                    </div>
                  );
                })}
              </div>
              {events.length > ACTIVITY_PREVIEW && (
                <button
                  type="button"
                  className="mw-recs-more"
                  onClick={() => setActivityExpanded((expanded) => !expanded)}
                >
                  {activityExpanded ? "Show fewer" : `All ${history?.total ?? events.length} events ›`}
                </button>
              )}
            </>
          )}
        </div>
      )}

      {diffEvent && (
        <MemoryDiffOverlay event={diffEvent} path={page.path} onClose={() => setDiffEvent(null)} />
      )}
    </>
  );
}
