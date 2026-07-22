import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  ArrowUpDown,
  ChevronDown,
  FilePlus2,
  FileText,
  FolderPlus,
  Layers,
  NotebookText,
  Pin,
  Search,
} from "@/components/icons";
import clsx from "clsx";
import { Empty } from "@/components/ui/EmptyState";
import { ListError, ListSkeleton } from "@/components/ui/ListColumn";
import { GhostBtn } from "@/features/memory/components/shared";
import type { MemoryArtifactSummary } from "@/features/memory/lib/notebookTypes";
import {
  bucketOf,
  collectNotes,
  countNotes,
  findDir,
  parentDir,
  prettyDate,
  sortNotes,
  stem,
  type SortKey,
  type SortOrder,
  type WorkspaceDir,
} from "@/features/memory/lib/workspaceTree";

const COLLAPSED_KEY = "arden.desktop.memory.rail.collapsed";
const PINS_KEY = "arden.desktop.memory.pins";

function loadStringSet(key: string, fallback: string[]): Set<string> {
  try {
    const raw = localStorage.getItem(key);
    if (!raw) return new Set(fallback);
    const parsed: unknown = JSON.parse(raw);
    return new Set(Array.isArray(parsed) ? parsed.filter((v): v is string => typeof v === "string") : fallback);
  } catch {
    return new Set(fallback);
  }
}

function persistStringSet(key: string, value: Set<string>): void {
  try {
    localStorage.setItem(key, JSON.stringify([...value]));
  } catch {
    /* localStorage unavailable — non-fatal */
  }
}

interface RcMenuState {
  x: number;
  y: number;
  path: string;
}

interface CreateState {
  kind: "note" | "dir";
  value: string;
}

const SORT_LABELS: Record<SortKey, string> = {
  name: "Name",
  modified: "Time modified",
  created: "Time created",
};

