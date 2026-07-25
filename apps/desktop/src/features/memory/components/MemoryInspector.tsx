import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Database, X } from "@/components/icons";
import { BlurSwap } from "@/components/ui/BlurSwap";
import { Tab, Tabs } from "@/components/ui/Tabs";
import { TabPanels, useTabDirection } from "@/components/ui/TabPanels";
import { MemoryDiffOverlay, patchStat } from "@/features/memory/components/MemoryDiffOverlay";
import type {
  MemoryArtifactDetail,
  MemoryLink,
  PageEditEvent,
  PageEditHistory,
  PageLinks,
} from "@/features/memory/lib/notebookTypes";

/** The three persistent page instruments. Outline is deliberately retired: the
 * reading lane owns document navigation, while the peek owns page context. */
export type MemoryInspectorPane = "links" | "records" | "activity";

const PANE_STORAGE_KEY = "arden.desktop.memory.ctxPane";
const PANE_ORDER: readonly MemoryInspectorPane[] = ["links", "records", "activity"];
const LINK_DIRECTION_ORDER = ["outgoing", "incoming"] as const;
type LinkDirection = (typeof LINK_DIRECTION_ORDER)[number];
const ACTIVITY_PREVIEW = 8;

export function loadMemoryInspectorPane(): MemoryInspectorPane {
  try {
    const value = localStorage.getItem(PANE_STORAGE_KEY);
    return value === "links" || value === "records" || value === "activity" ? value : "links";
  } catch {
    return "links";
  }
}

export function persistMemoryInspectorPane(value: MemoryInspectorPane): void {
  try {
    localStorage.setItem(PANE_STORAGE_KEY, value);
  } catch {
    /* localStorage unavailable — non-fatal */
  }
}

function stem(path: string): string {
  return (path.split("/").pop() ?? path).replace(/\.md$/, "");
}

function parentDir(path: string): string {
  return path.includes("/") ? path.split("/").slice(0, -1).join("/") : "";
}

/** Wikilinks resolved to their display text, markdown markup stripped, and
 * collapsed to the one-line context shown in the incoming-links tab. */
