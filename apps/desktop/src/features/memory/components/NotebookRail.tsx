import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { CheckList, ChevronDown, FileText, Folder, Notebook01 } from "@/components/icons";
import clsx from "clsx";
import { AnchoredPopover } from "@/components/ui/AnchoredPopover";
import { Empty } from "@/components/ui/EmptyState";
import { ListError, ListSkeleton } from "@/components/ui/ListColumn";
import { ContextMenu, type ContextMenuPosition } from "@/components/ui/ContextMenu";
import { Tab, Tabs } from "@/components/ui/Tabs";
import { TabPanels, useTabDirection } from "@/components/ui/TabPanels";
import { copyText } from "@/lib/clipboard";
import { GhostBtn } from "@/features/memory/components/shared";
import type { MemoryItem } from "@/api/memoryItems";
import type { MemoryArtifactSummary } from "@/features/memory/lib/notebookTypes";
import { noteDateGroup, prettyDate, sortNotes, stem, type WorkspaceDir } from "@/features/memory/lib/workspaceTree";

const COLLAPSED_KEY = "arden.desktop.memory.rail.collapsed";
const NOTEBOOK_COLLAPSED_KEY = "arden.desktop.memory.notebook.collapsed";
const RAIL_MODES = ["files", "notebook", "facts"] as const;
/** Hover-intent before a fact opens its full text, and the grace that lets the
 *  pointer cross the gap into the panel — same pair as the wiki-link preview. */
const FACT_INTENT_MS = 300;
const FACT_HIDE_MS = 120;

export type MemoryRailMode = (typeof RAIL_MODES)[number];

interface FileContextMenuState extends ContextMenuPosition {
  path: string;
}

interface FactPreviewState {
  record: MemoryItem;
  element: HTMLElement;
}

function loadStringSet(key: string): Set<string> {
  try {
    const parsed: unknown = JSON.parse(localStorage.getItem(key) ?? "[]");
    return new Set(Array.isArray(parsed) ? parsed.filter((value): value is string => typeof value === "string") : []);
  } catch {
    return new Set();
  }
}

function persistStringSet(key: string, value: Set<string>): void {
  try {
    localStorage.setItem(key, JSON.stringify([...value]));
  } catch {
    /* localStorage unavailable — non-fatal */
  }
}

/** The rail row is three lines of prose in ~250px — the summary has to survive
 *  as plain text, so markdown emphasis, headings and wiki-link plumbing are
 *  stripped rather than rendered. */
