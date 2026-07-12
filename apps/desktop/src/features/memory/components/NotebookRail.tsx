import { ChevronRight, Database, FileText, RefreshCw, Search } from "lucide-react";
import clsx from "clsx";
import type { RefObject } from "react";
import { Empty } from "@/components/ui/EmptyState";
import { ListError, ListSkeleton } from "@/components/ui/ListColumn";
import { ScrollFadeBottom } from "@/components/ui/ScrollBlur";
import { GhostBtn } from "@/features/memory/components/shared";
import { FlatRow, TreeSearch } from "@/features/memory/components/MemoryFileTree";
import type { MemoryArtifactSummary } from "@/features/memory/lib/notebookTypes";
import type { NotebookRailEntry, NotebookRailModel } from "@/features/memory/lib/notebookIndex";

function descriptionFor(artifact: MemoryArtifactSummary): string | null {
  return artifact.summary ?? artifact.snippet;
}

function NoteRow({
  artifact,
  description,
  selected,
  onSelect,
}: {
  artifact: MemoryArtifactSummary;
  description: string;
  selected: boolean;
  onSelect: (path: string) => void;
}) {
  return (
    <button
      type="button"
      data-memory-entry={artifact.path}
      onClick={() => onSelect(artifact.path)}
      className="app-row group w-full rounded-[10px] px-2.5 py-2 text-left"
      data-active={selected}
      aria-current={selected ? "page" : undefined}
    >
      <span className={clsx("block text-sm", selected ? "font-medium text-ink" : "text-ink-soft group-hover:text-ink")}>
        {artifact.title}
      </span>
      {description && <span className="mt-0.5 block line-clamp-2 text-xs leading-[1.35] text-muted">{description}</span>}
    </button>
  );
}

function RailEntry({
  entry,
  depth,
  selectedPath,
  onSelect,
}: {
  entry: NotebookRailEntry;
  depth: number;
  selectedPath: string | null;
  onSelect: (path: string) => void;
}) {
  if (entry.kind === "note") {
    return (
      <NoteRow
        artifact={entry.artifact}
        description={entry.description}
        selected={selectedPath === entry.path}
        onSelect={onSelect}
      />
    );
  }
  const id = `memory-directory-${entry.path.replace(/[^a-z0-9]+/gi, "-")}`;
  return (
    <section
      data-memory-entry={entry.path}
      data-memory-directory={entry.path}
      aria-labelledby={id}
      className={clsx(depth > 0 && "ml-2 border-l border-line-soft/60 pl-2")}
    >
      <div className="px-2 pb-1.5 pt-1">
        <h2 id={id} className="text-xs font-semibold text-ink-soft">{entry.title}</h2>
        <p className="mt-0.5 text-2xs leading-[1.35] text-muted">{entry.description}</p>
      </div>
      {entry.children.length > 0 && (
        <div className="flex flex-col gap-1">
          {entry.children.map((child) => (
            <RailEntry key={`${child.kind}:${child.path}`} entry={child} depth={depth + 1} selectedPath={selectedPath} onSelect={onSelect} />
          ))}
        </div>
      )}
    </section>
  );
}

