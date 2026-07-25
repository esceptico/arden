import type { ReactNode, Ref } from "react";
import { Loader2, Search, X } from "@/components/icons";
import clsx from "clsx";
import { ICON } from "@/lib/icons";

interface SearchInputProps {
  value: string;
  onChange: (v: string) => void;
  placeholder: string;
  /** Defaults to `placeholder`. */
  ariaLabel?: string;
  autoFocus?: boolean;
  /** Swaps the leading Search icon for a spinning Loader2. */
  busy?: boolean;
  /** Render the X clear button when there's a value. Default true. */
  showClear?: boolean;
  inputRef?: Ref<HTMLInputElement>;
  /** Optional muted label beside the clear control (for result counts, etc.). */
  trailing?: ReactNode;
  /** Merged onto the wrapper div — callers control width (e.g. `flex-1`, `w-[200px]`). */
  className?: string;
  /** Exact shared search role. `compact` is the 32px toolbar form; `command`
   * removes furniture for a command-sheet field. */
  size?: "default" | "compact" | "command";
}

export function SearchInput({
  value,
  onChange,
  placeholder,
  ariaLabel = placeholder,
  autoFocus = false,
  busy = false,
  showClear = true,
  inputRef,
  trailing,
  className,
  size = "default",
}: SearchInputProps) {
  const Icon = busy ? Loader2 : Search;
  const hasTrailing = trailing !== undefined && trailing !== null;
  return (
    <div className={clsx("arden-search-shell min-w-0", className)} data-size={size}>
      <Icon
        size={ICON.MD}
        className={clsx(
          "pointer-events-none",
          busy && "animate-spin",
        )}
      />
      <input
        ref={inputRef}
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        aria-label={ariaLabel}
        aria-busy={busy || undefined}
        autoFocus={autoFocus}
        spellCheck={false}
        className="arden-search-input"
      />
      {(hasTrailing || (showClear && value)) && (
        <span className="arden-search-trailing">
          {hasTrailing && <span>{trailing}</span>}
          {showClear && value && (
            <button
              type="button"
              onClick={() => onChange("")}
              aria-label="Clear search"
              className="arden-search-clear"
            >
              <X size={ICON.MD} />
            </button>
          )}
        </span>
      )}
    </div>
  );
}
