import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { CheckList, ChevronDown, FileText, Folder, Notebook01 } from "@/components/icons";
import clsx from "clsx";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { Empty } from "@/components/ui/EmptyState";
import { ListError, ListSkeleton } from "@/components/ui/ListColumn";
import { ContextMenu, type ContextMenuPosition } from "@/components/ui/ContextMenu";
import { Tab, Tabs } from "@/components/ui/Tabs";
import { copyText } from "@/lib/clipboard";
import { GhostBtn } from "@/features/memory/components/shared";
import {
  CONTENT_ENTER_TRANSITION,
  CONTENT_EXIT_TRANSITION,
  CONTENT_SWAP_VARIANTS,
} from "@/lib/tokens/motion";
import type { MemoryItem } from "@/api/memoryItems";
import type { MemoryArtifactSummary } from "@/features/memory/lib/notebookTypes";
import { prettyDate, sortNotes, stem, type WorkspaceDir } from "@/features/memory/lib/workspaceTree";

const COLLAPSED_KEY = "arden.desktop.memory.rail.collapsed";
const RAIL_MODES = ["files", "notebook", "facts"] as const;

export type MemoryRailMode = (typeof RAIL_MODES)[number];

interface FileContextMenuState extends ContextMenuPosition {
  path: string;
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

function modeIndex(mode: MemoryRailMode): number {
  return RAIL_MODES.indexOf(mode);
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
  const reduce = useReducedMotion();
  const [collapsed, setCollapsed] = useState<Set<string>>(() => loadStringSet(COLLAPSED_KEY));
  const [fileContextMenu, setFileContextMenu] = useState<FileContextMenuState | null>(null);
  const scrollerRef = useRef<HTMLDivElement>(null);
  const previousModeRef = useRef(mode);
  const direction = modeIndex(mode) > modeIndex(previousModeRef.current) ? 1 : -1;
  const empty = tree.dirs.length === 0 && tree.files.length === 0;

  useEffect(() => {
    previousModeRef.current = mode;
  }, [mode]);

  useEffect(() => {
    if (!selectedPath || mode !== "files") return;
    setCollapsed((current) => {
      const ancestors = selectedPath.split("/").slice(0, -1)
        .map((_, index, parts) => `${parts.slice(0, index + 1).join("/")}/`);
      if (!ancestors.some((ancestor) => current.has(ancestor))) return current;
      const next = new Set(current);
      for (const ancestor of ancestors) next.delete(ancestor);
      persistStringSet(COLLAPSED_KEY, next);
      return next;
    });
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

  const notebook = useMemo(() => (
    <div ref={scrollerRef} className="mw-rail-list scroll-fade" data-memory-notebook-list>
      {notebookNotes.map((artifact) => (
        <button
          key={artifact.path}
          type="button"
          disabled={navigationDisabled}
          data-memory-entry={artifact.path}
          className={clsx("mw-rail-list-row", selectedPath === artifact.path && "active")}
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
          <span className="mw-rail-row-title">{stem(artifact.path)}</span>
          <span className="mw-rail-row-meta">{prettyDate(artifact.updatedAt ?? artifact.createdAt)}</span>
        </button>
      ))}
    </div>
  ), [navigationDisabled, notebookNotes, openFileContextMenu, selectPath, selectedPath]);

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
            className="mw-rail-list-row fact"
          >
            <span className="mw-rail-row-meta">{record.updated_at.slice(0, 10)}</span>
            <span className="mw-rail-row-fact">{record.content}</span>
          </button>
        ))}
      </div>
    );
  }, [navigationDisabled, onRetryRecords, records, recordsError, recordsLoading]);

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
    <div className="memory-notebook-rail-slot">
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
          return (
            <Tab
              key={nextMode}
              value={nextMode}
              disabled={navigationDisabled}
              className={clsx(mode === nextMode && "active")}
              title={nextMode[0]!.toUpperCase() + nextMode.slice(1)}
            >
              {icon}
              <span className="mw-rail-segment-label">
                {nextMode[0]!.toUpperCase() + nextMode.slice(1)}
              </span>
            </Tab>
          );
        })}
      </Tabs>
      <AnimatePresence initial={false} mode="wait" custom={direction}>
        <motion.div
          key={mode}
          custom={direction}
          className="mw-rail-mode"
          initial={reduce ? false : CONTENT_SWAP_VARIANTS.enter(direction)}
          animate={{
            ...CONTENT_SWAP_VARIANTS.center,
            transition: reduce ? { duration: 0 } : CONTENT_ENTER_TRANSITION,
            transitionEnd: { filter: "none" },
          }}
          exit={reduce ? undefined : {
            ...CONTENT_SWAP_VARIANTS.exit(direction),
            transition: CONTENT_EXIT_TRANSITION,
          }}
        >
          {mode === "facts" ? facts : notesState}
        </motion.div>
      </AnimatePresence>
      <ContextMenu
        state={fileContextMenu}
        onClose={() => setFileContextMenu(null)}
        entries={fileContextMenu ? [
          { id: "open", label: "Open", onSelect: () => selectPath(fileContextMenu.path) },
          { id: "copy-path", label: "Copy path", onSelect: () => void copyText(fileContextMenu.path) },
        ] : []}
      />
    </div>
  );
}
