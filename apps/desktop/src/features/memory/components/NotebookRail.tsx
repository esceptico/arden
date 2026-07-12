import { ChevronRight, Database, FileText, RefreshCw, Search } from "lucide-react";
import clsx from "clsx";
import { Empty } from "@/components/ui/EmptyState";
import { ListError, ListSkeleton } from "@/components/ui/ListColumn";
import { ScrollFadeBottom } from "@/components/ui/ScrollBlur";
import { GhostBtn } from "@/features/memory/components/shared";
import { FlatRow, TreeSearch } from "@/features/memory/components/MemoryFileTree";
import type { MemoryArtifactSummary } from "@/features/memory/lib/notebookTypes";

const ROOT_PAGES = ["me.md", "active-work.md", "directives.md", "lessons.md", "references.md"];
const SEMANTIC_DIRECTORIES = ["topics", "feeds", "insights", "daily"];
const HIDDEN_ROOT_FILES = new Set(["index.md", "README.md", "AGENTS.md", "health.md", "tooling.md"]);
const HIDDEN_DIRECTORIES = new Set(["raw", ".ntrp", ".index", ".maintenance", "changelog"]);
const HIDDEN_FILE_NAMES = new Set(["AGENTS.md", "health.md"]);

interface RailSection {
  key: string;
  title: string;
  description: string | null;
  artifacts: MemoryArtifactSummary[];
}

function parentDirectory(path: string): string {
  const parts = path.split("/");
  return parts.slice(0, -1).join("/");
}

function isDirectoryDescription(path: string): boolean {
  const name = path.split("/").at(-1);
  return name === "README.md" || name === "index.md";
}

export function isNotebookResource(artifact: MemoryArtifactSummary): boolean {
  if (HIDDEN_ROOT_FILES.has(artifact.path)) return false;
  const parts = artifact.path.split("/");
  if (parts.some((part) => HIDDEN_DIRECTORIES.has(part))) return false;
  return !HIDDEN_FILE_NAMES.has(parts.at(-1) ?? "");
}

export function isNotebookArtifact(artifact: MemoryArtifactSummary): boolean {
  return isNotebookResource(artifact) && !isDirectoryDescription(artifact.path);
}

function artifactDescription(artifact: MemoryArtifactSummary): string | null {
  return artifact.summary ?? artifact.snippet;
}

function humanizeDirectory(directory: string): string {
  return directory
    .split("/")
    .at(-1)!
    .replace(/[-_]+/g, " ")
    .replace(/^./, (letter) => letter.toUpperCase());
}

function sortArtifacts(items: MemoryArtifactSummary[]): MemoryArtifactSummary[] {
  return [...items].sort((a, b) => a.title.localeCompare(b.title) || a.path.localeCompare(b.path));
}

export function buildNotebookSections(artifacts: MemoryArtifactSummary[]): {
  sections: RailSection[];
  files: MemoryArtifactSummary[];
} {
  const resources = artifacts.filter(isNotebookResource);
  const visible = resources.filter(isNotebookArtifact);
  const consumed = new Set<string>();
  const sections: RailSection[] = [];

  const directoryDescription = (directory: string): MemoryArtifactSummary | undefined =>
    resources.find((artifact) => artifact.path === `${directory}/README.md`)
    ?? resources.find((artifact) => artifact.path === `${directory}/index.md`);

  const consumeDescriptions = (directory: string) => {
    consumed.add(`${directory}/README.md`);
    consumed.add(`${directory}/index.md`);
  };

  const roots = ROOT_PAGES.flatMap((path) => visible.filter((artifact) => artifact.path === path));
  if (roots.length) {
    roots.forEach((artifact) => consumed.add(artifact.path));
    sections.push({ key: "memory", title: "Memory", description: null, artifacts: roots });
  }

  for (const directory of SEMANTIC_DIRECTORIES) {
    const description = directoryDescription(directory);
    const entries = visible.filter(
      (artifact) => parentDirectory(artifact.path) === directory,
    );
    consumeDescriptions(directory);
    if (!entries.length) continue;
    entries.forEach((artifact) => consumed.add(artifact.path));
    sections.push({
      key: directory,
      title: humanizeDirectory(directory),
      description: description ? artifactDescription(description) : null,
      artifacts: directory === "daily" ? [...entries].sort((a, b) => b.path.localeCompare(a.path)) : sortArtifacts(entries),
    });
  }

  const descriptionDirectories = [...new Set(resources
    .filter((artifact) => isDirectoryDescription(artifact.path) && parentDirectory(artifact.path))
    .map((artifact) => parentDirectory(artifact.path)))]
    .filter((directory) => !SEMANTIC_DIRECTORIES.includes(directory));
  for (const directory of descriptionDirectories.sort((a, b) => {
    const depth = a.split("/").length - b.split("/").length;
    return depth || a.localeCompare(b);
  })) {
    const description = directoryDescription(directory);
    if (!description) continue;
    const entries = visible.filter(
      (artifact) => parentDirectory(artifact.path) === directory && !consumed.has(artifact.path),
    );
    consumeDescriptions(directory);
    if (entries.length === 0) continue;
    entries.forEach((artifact) => consumed.add(artifact.path));
    const genericTitle = description.title === "Index" || description.title === "README";
    sections.push({
      key: directory,
      title: genericTitle ? humanizeDirectory(directory) : description.title || humanizeDirectory(directory),
      description: artifactDescription(description),
      artifacts: sortArtifacts(entries),
    });
  }

  return {
    sections,
    files: sortArtifacts(visible.filter((artifact) => !consumed.has(artifact.path))),
  };
}

