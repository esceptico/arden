import { Search, X } from "@/components/icons";
import { ICON } from "@/lib/icons";

/** Full-bleed search header with a leading icon — the file-tree variant of
 *  SearchInput's chrome, sized to the 52px list header. */
export function TreeSearch({ value, onChange, placeholder, quiet = false }: { value: string; onChange: (v: string) => void; placeholder: string; quiet?: boolean }) {
  return (
    <div className={quiet ? "relative flex-none h-[44px]" : "relative flex-none h-[52px] border-b border-line-soft"}>
      <Search
        size={ICON.XS}
        strokeWidth={2}
        className="absolute left-4 top-1/2 -translate-y-1/2 text-whisper pointer-events-none"
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