export function NotebookRail({
  tree,
  allNotes,
  selectedPath,
  loading,
  error,
  rebuilding,
  navigationDisabled,
  nbMode,
  createdAt,
  onToggleNbMode,
  onSelect,
  onOpenInNewTab,
  onOpenSwitcher,
  onCreate,
  onRetry,
  onRebuild,
}: {
  tree: WorkspaceDir;
  allNotes: MemoryArtifactSummary[];
  selectedPath: string | null;
  loading: boolean;
  error: string | null;
  rebuilding: boolean;
  navigationDisabled: boolean;
  nbMode: boolean;
  createdAt: (artifact: MemoryArtifactSummary) => string;
  onToggleNbMode: () => void;
  onSelect: (path: string) => void;
  onOpenInNewTab: (path: string) => void;
  onOpenSwitcher: () => void;
  onCreate: (kind: "note" | "dir", path: string) => void;
  onRetry: () => void;
  onRebuild: () => void;
}) {
  const [collapsed, setCollapsed] = useState<Set<string>>(() => loadStringSet(COLLAPSED_KEY, []));
  const [pins, setPins] = useState<Set<string>>(() => loadStringSet(PINS_KEY, ["me.md"]));
  const [sortKey, setSortKey] = useState<SortKey>("name");
  const [sortAsc, setSortAsc] = useState(true);
  const [sortOverrides, setSortOverrides] = useState<Record<string, SortOrder>>({});
  const [sortMenuOpen, setSortMenuOpen] = useState(false);
  const [create, setCreate] = useState<CreateState | null>(null);
  const [rcMenu, setRcMenu] = useState<RcMenuState | null>(null);
  const [listDir, setListDir] = useState("");
  const [nbDescendants, setNbDescendants] = useState(true);
  const [pinsSectionClosed, setPinsSectionClosed] = useState(false);
  const createInputRef = useRef<HTMLInputElement>(null);
  const scrollerRef = useRef<HTMLDivElement>(null);
  const now = useMemo(() => new Date(), []);

  const notesByPath = useMemo(() => new Map(allNotes.map((artifact) => [artifact.path, artifact])), [allNotes]);
  const empty = tree.dirs.length === 0 && tree.files.length === 0;

  const activeSort = (scope: string | null): SortOrder =>
    (scope != null && sortOverrides[scope]) || { key: sortKey, asc: sortAsc };

  const toggleDir = (key: string) => {
    setCollapsed((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      persistStringSet(COLLAPSED_KEY, next);
      return next;
    });
  };

  const togglePin = (path: string) => {
    setPins((current) => {
      const next = new Set(current);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      persistStringSet(PINS_KEY, next);
      return next;
    });
  };

  useEffect(() => {
    if (!selectedPath) return;
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
  }, [selectedPath]);

  useEffect(() => {
    if (!sortMenuOpen && !rcMenu) return;
    const close = () => {
      setSortMenuOpen(false);
      setRcMenu(null);
    };
    window.addEventListener("pointerdown", close);
    return () => window.removeEventListener("pointerdown", close);
  }, [rcMenu, sortMenuOpen]);

  useEffect(() => {
    if (create) createInputRef.current?.focus();
  }, [create?.kind, create != null]);

  const openCreate = (kind: "note" | "dir") => {
    const dir = selectedPath?.includes("/") ? `${parentDir(selectedPath)}/` : "";
    setCreate({ kind, value: dir });
  };

  const submitCreate = () => {
    const value = create?.value.trim();
    if (!create || !value || value.endsWith("/")) return;
    onCreate(create.kind, create.kind === "note" && !value.endsWith(".md") ? `${value}.md` : value);
    setCreate(null);
  };

  const applySortKey = (key: SortKey) => {
    const scope = nbMode ? (listDir || "root") : null;
    const current = activeSort(scope);
    const next: SortOrder = { key, asc: current.key === key ? !current.asc : true };
    if (scope != null) setSortOverrides((overrides) => ({ ...overrides, [scope]: next }));
    else {
      setSortKey(next.key);
      setSortAsc(next.asc);
    }
  };

  const onRowContextMenu = (event: React.MouseEvent, path: string) => {
    event.preventDefault();
    setRcMenu({ x: event.clientX, y: event.clientY, path });
  };

  const pinButton = (path: string) => (
    <span
      role="button"
      tabIndex={-1}
      aria-label={pins.has(path) ? "Unpin" : "Pin"}
      title={pins.has(path) ? "Unpin" : "Pin"}
      className={clsx("mw-pin-btn", pins.has(path) && "on")}
      onClick={(event) => {
        event.stopPropagation();
        togglePin(path);
      }}
    >
      <Pin size={12} aria-hidden />
    </span>
  );

  const fileRow = (artifact: MemoryArtifactSummary, spacer: boolean) => (
    <button
      key={artifact.path}
      type="button"
      disabled={navigationDisabled}
      data-memory-entry={artifact.path}
      aria-current={selectedPath === artifact.path ? "page" : undefined}
      className={clsx("mw-tree-row", selectedPath === artifact.path && "active")}
      onClick={() => onSelect(artifact.path)}
      onContextMenu={(event) => onRowContextMenu(event, artifact.path)}
    >
      {spacer && <span className="mw-spacer" />}
      <span className="mw-label">{stem(artifact.path)}</span>
      {pinButton(artifact.path)}
    </button>
  );

  const folderNode = (dir: WorkspaceDir): React.ReactNode => (
    <div key={dir.path} className={clsx("mw-tree-folder", collapsed.has(dir.path) && "closed")} data-memory-directory={dir.path}>
      <div className="mw-tree-row folder">
        <button type="button" className="mw-chev-btn" aria-label={collapsed.has(dir.path) ? `Expand ${dir.name}` : `Collapse ${dir.name}`} onClick={() => toggleDir(dir.path)}>
          <ChevronDown className="mw-chev" size={12} aria-hidden />
        </button>
        <button type="button" className="mw-folder-label" onClick={() => toggleDir(dir.path)}>{dir.name}</button>
      </div>
      <div className="mw-tree-kids">
        {dir.dirs.map(folderNode)}
        {sortNotes(dir.files, activeSort(null), createdAt).map((artifact) => fileRow(artifact, true))}
      </div>
    </div>
  );

  const pinnedRows = [...pins].filter((path) => notesByPath.has(path));

  const treeView = (
    <div ref={scrollerRef} className="mw-tree" data-memory-tree>
      {pinnedRows.length > 0 && (
        <div className="mw-tree-pins">
          {pinnedRows.map((path) => fileRow(notesByPath.get(path)!, true))}
        </div>
      )}
      {tree.dirs.map(folderNode)}
      {sortNotes(tree.files, activeSort(null), createdAt).map((artifact) => fileRow(artifact, true))}
    </div>
  );

  const nbFolderNode = (dir: WorkspaceDir): React.ReactNode => (
    <div key={dir.path} className={clsx("mw-tree-folder", collapsed.has(dir.path) && "closed")}>
      <div className={clsx("mw-tree-row", "folder", listDir === dir.path && "active")}>
        <button type="button" className="mw-chev-btn" aria-label={collapsed.has(dir.path) ? `Expand ${dir.name}` : `Collapse ${dir.name}`} onClick={() => toggleDir(dir.path)}>
          <ChevronDown className="mw-chev" size={12} aria-hidden />
        </button>
        <button type="button" className="mw-folder-label" onClick={() => setListDir(dir.path)}>{dir.name}</button>
        <span className="mw-nb-count">
          {dir.files.length}
          {countNotes(dir) !== dir.files.length ? ` ▾ ${countNotes(dir)}` : ""}
        </span>
      </div>
      <div className="mw-tree-kids">{dir.dirs.map(nbFolderNode)}</div>
    </div>
  );

  const nbList = () => {
    const scope = listDir || "root";
    const override = sortOverrides[scope];
    const order: SortOrder = override ?? (sortKey === "name" ? { key: "modified", asc: false } : { key: sortKey, asc: sortAsc });
    const node = findDir(tree, listDir);
    const base = node
      ? nbDescendants
        ? listDir === ""
          ? allNotes.filter((artifact) => artifact.path !== "index.md")
          : collectNotes(node)
        : node.files
      : [];
    const field = (artifact: MemoryArtifactSummary) =>
      order.key === "created" ? createdAt(artifact) : artifact.updatedAt ?? "";
    const files = [...base].sort((a, b) => {
      const cmp = order.key === "name"
        ? stem(a.path).localeCompare(stem(b.path))
        : field(a).localeCompare(field(b)) || stem(a.path).localeCompare(stem(b.path));
      return order.asc ? cmp : -cmp;
    });
    const effectiveKey = order.key === "name" ? "modified" : order.key;
    const when = (artifact: MemoryArtifactSummary) =>
      effectiveKey === "created" ? createdAt(artifact) : artifact.updatedAt ?? "";
    const row = (artifact: MemoryArtifactSummary) => {
      const dir = parentDir(artifact.path);
      const showParent = dir && `${dir}/` !== listDir;
      const snippet = artifact.snippet?.trim()
        || (artifact.recordCount ? `${artifact.recordCount} records` : "No content yet.");
      return (
        <button
          key={artifact.path}
          type="button"
          disabled={navigationDisabled}
          data-memory-entry={artifact.path}
          className={clsx("mw-nnl-row", selectedPath === artifact.path && "active")}
          onClick={() => onSelect(artifact.path)}
          onContextMenu={(event) => onRowContextMenu(event, artifact.path)}
        >
          <span className="mw-nnl-title">{stem(artifact.path)}</span>
          <span className="mw-nnl-snippet">{snippet}</span>
          <span className="mw-nnl-when">
            {prettyDate(when(artifact))}
            {showParent ? <> · <span className="mw-nnl-parent">{dir}</span></> : null}
          </span>
        </button>
      );
    };
    const pinnedHere = files.filter((artifact) => pins.has(artifact.path));
    const rest = files.filter((artifact) => !pins.has(artifact.path));
    let lastBucket: string | null = null;
    return (
      <div className="mw-nb-list" data-memory-nb-list>
        <div className="mw-nnl-head">
          <h2>{listDir ? listDir.replace(/\/$/, "") : "Memory"}</h2>
          <span className="mw-nb-count">{files.length}</span>
          <button
            type="button"
            className={clsx("mw-icon-btn", nbDescendants && "on")}
            aria-pressed={nbDescendants}
            title="Show notes from subfolders"
            onClick={() => setNbDescendants((value) => !value)}
          >
            <Layers size={13.5} aria-hidden />
          </button>
        </div>
        {pinnedHere.length > 0 && (
          <>
            <h3 className="mw-nnl-bucket">Pinned</h3>
            {pinnedHere.map(row)}
          </>
        )}
        {rest.length > 0
          ? rest.map((artifact) => {
              const bucket = order.key === "name" ? null : bucketOf(when(artifact), now);
              const head = bucket && bucket !== lastBucket
                ? <h3 key={`bucket:${bucket}`} className="mw-nnl-bucket">{bucket}</h3>
                : null;
              if (bucket) lastBucket = bucket;
              return [head, row(artifact)];
            })
          : <p className="mw-ctx-empty" style={{ padding: 8 }}>Empty folder.</p>}
      </div>
    );
  };

  const nbView = (
    <div ref={scrollerRef} className="mw-tree as-nb" data-memory-tree>
      <div className="mw-nb-nav">
        <div className={clsx("mw-nb-section", pinsSectionClosed && "closed")}>
          <button type="button" className="mw-nb-section-head" onClick={() => setPinsSectionClosed((value) => !value)}>
            <Pin size={13} aria-hidden />
            Pinned
            <ChevronDown className="mw-chev" size={10} aria-hidden />
          </button>
          <div className="mw-nb-section-rows">
            {pinnedRows.length > 0
              ? pinnedRows.map((path) => fileRow(notesByPath.get(path)!, false))
              : <p className="mw-ctx-empty" style={{ padding: "2px 26px" }}>Nothing pinned.</p>}
          </div>
        </div>
        <div className="mw-nb-root">
          <div className={clsx("mw-tree-row", "folder", listDir === "" && "active")}>
            <span className="mw-spacer" style={{ width: 24 }} />
            <button type="button" className="mw-folder-label" onClick={() => setListDir("")}>Memory</button>
            <span className="mw-nb-count">{tree.files.length}</span>
          </div>
        </div>
        {tree.dirs.map(nbFolderNode)}
      </div>
      {nbList()}
    </div>
  );

  const sortScope = nbMode ? (listDir || "root") : null;
  const sortState = activeSort(sortScope);

  return (
    <>
      <div className="mw-rail-top">
        <button type="button" className="mw-rail-search" onClick={onOpenSwitcher} aria-label="Search notes">
          <Search size={13} aria-hidden />
          Search notes…
        </button>
        <div className="mw-rail-tools">
          <button type="button" className={clsx("mw-icon-btn", nbMode && "on")} aria-pressed={nbMode} title="Notebook view" onClick={onToggleNbMode}>
            <NotebookText size={13.5} aria-hidden />
          </button>
          <button type="button" className="mw-icon-btn" title="New note" onClick={() => openCreate("note")}>
            <FilePlus2 size={13.5} aria-hidden />
          </button>
          <button type="button" className="mw-icon-btn" title="New folder" onClick={() => openCreate("dir")}>
            <FolderPlus size={13.5} aria-hidden />
          </button>
          <button
            type="button"
            className="mw-icon-btn"
            title="Sort"
            aria-expanded={sortMenuOpen}
            onPointerDown={(event) => event.stopPropagation()}
            onClick={() => setSortMenuOpen((open) => !open)}
          >
            <ArrowUpDown size={13.5} aria-hidden />
          </button>
          {sortMenuOpen && (
            <div className="mw-sort-menu" role="menu" onPointerDown={(event) => event.stopPropagation()}>
              <div className="mw-sort-scope">
                {sortScope != null ? `This folder — ${sortScope === "root" ? "Memory" : sortScope}` : "All folders"}
              </div>
              {(Object.keys(SORT_LABELS) as SortKey[]).map((key) => (
                <button key={key} type="button" role="menuitem" className={clsx(sortState.key === key && "on")} onClick={() => applySortKey(key)}>
                  {SORT_LABELS[key]}
                  <span className="dir">{sortState.key === key ? (sortState.asc ? "↑" : "↓") : ""}</span>
                </button>
              ))}
            </div>
          )}
        </div>
      </div>
      {create && (
        <div className="mw-tree-create">
          <input
            ref={createInputRef}
            value={create.value}
            placeholder={create.kind === "note" ? "path/note-name" : "path/folder-name"}
            aria-label={create.kind === "note" ? "New note path" : "New folder path"}
            onChange={(event) => setCreate({ ...create, value: event.currentTarget.value })}
            onBlur={() => setCreate(null)}
            onKeyDown={(event) => {
              if (event.key === "Enter") submitCreate();
              else if (event.key === "Escape") setCreate(null);
            }}
          />
        </div>
      )}
      {loading && empty ? (
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
      ) : nbMode ? nbView : treeView}
      {rcMenu && createPortal(
        <div
          className="mw-rc-menu"
          role="menu"
          style={{ left: Math.min(rcMenu.x, window.innerWidth - 180), top: Math.min(rcMenu.y, window.innerHeight - 140) }}
          onPointerDown={(event) => event.stopPropagation()}
        >
          <div className="mw-rc-path">{rcMenu.path}</div>
          <button
            type="button"
            role="menuitem"
            onClick={() => {
              void navigator.clipboard?.writeText(rcMenu.path);
              setRcMenu(null);
            }}
          >
            Copy path
          </button>
          <button
            type="button"
            role="menuitem"
            onClick={() => {
              togglePin(rcMenu.path);
              setRcMenu(null);
            }}
          >
            {pins.has(rcMenu.path) ? "Unpin" : "Pin"}
          </button>
          <button
            type="button"
            role="menuitem"
            onClick={() => {
              onOpenInNewTab(rcMenu.path);
              setRcMenu(null);
            }}
          >
            Open in new tab
          </button>
        </div>,
        document.body,
      )}
    </>
  );
}