function NoteRow({
  artifact,
  selected,
  onSelect,
}: {
  artifact: MemoryArtifactSummary;
  selected: boolean;
  onSelect: (path: string) => void;
}) {
  const description = artifactDescription(artifact);
  return (
    <button
      type="button"
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

export function NotebookRail({
  artifacts,
  selectedPath,
  query,
  loading,
  error,
  rebuilding,
  recordsOpen,
  onQueryChange,
  onSelect,
  onRetry,
  onRebuild,
  onToggleRecords,
}: {
  artifacts: MemoryArtifactSummary[];
  selectedPath: string | null;
  query: string;
  loading: boolean;
  error: string | null;
  rebuilding: boolean;
  recordsOpen: boolean;
  onQueryChange: (value: string) => void;
  onSelect: (path: string) => void;
  onRetry: () => void;
  onRebuild: () => void;
  onToggleRecords: () => void;
}) {
  const visible = artifacts.filter(isNotebookArtifact);
  const needle = query.trim().toLowerCase();
  const matches = needle
    ? visible.filter((artifact) => [artifact.title, artifact.summary, artifact.snippet, artifact.path]
      .filter(Boolean)
      .join(" ")
      .toLowerCase()
      .includes(needle))
    : null;
  const { sections, files } = buildNotebookSections(artifacts);

  return (
    <>
      <TreeSearch value={query} onChange={onQueryChange} placeholder="Search memory notes…" />
      <div className="flex-1 min-h-0 overflow-y-auto scroll-thin px-3 py-3">
        <ScrollFadeBottom />
        {loading && artifacts.length === 0 ? (
          <ListSkeleton />
        ) : error ? (
          <ListError title="Couldn't load memory notes" message={error} onRetry={onRetry} />
        ) : matches ? (
          matches.length ? (
            <section aria-labelledby="memory-search-results">
              <h2 id="memory-search-results" className="px-2 pb-1 text-2xs font-semibold uppercase tracking-[0.08em] text-faint">
                Results
              </h2>
              <div className="flex flex-col gap-1">
                {sortArtifacts(matches).map((artifact) => (
                  <FlatRow key={artifact.path} a={artifact} active={selectedPath === artifact.path} onSelect={onSelect} />
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
        ) : visible.length === 0 ? (
          <Empty
            icon={FileText}
            hint="Memory pages appear here as the system learns."
            action={<GhostBtn onClick={onRebuild} disabled={rebuilding}>{rebuilding ? "Reloading…" : "Reload"}</GhostBtn>}
          >
            No memory notes yet
          </Empty>
        ) : (
          <div className="flex flex-col gap-5">
            {sections.map((section) => (
              <section key={section.key} aria-labelledby={`memory-section-${section.key.replace(/[^a-z0-9]+/gi, "-")}`}>
                <div className="px-2 pb-1.5">
                  <h2 id={`memory-section-${section.key.replace(/[^a-z0-9]+/gi, "-")}`} className="text-xs font-semibold text-ink-soft">
                    {section.title}
                  </h2>
                  {section.description && <p className="mt-0.5 text-2xs leading-[1.35] text-muted">{section.description}</p>}
                </div>
                <div className="flex flex-col gap-1">
                  {section.artifacts.map((artifact) => (
                    <NoteRow key={artifact.path} artifact={artifact} selected={selectedPath === artifact.path} onSelect={onSelect} />
                  ))}
                </div>
              </section>
            ))}
            {files.length > 0 && (
              <details data-memory-files className="group/files rounded-[10px] bg-surface-soft/35 px-2 py-1.5">
                <summary className="flex cursor-pointer list-none items-center gap-1.5 rounded px-1 py-1 text-xs font-medium text-muted hover:text-ink-soft">
                  <ChevronRight className="h-3 w-3 transition-transform duration-check group-open/files:rotate-90" />
                  Files
                  <span className="ml-auto tabular-nums text-faint">{files.length}</span>
                </summary>
                <div className="mt-1 flex flex-col gap-1 pb-1">
                  {files.map((artifact) => (
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
