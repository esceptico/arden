import { ChevronRight, FileText, Folder, FolderOpen, Search, X } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import clsx from "clsx";
import type { MemoryArtifactSummary } from "@/features/memory/lib/notebookTypes";
import { ICON } from "@/lib/icons";
import { EASE_EMPHASIZED, EASE_OUT, MOTION, RISE_IN, RISE_SETTLED } from "@/lib/tokens/motion";
import { displayFileName, displayTitle } from "@/features/memory/lib/format";
import { SYSTEM_FILES, type TreeNode } from "@/features/memory/lib/artifactTree";

/** Full-bleed search header with a leading icon — the file-tree variant of
 *  SearchInput's chrome, sized to the 52px list header. */
export function TreeSearch({ value, onChange, placeholder }: { value: string; onChange: (v: string) => void; placeholder: string }) {
  return (
    <div className="relative flex-none h-[52px] border-b border-line-soft">
      <Search
        size={ICON.XS}
        strokeWidth={2}
        className="absolute left-4 top-1/2 -translate-y-1/2 text-faint pointer-events-none"
      />
      <input
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        aria-label={placeholder}
        spellCheck={false}
        className="h-full w-full bg-transparent pl-10 pr-9 text-sm text-ink-soft placeholder:text-muted outline-none"
      />
      {value && (
        <button
          type="button"
          onClick={() => onChange("")}
          aria-label="Clear search"
          className="absolute right-2.5 top-1/2 grid size-5 -translate-y-1/2 place-items-center rounded text-faint hover:bg-surface-soft hover:text-ink"
        >
          <X size={ICON.XS} strokeWidth={2} />
        </button>
      )}
    </div>
  );
}

// ─── Tree rows ────────────────────────────────────────────────────────

export function TreeRow({
  node,
  depth,
  expanded,
  onToggle,
  selectedPath,
  onSelect,
  reduce,
  countFiles,
}: {
  node: TreeNode;
  depth: number;
  expanded: Set<string>;
  onToggle: (path: string) => void;
  selectedPath: string | null;
  onSelect: (path: string) => void;
  reduce: boolean;
  countFiles: (node: TreeNode) => number;
}) {
  const indent = depth * 14;

  if (node.kind === "directory") {
    const open = expanded.has(node.path);
    return (
      <>
        <button
          type="button"
          role="treeitem"
          aria-expanded={open}
          aria-level={depth + 1}
          onClick={() => onToggle(node.path)}
          title={node.name}
          className="group mt-1 flex h-8 min-w-0 items-center gap-1.5 rounded-[10px] pl-2 pr-3 text-left transition-colors hover:bg-surface-soft"
          style={{ marginLeft: indent }}
        >
          <ChevronRight
            className={clsx("h-3 w-3 shrink-0 text-faint transition-transform duration-trace", open && "rotate-90")}
            strokeWidth={2.25}
          />
          {open ? <FolderOpen className="h-3.5 w-3.5 shrink-0 text-faint" /> : <Folder className="h-3.5 w-3.5 shrink-0 text-faint" />}
          <span className="min-w-0 flex-1 truncate text-sm font-medium text-ink-soft group-hover:text-ink">{node.name}</span>
          <span className="shrink-0 text-2xs tabular-nums text-muted">{countFiles(node)}</span>
        </button>
        {/* Snap layout — no height-tween over the recursive subtree. The
            revealed block rises in as one unit (bounded on big folders);
            initial={false} keeps the all-expanded mount from animating. */}
        <AnimatePresence initial={false}>
          {open && (
            <motion.div
              key="children"
              initial={reduce ? false : RISE_IN}
              animate={RISE_SETTLED}
              exit={
                reduce
                  ? { opacity: 0, transition: { duration: 0 } }
                  : { opacity: 0, filter: "blur(3px)", transition: { duration: MOTION.fast, ease: EASE_OUT } }
              }
              transition={
                reduce
                  ? { duration: 0 }
                  : { duration: MOTION.panel, ease: EASE_EMPHASIZED }
              }
              className="flex flex-col gap-px"
            >
              {node.children.map((child) => (
                <TreeRow
                  key={child.kind === "directory" ? `d:${child.path}` : `f:${child.path}`}
                  node={child}
                  depth={depth + 1}
                  expanded={expanded}
                  onToggle={onToggle}
                  selectedPath={selectedPath}
                  onSelect={onSelect}
                  reduce={reduce}
                  countFiles={countFiles}
                />
              ))}
            </motion.div>
          )}
        </AnimatePresence>
      </>
    );
  }

  const a = node.artifact!;
  const active = selectedPath === a.path;
  // Generated system reports (index/health/AGENTS) sit at the tail of the root —
  // dimmed so the pages that matter carry the visual weight.
  const system = depth === 0 && SYSTEM_FILES.has(a.path);
  return (
    <button
      type="button"
      role="treeitem"
      aria-level={depth + 1}
      onClick={() => onSelect(a.path)}
      title={`${displayTitle(a)} — ${a.path}`}
      className="app-row group flex h-8 min-w-0 items-center gap-1.5 rounded-[10px] pl-2 pr-3 text-left"
      data-active={active}
      style={{ marginLeft: indent }}
    >
      <span className="w-3 shrink-0" aria-hidden />
      <FileText className={clsx("h-3.5 w-3.5 shrink-0", active ? "text-muted" : "text-faint")} />
      <span
        className={clsx(
          "min-w-0 flex-1 truncate text-sm",
          active ? "font-medium text-ink" : system ? "text-muted group-hover:text-ink" : "text-ink-soft group-hover:text-ink",
        )}
      >
        {displayFileName(a)}
      </span>
    </button>
  );
}

export function FlatRow({ a, active, onSelect }: { a: MemoryArtifactSummary; active: boolean; onSelect: (path: string) => void }) {
  const segments = a.path.split("/");
  const parent = segments.slice(0, -1).join(" › ");
  return (
    <button
      type="button"
      onClick={() => onSelect(a.path)}
      title={a.path}
      className="app-row group flex min-w-0 items-start gap-2 rounded-[10px] px-2.5 py-1.5 text-left"
      data-active={active}
    >
      <FileText className={clsx("mt-px h-3.5 w-3.5 shrink-0", active ? "text-muted" : "text-faint")} />
      <span className="min-w-0 flex-1">
        <span className={clsx("block truncate text-sm", active ? "font-medium text-ink" : "text-ink-soft group-hover:text-ink")}>
          {a.title}
        </span>
        {parent && <span className="block truncate text-2xs text-muted">{parent}</span>}
      </span>
    </button>
  );
}