function cleanContext(context: string): string {
  return context
    .replace(/\[\[([^\]|]+)(?:\|([^\]]+))?\]\]/g, (_match, target: string, display?: string) => display ?? target)
    .replace(/[*_`]/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

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
  onClose,
  onOpenDiff,
  activePane = "links",
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
  onClose: () => void;
  /** The workspace owns activity-diff lifetime so its blocking portal cannot
   * dismiss and unmount this nonblocking peek in the same interaction. */
  onOpenDiff?: (event: PageEditEvent, trigger: HTMLButtonElement) => void;
  activePane?: MemoryInspectorPane;
}) {
  const pane = activePane;
  const [linkDirection, setLinkDirection] = useState<LinkDirection>("outgoing");
  // Isolated inspector stories still render the overlay locally. Production
  // supplies `onOpenDiff`, keeping the modal above the peek lifecycle.
  const [localDiffEvent, setLocalDiffEvent] = useState<PageEditEvent | null>(null);
  const [localDiffOpen, setLocalDiffOpen] = useState(false);
  const [activityExpanded, setActivityExpanded] = useState(false);
  const paneDirection = useTabDirection(PANE_ORDER, pane);
  const linkDirectionMotion = useTabDirection(LINK_DIRECTION_ORDER, linkDirection);

  useEffect(() => {
    setLinkDirection("outgoing");
    setLocalDiffEvent(null);
    setLocalDiffOpen(false);
    setActivityExpanded(false);
  }, [page.path]);

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

  const events = useMemo(() => {
    const list = [...(history?.events ?? [])];
    list.sort((a, b) => b.sequence - a.sequence);
    return list;
  }, [history]);
  const visibleEvents = activityExpanded ? events : events.slice(0, ACTIVITY_PREVIEW);
  const records = page.timeline.filter((record) => !record.superseded);
  const openDiff = (event: PageEditEvent, trigger: HTMLButtonElement) => {
    if (onOpenDiff) {
      onOpenDiff(event, trigger);
      return;
    }
    setLocalDiffEvent(event);
    setLocalDiffOpen(true);
  };

  const linkBody = linkError ? (
    <>
      <p role="alert" className="mw-ctx-empty">{linkError}</p>
      <button type="button" className="mw-recs-more" onClick={onRetryLinks}>Retry</button>
    </>
  ) : linksLoading && !links ? (
    <p className="mw-ctx-empty">Loading…</p>
  ) : (
    <TabPanels value={linkDirection} direction={linkDirectionMotion} className="mw-ctx-link-content">
      {linkDirection === "outgoing" ? (
        <div>
          {outgoing.length === 0 && <p className="mw-ctx-empty">No outgoing links.</p>}
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
                <span className="mw-lk-body">
                  <span className="mw-lk-title">{title}</span>
                  <span className="mw-lk-sub">{path ? parentDir(path) || "pages" : "unresolved"}</span>
                </span>
              </button>
            );
          })}
        </div>
      ) : (
        <div>
          {backlinkGroups.length === 0 && <p className="mw-ctx-empty">No incoming links.</p>}
          {backlinkGroups.map(([sourcePath, group]) => (
            <div key={sourcePath} className="mw-bl-group">
              <button
                type="button"
                className="mw-bl-title"
                disabled={navigationDisabled}
                title={sourcePath}
                onClick={() => onNavigate(sourcePath, null)}
              >
                {titleForPath(sourcePath) ?? stem(sourcePath)} <span>· mention</span>
              </button>
              {group.slice(0, 2).map((link, index) => (
                <p key={`${link.line}:${link.column}:${index}`} className="mw-bl-excerpt">
                  {markFirst(cleanContext(link.context), link.display)}
                </p>
              ))}
            </div>
          ))}
        </div>
      )}
    </TabPanels>
  );

  return (
    <>
      <div className="mw-ctx-toolbar arden-peek-rule-below">
        <BlurSwap swapKey={pane} blur={1.4} className="mw-ctx-label">{pane}</BlurSwap>
        {pane === "links" && (
          <Tabs
            value={linkDirection}
            onChange={(value) => setLinkDirection(value as LinkDirection)}
            variant="plain"
            label="Link direction"
            className="mw-ctx-tabs"
          >
            <Tab
              value="outgoing"
              className={linkDirection === "outgoing" ? "on" : undefined}
            >
              outgoing <span className="mw-tab-count">{outgoing.length}</span>
            </Tab>
            <Tab
              value="incoming"
              className={linkDirection === "incoming" ? "on" : undefined}
            >
              incoming <span className="mw-tab-count">{links?.totalBacklinks ?? 0}</span>
            </Tab>
          </Tabs>
        )}
        <button type="button" className="mw-icon-btn mw-ctx-close" onClick={onClose} aria-label="Close peek" title="Close peek">
          <X size={15} aria-hidden />
        </button>
      </div>

      <TabPanels value={pane} direction={paneDirection} className="mw-ctx-content scroll-fade">
        {pane === "links" && linkBody}

        {pane === "records" && (
          <div>
            {records.length === 0 ? (
              <div className="mw-ctx-empty mw-page-records-empty">
                <Database size={16} aria-hidden />
                <span>No records on this page.</span>
              </div>
            ) : (
              <div className="mw-page-records">
                {records.map((record) => (
                  <article key={record.id} className="mw-page-record">
                    <div className="mw-page-record__meta">
                      <span>{record.date}</span>
                      <span>·</span>
                      <span>{record.kind}</span>
                      {record.pinned && <span>pinned</span>}
                    </div>
                    <p>{record.text}</p>
                  </article>
                ))}
              </div>
            )}
          </div>
        )}

        {pane === "activity" && (
          <div>
            {historyError ? (
              <p role="alert" className="mw-ctx-empty">{historyError}</p>
            ) : historyLoading && !history ? (
              <p className="mw-ctx-empty">Loading…</p>
            ) : events.length === 0 ? (
              <p className="mw-ctx-empty">No edits recorded.</p>
            ) : (
              <>
                <div className="mw-recs">
                  {visibleEvents.map((event) => {
                    const at = event.occurredAt.slice(0, 16).replace("T", " ");
                    return (
                      <div key={event.id} className="mw-rec">
                        <button type="button" title={`${at} — show diff`} onClick={(clickEvent) => openDiff(event, clickEvent.currentTarget)}>
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
                  <button type="button" className="mw-recs-more" onClick={() => setActivityExpanded((expanded) => !expanded)}>
                    {activityExpanded ? "Show fewer" : `All ${history?.total ?? events.length} events ›`}
                  </button>
                )}
              </>
            )}
          </div>
        )}
      </TabPanels>

      {!onOpenDiff && localDiffEvent && (
        <MemoryDiffOverlay
          event={localDiffEvent}
          path={page.path}
          open={localDiffOpen}
          onOpenChange={setLocalDiffOpen}
          onExitComplete={() => setLocalDiffEvent(null)}
        />
      )}
    </>
  );
}
