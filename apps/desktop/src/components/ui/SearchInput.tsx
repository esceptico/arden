import type { Ref } from "react";
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
  /** Merged onto the wrapper div — callers control width (e.g. `flex-1`, `w-[200px]`). */
  className?: string;
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
  className,
}: SearchInputProps) {
  const Icon = busy ? Loader2 : Search;
  return (
    <div className={clsx("relative min-w-0", className)}>
      <Icon
        size={ICON.XS}
        strokeWidth={2}
        className={clsx(
          "absolute left-2.5 top-1/2 -translate-y-1/2 text-faint pointer-events-none",
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
        autoFocus={autoFocus}
        spellCheck={false}
        className="w-full h-7 pl-7 pr-7 rounded-[10px] bg-surface-soft focus:bg-surface-sunken border border-transparent focus:border-line text-sm text-ink-soft placeholder:text-muted outline-none transition-[background-color,border-color]"
      />
      {showClear && value && (
        <button
          type="button"
          onClick={() => onChange("")}
          aria-label="Clear search"
          className="absolute right-1.5 top-1/2 grid size-4 -translate-y-1/2 place-items-center rounded text-faint hover:bg-surface-soft hover:text-ink"
        >
          <X size={ICON.XS} strokeWidth={2} />
        </button>
      )}
    </div>
  );
}
