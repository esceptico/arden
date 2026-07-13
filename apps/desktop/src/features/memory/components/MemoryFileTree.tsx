import { FileText, Search, X } from "lucide-react";
import clsx from "clsx";
import type { MemoryArtifactSummary } from "@/features/memory/lib/notebookTypes";
import { ICON } from "@/lib/icons";

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

export function FlatRow({ a, active, disabled = false, onSelect }: { a: MemoryArtifactSummary; active: boolean; disabled?: boolean; onSelect: (path: string) => void }) {
  const segments = a.path.split("/");
  const parent = segments.slice(0, -1).join(" › ");
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={() => onSelect(a.path)}
      title={a.path}
      className="app-row group flex min-w-0 items-start gap-2 rounded-[10px] px-2.5 py-1.5 text-left disabled:opacity-50"
      data-active={active}
      data-memory-entry={a.path}
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