function previewLine(artifact: MemoryArtifactSummary): string {
  return (artifact.snippet ?? artifact.summary ?? "")
    // Generated pages open with an `<!-- arden:index:start -->` marker — a
    // preview of the machinery, not of the note.
    .replace(/<!--[\s\S]*?(?:-->|$)/g, "")
    .replace(/\[\[[^\]|]+\|([^\]]+)\]\]/g, "$1")
    .replace(/\[\[([^\]]+)\]\]/g, "$1")
    .replace(/^\s*[-*+]\s+/gm, "")
    .replace(/[*_`>#]/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

export function NotebookRail({
  mode,
  tree,
  allNotes,
  records,
  selectedPath,
  loading,
  error,
  rebuilding,
  recordsLoading,
  recordsError,
  navigationDisabled,
  onModeChange,
  onSelect,
  onRetry,
  onRetryRecords,
  onRebuild,
}: {
  mode: MemoryRailMode;
  tree: WorkspaceDir;
  allNotes: MemoryArtifactSummary[];
  records: MemoryItem[];
  selectedPath: string | null;
  loading: boolean;
  error: string | null;
  rebuilding: boolean;
  recordsLoading: boolean;
  recordsError: string | null;
  navigationDisabled: boolean;
  onModeChange: (mode: MemoryRailMode) => void;
  onSelect: (path: string, direction: number) => void;
  onRetry: () => void;
  onRetryRecords: () => void;
  onRebuild: () => void;
}) {
  const [collapsed, setCollapsed] = useState<Set<string>>(() => loadStringSet(COLLAPSED_KEY));
  const [closedGroups, setClosedGroups] = useState<Set<string>>(() => loadStringSet(NOTEBOOK_COLLAPSED_KEY));
  const [fileContextMenu, setFileContextMenu] = useState<FileContextMenuState | null>(null);
  const [factPreview, setFactPreview] = useState<FactPreviewState | null>(null);
  const scrollerRef = useRef<HTMLDivElement>(null);
  const slotRef = useRef<HTMLDivElement>(null);
  const [segmentLabelsHidden, setSegmentLabelsHidden] = useState(false);
  const factIntentTimer = useRef<number | null>(null);
  const factHideTimer = useRef<number | null>(null);
  const direction = useTabDirection(RAIL_MODES, mode);
  const empty = tree.dirs.length === 0 && tree.files.length === 0;

  const clearFactTimers = useCallback(() => {
    if (factIntentTimer.current != null) window.clearTimeout(factIntentTimer.current);
    if (factHideTimer.current != null) window.clearTimeout(factHideTimer.current);
    factIntentTimer.current = null;
    factHideTimer.current = null;
  }, []);
  const closeFactPreview = useCallback(() => {
    clearFactTimers();
    setFactPreview(null);
  }, [clearFactTimers]);
  const showFactPreview = useCallback((record: MemoryItem, element: HTMLElement) => {
    clearFactTimers();
    setFactPreview({ record, element });
  }, [clearFactTimers]);
  const scheduleFactPreview = useCallback((record: MemoryItem, element: HTMLElement) => {
    clearFactTimers();
    factIntentTimer.current = window.setTimeout(() => setFactPreview({ record, element }), FACT_INTENT_MS);
  }, [clearFactTimers]);
  const scheduleFactHide = useCallback(() => {
    clearFactTimers();
    factHideTimer.current = window.setTimeout(() => setFactPreview(null), FACT_HIDE_MS);
  }, [clearFactTimers]);

  // A preview holds a record object and a live DOM node — both go stale when
  // the rail swaps mode or the ledger reloads underneath it.
  useEffect(() => {
    closeFactPreview();
    return closeFactPreview;
  }, [closeFactPreview, mode, records]);

  const factAnchor = useMemo(() => ({ current: factPreview?.element ?? null }), [factPreview?.element]);

  // The segment labels come and go on a container query (memory-rail ≥ 19rem),
  // so the rail is icon-only at narrow widths and the mode names are gone.
  // Read whether the label actually rendered rather than re-deriving the
  // breakpoint here — one threshold, living in the stylesheet.
  useLayoutEffect(() => {
    const root = slotRef.current;
    if (!root) return;
    const read = () => {
      const label = root.querySelector<HTMLElement>(".mw-rail-segment-label");
      setSegmentLabelsHidden(label ? getComputedStyle(label).display === "none" : false);
    };
    read();
    const frame = requestAnimationFrame(read);
    // The slot itself is `display: contents` — no box, so a ResizeObserver on
    // it never reports. Watch the query container that actually resizes.
    const container = root.closest<HTMLElement>(".mw-rail");
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(read);
    if (container) observer?.observe(container);
    return () => {
      cancelAnimationFrame(frame);
      observer?.disconnect();
    };
  }, []);

  useEffect(() => {
    if (!selectedPath || mode === "facts") return;
    if (mode === "files") {
      setCollapsed((current) => {
        const ancestors = selectedPath.split("/").slice(0, -1)
          .map((_, index, parts) => `${parts.slice(0, index + 1).join("/")}/`);
        if (!ancestors.some((ancestor) => current.has(ancestor))) return current;
        const next = new Set(current);
        for (const ancestor of ancestors) next.delete(ancestor);
        persistStringSet(COLLAPSED_KEY, next);
        return next;
      });
    }
    const frame = requestAnimationFrame(() => {
      scrollerRef.current
        ?.querySelector<HTMLElement>(`[data-memory-entry="${CSS.escape(selectedPath)}"]`)
        ?.scrollIntoView({ block: "nearest" });
    });
    return () => cancelAnimationFrame(frame);
  }, [mode, selectedPath]);

  const toggleDir = (path: string) => {
    setCollapsed((current) => {
      const next = new Set(current);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      persistStringSet(COLLAPSED_KEY, next);
      return next;
    });
  };

  const toggleGroup = (label: string) => {
    setClosedGroups((current) => {
      const next = new Set(current);
      if (next.has(label)) next.delete(label);
      else next.add(label);
      persistStringSet(NOTEBOOK_COLLAPSED_KEY, next);
      return next;
    });
  };

  const openFileContextMenu = useCallback((
    path: string,
    trigger: HTMLElement,
    source: ContextMenuPosition["source"],
    x: number,
    y: number,
  ) => {
    if (navigationDisabled) return;
    setFileContextMenu({ path, trigger, source, x, y });
  }, [navigationDisabled]);

  const selectPath = useCallback((path: string) => {
    const rows = Array.from(
      scrollerRef.current?.querySelectorAll<HTMLElement>("[data-memory-entry]") ?? [],
    );
    const currentIndex = rows.findIndex((row) => row.dataset.memoryEntry === selectedPath);
    const nextIndex = rows.findIndex((row) => row.dataset.memoryEntry === path);
    onSelect(path, currentIndex >= 0 && nextIndex < currentIndex ? -1 : 1);
  }, [onSelect, selectedPath]);

  const files = useMemo(() => {
    const fileRow = (artifact: MemoryArtifactSummary, depth = 1) => (
      <button
        key={artifact.path}
        type="button"
        disabled={navigationDisabled}
        data-memory-entry={artifact.path}
        aria-current={selectedPath === artifact.path ? "page" : undefined}
        className={clsx(
          "mw-tree-row",
          depth === 0 && "root",
          depth === 2 && "depth-2",
          selectedPath === artifact.path && "active",
        )}
        onClick={() => selectPath(artifact.path)}
        onContextMenu={(event) => {
          event.preventDefault();
          openFileContextMenu(artifact.path, event.currentTarget, "pointer", event.clientX, event.clientY);
        }}
        onKeyDown={(event) => {
          if (event.key !== "ContextMenu" && !(event.shiftKey && event.key === "F10")) return;
          event.preventDefault();
          const rect = event.currentTarget.getBoundingClientRect();
          openFileContextMenu(artifact.path, event.currentTarget, "keyboard", rect.left + 12, rect.bottom - 4);
        }}
      >
        <span className="mw-label">{stem(artifact.path)}</span>
      </button>
    );

    const folder = (dir: WorkspaceDir, depth = 0): ReactNode => (
      <div key={dir.path} className={clsx("mw-tree-folder", collapsed.has(dir.path) && "closed")} data-memory-directory={dir.path}>
        <button
          type="button"
          className={clsx("mw-fold", depth > 0 && "subfold")}
          aria-label={collapsed.has(dir.path) ? `Expand ${dir.name}` : `Collapse ${dir.name}`}
          aria-expanded={!collapsed.has(dir.path)}
          onClick={() => toggleDir(dir.path)}
        >
          <ChevronDown className="mw-chev" aria-hidden />
          <span className="mw-label">{dir.name}</span>
        </button>
        <div className={clsx("mw-tree-kids", `depth-${Math.min(depth + 1, 2)}`)}>
          {dir.dirs.map((child) => folder(child, depth + 1))}
          {sortNotes(dir.files, { key: "name", asc: true }, (artifact) => artifact.createdAt ?? artifact.updatedAt ?? "").map((artifact) => fileRow(artifact, Math.min(depth + 1, 2)))}
        </div>
      </div>
    );

    return (
      <div ref={scrollerRef} className="mw-tree scroll-fade" data-memory-tree>
        {tree.dirs.map((dir) => folder(dir))}
        {sortNotes(tree.files, { key: "name", asc: true }, (artifact) => artifact.createdAt ?? artifact.updatedAt ?? "").map((artifact) => fileRow(artifact, 0))}
      </div>
    );
  }, [collapsed, navigationDisabled, openFileContextMenu, selectPath, selectedPath, tree]);

  const notebookNotes = useMemo(() => [...allNotes].sort((left, right) => {
    const updated = (right.updatedAt ?? right.createdAt ?? "").localeCompare(left.updatedAt ?? left.createdAt ?? "");
    return updated || stem(left.path).localeCompare(stem(right.path));
  }), [allNotes]);

  /** Notes stay newest-first; the date buckets fall out of that order, so a
   *  group is just a run of adjacent notes sharing a label. */
  const notebookGroups = useMemo(() => {
    const now = new Date();
    const groups: { label: string; notes: MemoryArtifactSummary[] }[] = [];
    for (const artifact of notebookNotes) {
      const label = noteDateGroup(artifact.updatedAt ?? artifact.createdAt, now);
      const open = groups.at(-1);
      if (open?.label === label) open.notes.push(artifact);
      else groups.push({ label, notes: [artifact] });
    }
    return groups;
  }, [notebookNotes]);

  const notebook = useMemo(() => (
    <div ref={scrollerRef} className="mw-rail-list mw-notebook scroll-fade" data-memory-notebook-list>
      {notebookGroups.map((group) => (
        <section
          key={group.label}
          className={clsx("mw-notebook-group", closedGroups.has(group.label) && "closed")}
          data-memory-note-group={group.label}
        >
          <button
            type="button"
            className="mw-fold mw-notebook-fold"
            aria-expanded={!closedGroups.has(group.label)}
            aria-label={closedGroups.has(group.label) ? `Expand ${group.label}` : `Collapse ${group.label}`}
            onClick={() => toggleGroup(group.label)}
          >
            <ChevronDown className="mw-chev" aria-hidden />
            <span className="mw-label">{group.label}</span>
          </button>
          <div className="mw-notebook-kids">
            {group.notes.map((artifact) => {
              const preview = previewLine(artifact);
              return (
                <button
                  key={artifact.path}
                  type="button"
                  disabled={navigationDisabled}
                  data-memory-entry={artifact.path}
                  aria-current={selectedPath === artifact.path ? "page" : undefined}
                  className={clsx("mw-note-card", selectedPath === artifact.path && "active")}
                  onClick={() => selectPath(artifact.path)}
                  onContextMenu={(event) => {
                    event.preventDefault();
                    openFileContextMenu(artifact.path, event.currentTarget, "pointer", event.clientX, event.clientY);
                  }}
                  onKeyDown={(event) => {
                    if (event.key !== "ContextMenu" && !(event.shiftKey && event.key === "F10")) return;
                    event.preventDefault();
                    const rect = event.currentTarget.getBoundingClientRect();
                    openFileContextMenu(artifact.path, event.currentTarget, "keyboard", rect.left + 12, rect.bottom - 4);
                  }}
                >
                  <span className="mw-note-card-title">{stem(artifact.path)}</span>
                  {preview ? <span className="mw-note-card-preview">{preview}</span> : null}
                  <span className="mw-note-card-date">{prettyDate(artifact.updatedAt ?? artifact.createdAt)}</span>
                </button>
              );
            })}
          </div>
        </section>
      ))}
    </div>
  ), [closedGroups, navigationDisabled, notebookGroups, openFileContextMenu, selectPath, selectedPath]);

  const facts = useMemo(() => {
    if (recordsLoading && records.length === 0) return <div className="mw-tree"><ListSkeleton /></div>;
    if (recordsError) return <div className="mw-tree"><ListError title="Couldn't load memory facts" message={recordsError} onRetry={onRetryRecords} /></div>;
    if (records.length === 0) {
      return (
        <div className="mw-tree">
          <Empty icon={CheckList} hint="Facts appear here as Arden learns durable context.">No facts yet</Empty>
        </div>
      );
    }
    return (
      <div ref={scrollerRef} className="mw-rail-list scroll-fade" data-memory-facts-list>
        {records.map((record) => (
          <button
            key={record.id}
            type="button"
            disabled={navigationDisabled}
            data-memory-fact={record.id}
            className={clsx("mw-rail-list-row fact", factPreview?.record.id === record.id && "peeking")}
            onMouseEnter={(event) => scheduleFactPreview(record, event.currentTarget)}
            onMouseLeave={scheduleFactHide}
            onFocus={(event) => showFactPreview(record, event.currentTarget)}
            onBlur={scheduleFactHide}
          >
            <span className="mw-rail-row-meta">{record.updated_at.slice(0, 10)}</span>
            <span className="mw-rail-row-fact">{record.content}</span>
          </button>
        ))}
      </div>
    );
  }, [
    factPreview?.record.id,
    navigationDisabled,
    onRetryRecords,
    records,
    recordsError,
    recordsLoading,
    scheduleFactHide,
    scheduleFactPreview,
    showFactPreview,
  ]);

  const notesState = loading && empty ? (
    <div className="mw-tree"><ListSkeleton /></div>
  ) : error ? (
    <div className="mw-tree"><ListError title="Couldn't load memory notes" message={error} onRetry={onRetry} /></div>
  ) : empty ? (
    <div className="mw-tree">
      <Empty
        icon={FileText}
        hint="Memory pages appear here once the vault has notes."
        action={<GhostBtn onClick={onRebuild} disabled={rebuilding}>{rebuilding ? "Refreshing…" : "Refresh"}</GhostBtn>}
      >
        No memory notes yet
      </Empty>
    </div>
  ) : mode === "files" ? files : notebook;

  return (
    <div ref={slotRef} className="memory-notebook-rail-slot">
      <Tabs
        value={mode}
        onChange={(value) => onModeChange(value as MemoryRailMode)}
        variant="surface"
        label="Memory view"
        className="mw-rail-segments"
        indicatorClassName="mw-rail-segment-indicator"
      >
        {RAIL_MODES.map((nextMode) => {
          const icon = nextMode === "files" ? <Folder size={14} aria-hidden />
            : nextMode === "notebook" ? <Notebook01 size={14} aria-hidden />
              : <CheckList size={14} aria-hidden />;
          const label = nextMode[0]!.toUpperCase() + nextMode.slice(1);
          return (
            <Tab
              key={nextMode}
              value={nextMode}
              disabled={navigationDisabled}
              className={clsx(mode === nextMode && "active")}
              aria-label={label}
              title={segmentLabelsHidden ? label : undefined}
            >
              {icon}
              <span className="mw-rail-segment-label">
                {label}
              </span>
            </Tab>
          );
        })}
      </Tabs>
      {/* The house tab swap: `custom` only reaches an exiting child through
        * variant functions, so the panels have to come from TabPanels — an
        * inline `exit` object bakes in the PREVIOUS direction and sends the
        * outgoing mode the same way as the incoming one. */}
      <TabPanels value={mode} direction={direction} className="mw-rail-mode">
        {mode === "facts" ? facts : notesState}
      </TabPanels>
      <ContextMenu
        state={fileContextMenu}
        onClose={() => setFileContextMenu(null)}
        entries={fileContextMenu ? [
          { id: "open", label: "Open", onSelect: () => selectPath(fileContextMenu.path) },
          { id: "copy-path", label: "Copy path", onSelect: () => void copyText(fileContextMenu.path) },
        ] : []}
      />
      <AnchoredPopover
        open={factPreview != null}
        onClose={closeFactPreview}
        anchor={factAnchor}
        role="tooltip"
        className="mw-fact-peek"
        side="inline"
        closeOnScroll
      >
        {factPreview && (
          <div
            className="mw-fact-peek-body"
            data-memory-fact-peek={factPreview.record.id}
            onMouseEnter={clearFactTimers}
            onMouseLeave={scheduleFactHide}
          >
            <div className="mw-fact-peek-meta">
              <span>{prettyDate(factPreview.record.updated_at)}</span>
              <span>·</span>
              <span>{factPreview.record.kind}</span>
              {factPreview.record.pinned && <span>pinned</span>}
            </div>
            <p>{factPreview.record.content}</p>
            {factPreview.record.labels.length > 0 && (
              <div className="mw-fact-peek-labels">{factPreview.record.labels.join(" · ")}</div>
            )}
          </div>
        )}
      </AnchoredPopover>
    </div>
  );
}