export function NotebookRail({
  model,
  searchResults,
  selectedPath,
  query,
  loading,
  searchLoading,
  error,
  searchError,
  rebuilding,
  recordsOpen,
  recordsTriggerRef,
  onQueryChange,
  onSelect,
  onRetry,
  onRebuild,
  onToggleRecords,
}: {
  model: NotebookRailModel;
  searchResults: MemoryArtifactSummary[] | null;
  selectedPath: string | null;
  query: string;
  loading: boolean;
  searchLoading: boolean;
  error: string | null;
  searchError: string | null;
  rebuilding: boolean;
  recordsOpen: boolean;
  recordsTriggerRef: RefObject<HTMLButtonElement | null>;
  onQueryChange: (value: string) => void;
  onSelect: (path: string) => void;
  onRetry: () => void;
  onRebuild: () => void;
  onToggleRecords: () => void;
}) {
  const searchActive = query.trim().length > 0;
  const empty = model.entries.length === 0 && model.files.length === 0;

  return (
    <>
      <TreeSearch value={query} onChange={onQueryChange} placeholder="Search memory notes…" />
      <div className="flex-1 min-h-0 overflow-y-auto scroll-thin px-3 py-3">
        <ScrollFadeBottom />
        {searchActive ? (
          searchLoading && searchResults == null ? (
            <ListSkeleton />
          ) : searchError ? (
            <ListError title="Couldn't search memory notes" message={searchError} />
          ) : searchResults?.length ? (
            <section aria-labelledby="memory-search-results">
              <h2 id="memory-search-results" className="px-2 pb-1 text-2xs font-semibold uppercase tracking-[0.08em] text-faint">
                Results
              </h2>
              <div className="flex flex-col gap-1">
                {searchResults.map((artifact) => (
                  <NoteRow
                    key={artifact.path}
                    artifact={artifact}
                    description={descriptionFor(artifact) ?? ""}
                    selected={selectedPath === artifact.path}
                    onSelect={onSelect}
                  />
                ))}
              </div>
            </section>
          ) : (
            <Empty
              icon={Search}
              hint={<>No memory notes match “{query.trim()}”.</>}
              action={<GhostBtn onClick={() => onQueryChange("")}>Clear search</GhostBtn>}
            >
              No matches
            </Empty>
          )
        ) : loading && empty ? (
          <ListSkeleton />
        ) : error ? (
          <ListError title="Couldn't load memory notes" message={error} onRetry={onRetry} />
        ) : empty ? (
          <Empty
            icon={FileText}
            hint="Memory pages appear here after they are described by an index."
            action={<GhostBtn onClick={onRebuild} disabled={rebuilding}>{rebuilding ? "Reloading…" : "Reload"}</GhostBtn>}
          >
            No memory notes yet
          </Empty>
        ) : (
          <div className="flex flex-col gap-4">
            {model.entries.map((entry) => (
              <RailEntry key={`${entry.kind}:${entry.path}`} entry={entry} depth={0} selectedPath={selectedPath} onSelect={onSelect} />
            ))}
            {model.files.length > 0 && (
              <details data-memory-files className="group/files rounded-[10px] bg-surface-soft/35 px-2 py-1.5">
                <summary className="flex cursor-pointer list-none items-center gap-1.5 rounded px-1 py-1 text-xs font-medium text-muted hover:text-ink-soft">
                  <ChevronRight className="h-3 w-3 transition-transform duration-check group-open/files:rotate-90" />
                  Files
                  <span className="ml-auto tabular-nums text-faint">{model.files.length}</span>
                </summary>
                <div className="mt-1 flex flex-col gap-1 pb-1">
                  {model.files.map((artifact) => (
                    <FlatRow key={artifact.path} a={artifact} active={selectedPath === artifact.path} onSelect={onSelect} />
                  ))}
                </div>
              </details>
            )}
          </div>
        )}
      </div>
      <div className="flex items-center gap-1 border-t border-line-soft px-3 py-2">
        <button
          ref={recordsTriggerRef}
          type="button"
          aria-label="Open raw records diagnostic"
          aria-pressed={recordsOpen}
          onClick={onToggleRecords}
          className="flex h-7 items-center gap-1.5 rounded-[8px] px-2 text-xs text-muted hover:bg-surface-soft hover:text-ink-soft"
        >
          <Database className="h-3.5 w-3.5" />
          Raw records
        </button>
        <button
          type="button"
          onClick={onRebuild}
          disabled={rebuilding}
          aria-label="Reload memory notes"
          className="ml-auto grid size-7 place-items-center rounded-[8px] text-faint hover:bg-surface-soft hover:text-ink disabled:opacity-50"
        >
          <RefreshCw className={clsx("h-3.5 w-3.5", rebuilding && "animate-spin")} />
        </button>
      </div>
    </>
  );
}
